"""
PubMed-grounded Claude chatbot backend (FastAPI).

It exposes ONE endpoint, POST /api/chat, which takes the whole conversation so
far and streams Claude's reply back as newline-delimited JSON (NDJSON) events —
one JSON object per line. Event types:
    {"type": "text",   "text": "..."}                 a chunk of answer text
    {"type": "search", "query": "...", "max_results": n}  a PubMed search ran
    {"type": "deep_research", "papers": ["..."]}       a deep-read of papers began
    {"type": "deep_research_paper", "pmid": "...", "source": "..."}  one paper done
    {"type": "notice", "text": "..."}                  e.g. search limit reached
    {"type": "turn_end"}                               close this turn's bubble
    {"type": "error",  "text": "..."}                  fatal error mid-stream
The frontend starts a fresh assistant bubble per agent-loop turn (turn_end marks
the boundary) and renders `search` events as inline markers.

When the user asks a medical/biological question, Claude calls a `search_pubmed`
tool (see pubmed.py) to fetch the most relevant article abstracts, then answers
strictly from those abstracts and cites them by PMID.

Run it (from the backend/ folder, with the virtual environment active):
    uvicorn main:app --reload
"""

import json
import logging
import os
import time
import uuid
from datetime import date

from anthropic import Anthropic
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import deep_research
import pubmed

# Load ANTHROPIC_API_KEY (and NCBI_API_KEY) from the .env file in this folder.
load_dotenv()

# The Anthropic client automatically reads ANTHROPIC_API_KEY from the environment.
client = Anthropic()

# Log everything the agent does: each request, each loop turn (with stop reason
# and token usage), each PubMed search and what it returned, and the loop summary.
# Set LOG_LEVEL=DEBUG (e.g. in .env) to also log the full tool payload (the
# abstract text the LLM reads) and the full answer text. Defaults to INFO.
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
# Root logger stays at WARNING so third-party libraries (anthropic, httpx,
# httpcore, ...) only surface real problems, not their per-request chatter.
logging.basicConfig(
    level="WARNING",
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# Only OUR loggers get the configured verbosity. Setting it on the shared
# "healthchecker" parent covers both "healthchecker.agent" (here) and
# "healthchecker.pubmed" (in pubmed.py), since they inherit from it.
logging.getLogger("healthchecker").setLevel(LOG_LEVEL)
logger = logging.getLogger("healthchecker.agent")


# Prefix every line with the request's short id so one conversation can be
# followed even when requests interleave. We prefix in-message (rather than via
# the format string) so it works for any logger without extra config.
class _RidAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        return f"[{self.extra['rid']}] {msg}", kwargs


def _logger_for(request_id):
    return _RidAdapter(logger, {"rid": request_id})

MODEL = "claude-opus-4-8"

# Per-turn output ceiling. This is a hard cap on how much the model can write in
# one turn — too low and a rich answer is cut off mid-sentence (stop_reason=
# max_tokens). deep_research feeds the model whole papers, so the final synthesis
# needs more room than a plain abstract answer; 4096 comfortably fits it while
# CONCISE_MODE still keeps everyday answers short.
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "4096"))

# Cap on how many PubMed searches a single answer may run (across all turns and
# parallel calls). NCBI rate limits are now handled by the sliding-window limiter
# in pubmed.py, so this is purely a bound on the agentic loop / cost — not a rate
# guard. Once spent, the model is forced to answer with the evidence gathered.
MAX_TOOL_CALLS = 12

