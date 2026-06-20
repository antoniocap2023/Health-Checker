"""Tool definitions, execution, and the registry the agent loop dispatches over.

A "tool" here bundles THREE things in one place: its name, the JSON schema we
advertise to Claude, and the code that runs when Claude calls it. The agent loop
(agent.py) is completely generic — it never names a specific tool; it just drives
whatever the REGISTRY contains. Adding a tool = write one Tool subclass and add an
instance to REGISTRY. You touch nothing else: not the loop, not a dispatcher.

THE UNIFORM INTERFACE — why `run` is a generator
------------------------------------------------
Tools differ in shape: `search_pubmed` is request/response, while `deep_research`
streams live per-paper progress before returning a result. To let the loop treat
them identically, every tool's `run` is a GENERATOR that yields tagged tuples:

    ("event",  payload)   # zero or more — each forwarded to the user as an NDJSON
                          #   line, so a tool can stream progress as it works
    ("result", obj)       # exactly one, last — the JSON-serializable value fed
                          #   back to Claude as the tool_result

A plain request/response tool just yields no events and one result. A streaming
tool yields many events, then the result. Same shape either way.
"""
import json
import time
from dataclasses import dataclass

import deep_research
import pubmed


@dataclass
class ToolContext:
    """Everything a tool may need from the running request, injected by the loop.

    Bundled into one object so tool signatures don't grow a new parameter every
    time some tool needs another piece of request context.
    """
    client: object       # the Anthropic client (for tools that call models)
    request_id: str      # short id tagging this request's log lines
    log: object          # the request-scoped logger


class Tool:
    """Base class every tool implements.

    Subclasses set two class attributes and implement `run`:
      name        — the tool name Claude calls (matches definition["name"]).
      definition  — the JSON schema dict advertised to Claude.
      run(tool_input, ctx) — a generator yielding ("event", payload) lines and
                    finally one ("result", obj). See the module docstring.
    """
    name: str = ""
    definition: dict | None = None

    def run(self, tool_input, ctx):
        raise NotImplementedError


class SearchPubmedTool(Tool):
    """Search PubMed by relevance and return the top articles with abstracts."""

    name = "search_pubmed"
    definition = {
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

    def run(self, tool_input, ctx):
        log = ctx.log
        # Surface the actual search to the frontend BEFORE running it, so the UI
        # can show "searching PubMed for ..." plus any filters the model applied.
        yield ("event", {
            "type": "search",
            "query": tool_input.get("query", ""),
            "max_results": tool_input.get("max_results", 3),
            "filters": {
                k: tool_input[k]
                for k in ("publication_types", "last_n_years", "min_year", "max_year")
                if k in tool_input
            },
        })

        # Log the exact input the LLM chose (the query it wrote, how many it asked for).
        log.info("search_pubmed CALL input=%s", tool_input)
        started = time.perf_counter()
        try:
            result = pubmed.search_and_fetch(rid=ctx.request_id, **tool_input)
        except Exception as exc:  # noqa: BLE001 - report failure back to Claude
            log.exception("search_pubmed FAILED: %s", exc)
            yield ("result", {"error": str(exc)})
            return
        elapsed_ms = (time.perf_counter() - started) * 1000
        # Log what came back: timing, total matches, and the PMID + title of each.
        articles = result["articles"]
        summary = [f"{a['pmid']} {a['title'][:70]}" for a in articles]
        log.info(
            "search_pubmed RESULT total_matches=%d returned=%d (%.0f ms) articles=%s",
            result["total_matches"], len(articles), elapsed_ms, summary,
        )
        # Full payload (incl. abstracts the LLM actually reads) — DEBUG only, so
        # INFO stays scannable.
        log.debug("search_pubmed FULL=%s", json.dumps(result, indent=2))
        yield ("result", result)


class DeepResearchTool(Tool):
    """Deep-read specific papers' full text via per-paper sub-agents.

    `deep_research.run_streaming` already yields exactly our ("event", ...) /
    ("result", ...) protocol, so this is a thin pass-through that supplies the
    request context and unpacks the tool input.
    """

    name = "deep_research"
    definition = deep_research.DEEP_RESEARCH_TOOL

    def run(self, tool_input, ctx):
        yield from deep_research.run_streaming(
            tool_input.get("papers", []),
            ctx.client,
            ctx.request_id,
            ctx.log,
            goal=tool_input.get("goal", ""),
        )


# The registry: the ONE place that lists the active tools. To add a tool, write a
# Tool subclass above and add an instance here — nothing else changes.
REGISTRY = [SearchPubmedTool(), DeepResearchTool()]

# Name -> tool, for the loop to dispatch an incoming tool_use block by its name.
TOOLS = {t.name: t for t in REGISTRY}


def tool_definitions():
    """The list of tool schemas to advertise to Claude on every turn."""
    return [t.definition for t in REGISTRY]
