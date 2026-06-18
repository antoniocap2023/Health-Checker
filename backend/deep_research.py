"""Deep-research tool: per-paper sub-agents that read full text.

`search_pubmed` returns abstracts only — enough to triage, not enough to answer
questions about methods, exact effect sizes, subgroup results, or limitations.
The `deep_research` tool closes that gap. The main agent hands it a list of
papers (each a PMID + a focused instruction). For each paper we:

    1. efetch `db=pubmed` to get metadata + the PMCID  (pubmed.fetch_articles)
    2. if a PMCID exists, efetch `db=pmc` for the full-text body  (pubmed.fetch_full_text)
    3. fall back to the abstract when there's no open-access full text
    4. run ONE Sonnet sub-agent that reads that text and follows the instruction

Papers are processed concurrently (one thread each). `run_streaming` yields
progress events as each paper finishes, then a final combined result the main
agent reads as the tool result.
"""
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pubmed

# Sub-agents read a single paper and extract findings — a cheaper/faster model
# than the main Opus loop is plenty, and we fan out several at once. Overridable
# via env so the model can be tuned without a code change.
SUBAGENT_MODEL = os.environ.get("DEEP_RESEARCH_MODEL", "claude-sonnet-4-6")
# Bound the fan-out: each paper is a full-text Sonnet call, so cap how many a
# single deep_research call may spin up (extra papers are dropped with a notice).
DEEP_RESEARCH_MAX_PAPERS = int(os.environ.get("DEEP_RESEARCH_MAX_PAPERS", "6"))
# How many sub-agents run at once. The PubMed rate limiter still paces NCBI calls
# globally; this just bounds concurrent Anthropic calls / memory.
DEEP_RESEARCH_MAX_WORKERS = int(os.environ.get("DEEP_RESEARCH_MAX_WORKERS", "4"))
# Output ceiling per sub-agent (the findings it returns, not the paper it reads).
SUBAGENT_MAX_TOKENS = int(os.environ.get("DEEP_RESEARCH_MAX_TOKENS", "2048"))
# A few open-access papers are enormous; cap the input text we feed a sub-agent so
# one giant paper can't blow up cost/latency. We note in the prompt when truncated.
FULL_TEXT_CHAR_CAP = int(os.environ.get("DEEP_RESEARCH_CHAR_CAP", "120000"))

logger = logging.getLogger("healthchecker.deep_research")

DEEP_RESEARCH_TOOL = {
    "name": "deep_research",
    "description": (
        "Deeply read one or more SPECIFIC PubMed articles you have already found "
        "via search_pubmed. For each paper you supply its PMID and a focused "
        "instruction; a dedicated sub-agent reads the FULL text (open-access PMC "
        "subset) and returns findings for that paper. Prefer answering from the "
        "abstracts you already have; this is a heavyweight tool that reads whole "
        "papers, so reserve it for when the abstract genuinely cannot answer the "
        "question — either the user explicitly asked for full-text detail (exact "
        "numbers, effect sizes/CIs, methods, subgroup breakdowns, stated "
        "limitations, 'read the full paper'), or their specific question hinges on "
        "a detail abstracts omit. Do not call it merely to enrich an adequate "
        "answer. Papers WITHOUT open-access full text are NOT deep-read: they come "
        "back marked 'no_full_text' so you fall back to the abstract you already "
        "have. You MUST already have the PMIDs from a prior search_pubmed result; "
        "never invent PMIDs."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "papers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "pmid": {
                            "type": "string",
                            "description": "PubMed ID of the article to deep-read (from a prior search_pubmed result).",
                        },
                        "instructions": {
                            "type": "string",
                            "description": (
                                "What this sub-agent should extract or analyze from THIS paper. "
                                "The sub-agent sees only this text (plus the shared goal), not the "
                                "conversation, so make it focused and self-contained, and match it "
                                "to what the user actually needs."
                            ),
                        },
                    },
                    "required": ["pmid", "instructions"],
                },
                "description": (
                    "Array of OBJECTS, one per paper to research — NOT an array of bare "
                    "PMID strings. Each object must have both a `pmid` string and its own "
                    "`instructions` string, e.g. "
                    "[{\"pmid\": \"PMID_HERE\", \"instructions\": \"...\"}]."
                ),
            },
            "goal": {
                "type": "string",
                "description": (
                    "Optional. The overall question/goal this research serves, given to every "
                    "sub-agent as shared context so each read stays on-target."
                ),
            },
        },
        "required": ["papers"],
    },
}

