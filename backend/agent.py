"""The agentic chat loop — the WHEN of tool use and the streaming protocol.

`run_chat_stream` drives one /api/chat request: it streams each assistant turn as
NDJSON events, and when a turn ends by calling a tool it runs the tool, feeds the
result back, and continues. The Anthropic `client` is INJECTED (passed in by
main.py) rather than imported here, so this module never depends on main — which
keeps imports acyclic and lets tests swap in a fake client.

NDJSON event types this yields (one JSON object per line):
    {"type": "text",   "text": "..."}                     a chunk of answer text
    {"type": "search", "query": "...", "max_results": n}   a PubMed search ran
    {"type": "deep_research", "papers": ["..."]}           a deep-read began
    {"type": "deep_research_paper", "pmid": "...", ...}    one paper finished
    {"type": "notice", "text": "..."}                      e.g. search limit reached
    {"type": "turn_end"}                                   close this turn's bubble
    {"type": "error",  "text": "..."}                      fatal error mid-stream
"""
import json
import logging
from datetime import date

import deep_research
from config import settings
from prompts import build_system_prompt
from tools import PUBMED_TOOL, run_tool

logger = logging.getLogger("healthchecker.agent")


# Prefix every line with the request's short id so one conversation can be
# followed even when requests interleave. We prefix in-message (rather than via
# the format string) so it works for any logger without extra config.
class _RidAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        return f"[{self.extra['rid']}] {msg}", kwargs


def logger_for(request_id):
    """A logger that tags every line with this request's id."""
    return _RidAdapter(logger, {"rid": request_id})


def _event(**payload):
    """Serialize one NDJSON event line (a JSON object terminated by a newline)."""
    return json.dumps(payload) + "\n"


def run_chat_stream(messages, client, request_id, log):
    """Generator yielding NDJSON lines for one chat request.

    `messages` is the conversation as a list of {role, content} dicts; `client` is
    the (injected) Anthropic client; `request_id`/`log` tag this request's output.

    Agentic loop: stream each assistant turn as NDJSON events. If a turn ends by
    calling a tool, run it, feed the result back, and continue. The typical flow
    is: turn 1 calls search_pubmed (little/no text), we fetch abstracts, turn 2
    streams the grounded answer. Each turn is its own bubble on the frontend,
    split by `turn_end`.
    """
    # Give the model today's actual date so recency filters use the real year.
    system_prompt = build_system_prompt(date.today())

    calls_used = 0
    turn = 0
    try:
        while True:
            turn += 1
            # Once the search budget is spent, forbid further tool calls so the
            # model must answer from what it has (instead of searching forever).
            budget_spent = calls_used >= settings.max_tool_calls
            tool_choice = {"type": "none"} if budget_spent else {"type": "auto"}
            log.info("TURN %d start tool_choice=%s (searches_used=%d/%d)",
                     turn, tool_choice["type"], calls_used, settings.max_tool_calls)

            turn_text = []
            with client.messages.stream(
                model=settings.model,
                max_tokens=settings.max_tokens,
                system=system_prompt,
                tools=[PUBMED_TOOL, deep_research.DEEP_RESEARCH_TOOL],
                tool_choice=tool_choice,
                messages=messages,
                # Prompt caching: the API is stateless, so every turn re-sends the
                # whole conversation (system + tools + all prior abstracts). This
                # auto-places a cache "bookmark" at the end of the current prompt;
                # because the conversation only grows, the next turn reads this
                # entire prefix from cache (~0.1x cost) and only pays full price
                # for what we appended. Watch cache_read/cache_write in the logs.
                cache_control={"type": "ephemeral"},
            ) as stream:
                for text in stream.text_stream:
                    turn_text.append(text)
                    yield _event(type="text", text=text)
                final = stream.get_final_message()

            # Log how this turn ended: stop reason, token usage, text length.
            answer = "".join(turn_text)
            usage = final.usage
            # cache_read = tokens served from cache this turn (~0.1x price);
            # cache_write = tokens written to cache this turn (~1.25x price);
            # input = uncached tokens at full price. On turn 1 these caches are 0
            # (nothing cached yet, and the prompt may be under the ~4k minimum);
            # from turn 2 on, cache_read should be large and input small.
            log.info(
                "TURN %d end stop_reason=%s in_tokens=%s out_tokens=%s "
                "cache_read=%s cache_write=%s text_len=%d",
                turn, final.stop_reason, usage.input_tokens, usage.output_tokens,
                getattr(usage, "cache_read_input_tokens", 0),
                getattr(usage, "cache_creation_input_tokens", 0), len(answer),
            )
            if answer:
                log.debug("TURN %d text=%r", turn, answer)

            if final.stop_reason != "tool_use":
                break

            # Close this turn's bubble so the next turn opens a fresh one.
            yield _event(type="turn_end")

            # Record the assistant turn (includes the tool_use blocks). Every
            # tool_use needs a matching tool_result; run each while budget
            # remains, otherwise return a "limit reached" result so it wraps up.
            messages.append({"role": "assistant", "content": final.content})
            tool_results = []
            for block in final.content:
                if block.type != "tool_use":
                    continue
                if calls_used >= settings.max_tool_calls:
                    log.info("tool budget (%d) reached; skipping extra call", settings.max_tool_calls)
                    yield _event(type="notice", text="Search limit reached — answering from evidence already gathered.")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps({
                            "error": "search limit reached; answer with the evidence already gathered"
                        }),
                        "is_error": True,
                    })
                elif block.name == "deep_research":
                    # deep_research fans out one sub-agent per paper and streams
                    # live per-paper progress, so we drive its generator here
                    # (rather than via run_tool) to interleave its events with
                    # our NDJSON stream. It counts as ONE call against the budget
                    # even though it expands to N internal sub-agent calls.
                    calls_used += 1
                    combined = {"papers": []}
                    for kind, payload in deep_research.run_streaming(
                        block.input.get("papers", []),
                        client,
                        request_id,
                        log,
                        goal=block.input.get("goal", ""),
                    ):
                        if kind == "event":
                            yield _event(**payload)
                        else:  # ("result", combined)
                            combined = payload
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(combined),
                    })
                else:
                    calls_used += 1
                    # Surface the actual search the agent ran to the frontend.
                    if block.name == "search_pubmed":
                        yield _event(
                            type="search",
                            query=block.input.get("query", ""),
                            max_results=block.input.get("max_results", 3),
                            # Surface any optional filters the model applied so
                            # the UI can show "filtered to meta-analysis, 2020+".
                            filters={
                                k: block.input[k]
                                for k in ("publication_types", "last_n_years", "min_year", "max_year")
                                if k in block.input
                            },
                        )
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": run_tool(block, log, request_id),
                    })
            messages.append({"role": "user", "content": tool_results})
    except Exception as exc:  # noqa: BLE001 - surface a clean error to the client
        log.exception("STREAM FAILED on turn %d: %s", turn, exc)
        yield _event(type="error", text="The assistant hit an error. Please try again.")
        return

    log.info("DONE turns=%d searches=%d", turn, calls_used)
