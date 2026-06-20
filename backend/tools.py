"""Tool definitions and dispatch for the agent loop.

This module owns the JSON Schema we advertise to Claude for `search_pubmed`, and
`run_tool`, which executes a tool_use block and returns its result as a JSON
string. The agent loop (agent.py) handles the streaming protocol and decides
WHEN to call a tool; this module is the WHAT — the contract and the execution.

(The `deep_research` tool's schema lives in deep_research.py alongside its
implementation, and the agent loop drives it directly because it streams
per-paper progress rather than returning a single result.)
"""
import json
import time

import pubmed

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


def run_tool(tool_use, log, rid):
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