SUBAGENT_SYSTEM_PROMPT = """You are a research sub-agent. You are given the text \
of ONE scientific paper and a specific instruction about what to extract from it.

Rules:
- Use ONLY the provided paper text. Do not add facts from your own knowledge.
- Follow the instruction precisely. Pull out concrete specifics — numbers, effect \
sizes, confidence intervals, sample sizes, study design, and stated limitations — \
when they're relevant to the instruction.
- If the paper does not contain what the instruction asks for, say so plainly \
rather than guessing.
- Be concise and factual. Your output goes back to a lead agent, not to a human, \
so return just the findings — no preamble."""


def _build_subagent_prompt(art, text, instructions, goal, truncated):
    """Assemble the single user message: goal + instruction + metadata + paper."""
    header = []
    if goal:
        header.append(f"Overall research goal: {goal}")
    header.append(f"Your instruction for this paper: {instructions}")
    header.append(
        "Paper metadata:\n"
        f"- PMID: {art.get('pmid', '')}\n"
        f"- Title: {art.get('title', '')}\n"
        f"- Journal: {art.get('journal', '')}\n"
        f"- Year: {art.get('year', '')}\n"
        f"- Authors: {art.get('authors', '')}"
    )
    if truncated:
        header.append(
            "(NOTE: the paper text below was truncated to fit a length limit; "
            "later sections may be missing.)"
        )
    return "\n\n".join(header) + "\n\n--- PAPER TEXT ---\n" + text