# Strict grounding: answers must come from the abstracts the tool returns.
SYSTEM_PROMPT = """You are a careful medical research assistant. You answer \
health, clinical, and biological questions using peer-reviewed evidence from \
PubMed.

How to answer:
- For any medical, clinical, or biological question, call the `search_pubmed` \
tool first to retrieve relevant article abstracts. Translate the user's question \
into a focused search query rather than searching their words verbatim.
- Answer ONLY from the abstracts the tool returns. Do not add medical claims \
from your own training knowledge. This is the most important rule you must follow. Everythng must be cited. 
- Cite every claim with its PubMed ID inline, like (PMID: 40123456).
- If the abstracts you get back are thin, off-target, or don't give you enough \
to answer confidently, search again with a refined or alternative query before \
answering — try different terms, broaden or narrow the focus, or break the \
question into parts and search each. Only say you couldn't find evidence after a \
few honest attempts come up short.
- If the returned abstracts do not actually address the question, say "I couldn't \
find evidence on that in PubMed" and do not guess. You may suggest how to refine \
the question.
- Default to answering from the abstracts `search_pubmed` returns; for most \
questions that is enough. Use the `deep_research` tool only when EITHER (a) the \
user explicitly asks for full-text-level depth — exact numbers, effect sizes, \
methods, subgroup results, stated limitations, or "read the full paper" / "go \
deeper than the abstract" — OR (b) answering their specific question genuinely \
requires a detail abstracts don't carry (full methods, exact confidence \
intervals, complete subgroup breakdowns, adverse-event detail). Do NOT deep-read \
just to be thorough or to enrich an answer the abstract already supports. In almost all cases, never offer using deep research or looking deeper unless explicitely asked for by the user. deep_research reads whole papers and is far heavier than a search, so use \
it sparingly. Call it with a `papers` ARRAY OF OBJECTS — never an array of bare \
PMID strings. Each object has exactly two string fields: `pmid` (a PubMed ID you \
got from a `search_pubmed` result) and `instructions` (what to extract from that \
specific paper). For example: papers=[{"pmid": "40311647", "instructions": "..."}, \
{"pmid": "26377054", "instructions": "..."}]. Give every paper its own \
`instructions`; do not pass a PMID without one. Only pass PMIDs you actually got \
from `search_pubmed`; never invent them. Papers with no open-access full text come back marked \
"no_full_text" and are NOT deep-read — for those, rely on the abstract you \
already have and say the full text wasn't available.
- Each article comes with its study type, publication year, and authors. Weigh \
stronger evidence (meta-analyses, systematic reviews, randomized trials) more \
heavily than weaker designs, and flag when a finding rests only on small, old, \
or low-quality studies. When a question asks for the strongest or most recent \
evidence, use the `publication_types` and year filters on `search_pubmed`.
- You are not a substitute for a doctor; keep answers factual and note when \
evidence is limited, mixed, or contradictory.

For greetings or non-medical small talk, just respond normally without searching."""

# Optional "concise mode" guidance, appended to the system prompt only when the
# CONCISE_MODE feature flag is on (default on). Toggle with CONCISE_MODE in .env:
# "true/1/yes/on" enables it, "false/0/no/off" disables it. This is a style nudge
# (it urges shorter, plainer answers) and is separate from the max_tokens cap,
# which is just a hard ceiling.
CONCISE_STYLE = """

Style — keep answers concise and easy to read:
- Lead with the direct answer in a sentence or two, then add only the detail \
that genuinely matters. Avoid long preambles, restating the question, and \
exhaustive write-ups.
- Use plain, everyday language. Avoid medical jargon where you can; when a \
technical term is unavoidable, define it briefly in parentheses.
Citations still apply to every claim."""

CONCISE_MODE = os.environ.get("CONCISE_MODE", "true").strip().lower() in {
    "1", "true", "yes", "on",
}
if CONCISE_MODE:
    SYSTEM_PROMPT += CONCISE_STYLE
logger.info("startup: CONCISE_MODE=%s", "on" if CONCISE_MODE else "off")

# Single combined tool: searches PubMed by relevance and returns abstracts.
PUBMED_TOOL = {
    "name": "search_pubmed",
    "description": (
        "Search PubMed for peer-reviewed biomedical literature and return the "
        "most relevant articles with their abstracts. Call this whenever the user "
        "asks a medical, clinical, or biological question that should be grounded "
        "in published research. Returns each article's title, journal, PMID, and "
        "abstract."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Focused PubMed search query (not the user's raw sentence).",
            },
            "max_results": {
                "type": "integer",
                "description": "How many articles to return, 1-3 (default 3).",
            },
            "publication_types": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "meta-analysis",
                        "systematic-review",
                        "randomized-controlled-trial",
                        "review",
                        "guideline",
                    ],
                },
                "description": (
                    "Optional. Restrict results to these study types (combined with "
                    "OR). Prefer 'meta-analysis' and 'systematic-review' when the "
                    "question calls for the strongest evidence. Use sparingly — a "
                    "narrow filter can return nothing; omit it to search all types."
                ),
            },
            "last_n_years": {
                "type": "integer",
                "description": (
                    "Optional. Restrict to research from the last N years (e.g. 5). "
                    "PREFER this for any 'recent' / 'last N years' / 'latest' "
                    "request — the server computes the cutoff year from today's "
                    "date, so you never calculate a year yourself."
                ),
            },
            "min_year": {
                "type": "integer",
                "description": "Optional. Earliest publication year, as an explicit year the user named (e.g. 2015). For relative recency use last_n_years instead.",
            },
            "max_year": {
                "type": "integer",
                "description": "Optional. Latest publication year to include (inclusive).",
            },
        },
        "required": ["query"],
    },
}

app = FastAPI()