def _research_one_paper(client, paper, goal, rid, log):
    """Fetch one paper's text (full text or abstract) and run a sub-agent on it."""
    pmid = str(paper.get("pmid", "")).strip()
    # When the model passed only a PMID (no per-paper instruction), fall back to
    # the shared goal, then to a generic extraction so the sub-agent still has a task.
    instructions = (paper.get("instructions") or goal
                    or "Extract the key findings, methods, and results of this paper.")
    sub_rid = f"{rid}:{pmid}"

    # PMIDs are always numeric. Reject junk locally instead of firing it at NCBI
    # (which answers a malformed id with a noisy HTTP 400) and report it per-paper.
    if not pmid.isdigit():
        log.info("deep_research skipping non-numeric pmid %r", pmid)
        return {"pmid": pmid, "source": "error", "error": "invalid PMID (expected digits)"}

    arts = pubmed.fetch_articles([pmid], sub_rid)
    if not arts:
        log.info("deep_research paper %s not found", pmid)
        return {"pmid": pmid, "source": "unavailable", "error": "article not found"}
    art = arts[0]

    # deep_research exists to read the FULL text. If there's no open-access full
    # text (no PMCID, or PMC returns only front-matter for a non-OA record), we do
    # NOT spin up a sub-agent on the abstract — the main agent already has that
    # from search_pubmed. Return early so it knows to rely on the abstract instead.
    full_text = pubmed.fetch_full_text(art["pmcid"], sub_rid) if art.get("pmcid") else ""
    if not full_text:
        log.info("deep_research paper %s has no open-access full text — skipping sub-agent", pmid)
        return {
            "pmid": pmid,
            "title": art.get("title", ""),
            "source": "no_full_text",
            "note": (
                "No open-access full text is available for this PMID, so it was not "
                "deep-read. Rely on the abstract from your earlier search for this paper."
            ),
        }

    text = full_text
    truncated = len(text) > FULL_TEXT_CHAR_CAP
    if truncated:
        text = text[:FULL_TEXT_CHAR_CAP]

    prompt = _build_subagent_prompt(art, text, instructions, goal, truncated)
    # Log the sub-agent call the same way the main loop logs a turn: what went in
    # (model, instruction, how much text, whether truncated) before, and timing +
    # token usage + result size after. The full prompt and findings go to DEBUG
    # only, mirroring search_pubmed's FULL payload dump, so INFO stays scannable.
    log.info(
        "deep_research paper %s sub-agent CALL model=%s text_chars=%d truncated=%s instruction=%r",
        pmid, SUBAGENT_MODEL, len(text), truncated, instructions[:120],
    )
    log.debug("deep_research paper %s sub-agent PROMPT=%r", pmid, prompt)
    started = time.perf_counter()
    msg = client.messages.create(
        model=SUBAGENT_MODEL,
        max_tokens=SUBAGENT_MAX_TOKENS,
        system=SUBAGENT_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    findings = "".join(b.text for b in msg.content if b.type == "text").strip()
    usage = getattr(msg, "usage", None)
    log.info(
        "deep_research paper %s sub-agent DONE (%.0f ms) stop_reason=%s in_tokens=%s "
        "out_tokens=%s findings_len=%d",
        pmid, elapsed_ms, getattr(msg, "stop_reason", "?"),
        getattr(usage, "input_tokens", "?"), getattr(usage, "output_tokens", "?"),
        len(findings),
    )
    log.debug("deep_research paper %s FINDINGS=%r", pmid, findings)
    return {
        "pmid": pmid,
        "title": art.get("title", ""),
        "source": "full_text",
        "findings": findings,
    }


def _coerce_papers(papers):
    """Coerce the raw `papers` argument into a list, BEFORE iterating it.

    The schema asks for a list of objects, but the model sometimes sends other
    shapes. The dangerous one is a plain string: ``for p in papers`` over a str
    iterates one CHARACTER at a time, turning "21399917" into eight bogus
    one-char "papers". So normalize the container first:
      - list  -> use as-is
      - dict  -> a single paper, wrap in a list
      - str   -> try to parse it as JSON (the model sometimes stringifies the
                 whole array); if that fails, treat the string as one PMID
      - other -> wrap in a list
    Per-entry coercion (bare PMID -> object) still happens in _normalize_paper.
    """
    if papers is None:
        return []
    if isinstance(papers, list):
        return papers
    if isinstance(papers, dict):
        return [papers]
    if isinstance(papers, str):
        s = papers.strip()
        if not s:
            return []
        try:
            return _coerce_papers(json.loads(s))
        except (ValueError, TypeError):
            return [s]  # a single PMID, not iterable-by-character
    return [papers]


def _normalize_paper(p):
    """Coerce one papers[] entry to a {'pmid', 'instructions'} dict.

    The schema asks for objects, but the model sometimes passes a bare PMID
    string (or, rarely, a number). Accept those instead of crashing on
    ``p.get(...)`` — an entry with no instruction falls back to the shared goal
    in _research_one_paper.
    """
    if isinstance(p, dict):
        return p
    return {"pmid": str(p), "instructions": ""}


def run_streaming(papers, client, rid, log, goal=""):
    """Research each paper concurrently, yielding progress, then the result.

    Yields ("event", payload) tuples for the main loop to forward as NDJSON, and
    finally one ("result", combined) tuple the main loop feeds back to Claude as
    the tool result. Splitting events from the result this way lets the caller
    interleave its own streaming yields with our final return value.
    """
    papers = [_normalize_paper(p) for p in _coerce_papers(papers)]
    dropped = 0
    if len(papers) > DEEP_RESEARCH_MAX_PAPERS:
        dropped = len(papers) - DEEP_RESEARCH_MAX_PAPERS
        papers = papers[:DEEP_RESEARCH_MAX_PAPERS]
        log.info("deep_research clamped to %d papers (dropped %d)", DEEP_RESEARCH_MAX_PAPERS, dropped)

    pmids = [str(p.get("pmid", "")).strip() for p in papers]
    start_event = {"type": "deep_research", "papers": pmids}
    if goal:
        start_event["goal"] = goal
    if dropped:
        start_event["dropped"] = dropped
    yield ("event", start_event)

    results = []
    workers = min(DEEP_RESEARCH_MAX_WORKERS, len(papers)) or 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_paper = {
            pool.submit(_research_one_paper, client, p, goal, rid, log): p
            for p in papers
        }
        for fut in as_completed(future_to_paper):
            paper = future_to_paper[fut]
            pmid = str(paper.get("pmid", "")).strip()
            try:
                res = fut.result()
            except Exception as exc:  # noqa: BLE001 - report per-paper failure, keep going
                log.exception("deep_research paper %s FAILED: %s", pmid, exc)
                res = {"pmid": pmid, "source": "error", "error": str(exc)}
            results.append(res)
            yield ("event", {
                "type": "deep_research_paper",
                "pmid": res.get("pmid", pmid),
                "title": res.get("title", ""),
                "source": res.get("source", "error"),
                "status": "done",
            })

    yield ("result", {"papers": results})