# Allow the React dev server (http://localhost:5173) to call this API from the
# browser. Without this, the browser blocks the request for security reasons.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- What the frontend sends us --------------------------------------------
# The frontend sends the FULL conversation every time, because the Claude API is
# stateless: it does not remember previous requests on its own.
class Message(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]


def _run_tool(tool_use, log, rid):
    """Execute a tool_use block and return its result as a JSON string."""
    if tool_use.name == "search_pubmed":
        # Log the exact input the LLM chose (the query it wrote, how many it asked for).
        log.info("search_pubmed CALL input=%s", tool_use.input)
        started = time.perf_counter()
        try:
            result = pubmed.search_and_fetch(rid=rid, **tool_use.input)
        except Exception as exc:  # noqa: BLE001 - report failure back to Claude
            log.exception("search_pubmed FAILED: %s", exc)
            return json.dumps({"error": str(exc)})
        elapsed_ms = (time.perf_counter() - started) * 1000
        # Log what came back: timing, total matches, and the PMID + title of each.
        articles = result["articles"]
        summary = [f"{a['pmid']} {a['title'][:70]}" for a in articles]
        log.info(
            "search_pubmed RESULT total_matches=%d returned=%d (%.0f ms) articles=%s",
            result["total_matches"], len(articles), elapsed_ms, summary,
        )
        # Full payload (incl. abstracts the LLM actually reads) — only when the
        # logger is at DEBUG level, so INFO stays scannable.
        log.debug("search_pubmed FULL=%s", json.dumps(result, indent=2))
        return json.dumps(result)
    log.warning("unknown tool requested: %s", tool_use.name)
    return json.dumps({"error": f"unknown tool: {tool_use.name}"})


def _event(**payload):
    """Serialize one NDJSON event line (a JSON object terminated by a newline)."""
    return json.dumps(payload) + "\n"


# ---- The chat endpoint ------------------------------------------------------
@app.post("/api/chat")
def chat(request: ChatRequest):
    # Convert our Message objects into the plain dicts the SDK expects.
    messages = [{"role": m.role, "content": m.content} for m in request.messages]

    # Short id to tag every log line for this conversation; make the request id
    # visible to pubmed.py too so its HTTP-level logs share the same tag.
    request_id = uuid.uuid4().hex[:8]
    log = _logger_for(request_id)

    last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    log.info("REQUEST history=%d msgs, last_user=%r", len(messages), last_user[:200])

    # Give the model today's actual date (from the server clock). Without it the
    # model guesses the year from training data — e.g. it resolved "the last 5
    # years" to 2020+ when today is 2026. Built per-request so it never goes stale.
    system_prompt = (
        f"{SYSTEM_PROMPT}\n\nToday's date is {date.today().isoformat()}. For recency "
        "(e.g. \"the last 5 years\"), use search_pubmed's `last_n_years` filter and "
        "let the server compute the cutoff year — never calculate years yourself."
    )

    def generate():
        # Agentic loop: stream each assistant turn as NDJSON events. If a turn
        # ends by calling the tool, run it, feed the result back, and continue.
        # The typical flow is: turn 1 calls search_pubmed (little/no text), we
        # fetch abstracts, turn 2 streams the grounded answer to the user.
        # Each turn is its own bubble on the frontend, split by `turn_end`.
        calls_used = 0
        turn = 0
        try:
            while True:
                turn += 1
                # Once the search budget is spent, forbid further tool calls so the
                # model must answer from what it has (instead of searching forever).
                budget_spent = calls_used >= MAX_TOOL_CALLS
                tool_choice = {"type": "none"} if budget_spent else {"type": "auto"}
                log.info("TURN %d start tool_choice=%s (searches_used=%d/%d)",
                         turn, tool_choice["type"], calls_used, MAX_TOOL_CALLS)

                turn_text = []
                with client.messages.stream(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
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
                    if calls_used >= MAX_TOOL_CALLS:
                        log.info("tool budget (%d) reached; skipping extra call", MAX_TOOL_CALLS)
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
                        # (rather than via _run_tool) to interleave its events with
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
                            "content": _run_tool(block, log, request_id),
                        })
                messages.append({"role": "user", "content": tool_results})
        except Exception as exc:  # noqa: BLE001 - surface a clean error to the client
            log.exception("STREAM FAILED on turn %d: %s", turn, exc)
            yield _event(type="error", text="The assistant hit an error. Please try again.")
            return

        log.info("DONE turns=%d searches=%d", turn, calls_used)

    # StreamingResponse forwards each yielded line to the browser as it arrives.
    return StreamingResponse(generate(), media_type="application/x-ndjson")
