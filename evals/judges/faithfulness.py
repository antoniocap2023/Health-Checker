"""Faithfulness — is each cited claim actually supported by its cited source(s)?

The dangerous-hallucination check: a claim can carry a valid (retrieved) PMID and
still misstate what that paper found. We decompose the answer once, then judge each
claim that has a citation against the source(s) it cites (read straight from the
record — Phase 0 stored them). The "source" is the cited paper's title + abstract:
some real PubMed records (warning letters, comments) carry a title but no abstract,
and the title often conveys the claim ("Ear candling warning"), so a title alone is
enough to judge against. Only a cited paper with NO title AND NO abstract is truly
unverifiable. A claim whose cited PMID wasn't retrieved at all is fabricated.
"""
from judges.client import judge
from judges.decompose import decompose

_SYSTEM = """You judge whether a single medical CLAIM is supported by the provided \
SOURCE(S).

- SOURCE(S) = a cited paper's title + abstract when available. When only a title is \
present, support ONLY what the title conveys — do not assume specific numbers, effect \
sizes, or details the title can't carry.
- "Supported" means the source(s) state or directly imply the claim — a semantic \
match, not a verbatim one. Reasonable paraphrase is fine.
- If the source(s) do not substantiate the claim (or contradict it, or simply don't \
address it), it is NOT supported.
- Judge ONLY against the provided source(s). Do NOT use any outside knowledge about \
the paper (its real publication date, authors, etc.) — only the text shown to you.
- Judge the CLINICAL content of the claim. Do NOT mark a claim unsupported over \
bibliographic details (publication year, authors, exact participant counts) — those \
are citation metadata, not medical assertions. If the abstract doesn't state such a \
detail, ignore it and judge the medical content. Online-first vs journal-issue \
publication years are both acceptable.
- Grade meaning, not exact decimals: reasonable rounding (e.g. "~61,000" for 61,589) \
counts as supported.
- Give a one-sentence reason."""

SCHEMA = {
    "type": "object",
    "properties": {
        "supported": {"type": "boolean"},
        "reasoning": {"type": "string"},
    },
    "required": ["supported", "reasoning"],
}


def _abstracts_by_pmid(record):
    return {str(a.get("pmid")): a for a in record.get("retrieved", []) if a.get("pmid")}


def _has_text(article):
    """True if the article carries any judge-able text — a title OR an abstract."""
    return bool((article.get("title") or "").strip() or (article.get("abstract") or "").strip())


def build_claim(claim, articles):
    """The (system, user) prompt pair for judging one claim against its cited papers."""
    blocks = "\n\n".join(
        f"PMID {a.get('pmid')} — {a.get('title','')}\n"
        f"{(a.get('abstract') or '').strip() or '(no abstract available)'}"
        for a in articles
    )
    return _SYSTEM, f"CLAIM:\n{claim}\n\nSOURCE(S):\n{blocks}"


def parse_claim(inp):
    """Turn a faithfulness tool-result into {supported:bool, reasoning:str}."""
    inp = inp or {}
    return {"supported": bool(inp.get("supported")), "reasoning": inp.get("reasoning", "")}


def _judge_claim(client, claim, articles):
    """Sync: judge one claim against its cited articles. (judge_trust uses this.)"""
    system, user = build_claim(claim, articles)
    return parse_claim(judge(client, system, user, SCHEMA))


def plan(record, claims):
    """Bucket each decomposed claim into uncited / unverifiable / fabricated / to_judge.

    Pure (no model calls), so it's shared by the sync and batch paths:
      - to_judge:    cited a retrieved paper with text (title and/or abstract) → judge it.
      - unverifiable: cited a retrieved paper with NO title AND NO abstract — nothing to
                      check against; doesn't count against faithfulness, surfaced separately.
      - fabricated:  cited PMID(s) not in the retrieved set → counts as unsupported.
      - uncited:     no citation at all → an agent problem, tracked separately.
    """
    by_pmid = _abstracts_by_pmid(record)
    uncited, unverifiable, fabricated, to_judge = [], [], [], []
    for c in claims:
        pmids = c.get("cited_pmids", [])
        if not pmids:
            uncited.append(c["claim"])
            continue
        with_text = [by_pmid[p] for p in pmids if p in by_pmid and _has_text(by_pmid[p])]
        in_retrieved = [p for p in pmids if p in by_pmid]
        if with_text:
            to_judge.append({"claim": c["claim"], "cited_pmids": pmids, "articles": with_text})
        elif in_retrieved:
            unverifiable.append({
                "claim": c["claim"], "cited_pmids": pmids,
                "reason": "cited paper(s) retrieved but have no title or abstract to verify against",
            })
        else:
            fabricated.append({"claim": c["claim"], "cited_pmids": pmids})
    return {"uncited": uncited, "unverifiable": unverifiable,
            "fabricated": fabricated, "to_judge": to_judge}


def assemble(plan_result, verdicts):
    """Build the faithfulness block from a plan + per-claim verdicts (aligned to to_judge).

    `verdicts[k]` is the parsed verdict for `plan_result["to_judge"][k]`, or None if the
    judge was unavailable (e.g. a failed batch request) — those degrade to unverifiable.
    """
    uncited = list(plan_result["uncited"])
    unverifiable = list(plan_result["unverifiable"])
    scored = []
    for entry in plan_result["fabricated"]:
        scored.append({
            "claim": entry["claim"], "cited_pmids": entry["cited_pmids"],
            "supported": False, "reasoning": "cited PMID(s) were not in the retrieved set",
        })
    for entry, verdict in zip(plan_result["to_judge"], verdicts):
        if verdict is None:
            unverifiable.append({
                "claim": entry["claim"], "cited_pmids": entry["cited_pmids"],
                "reason": "judge unavailable",
            })
            continue
        scored.append({
            "claim": entry["claim"], "cited_pmids": entry["cited_pmids"],
            "supported": bool(verdict["supported"]), "reasoning": verdict.get("reasoning", ""),
        })

    n_claims = len(uncited) + len(unverifiable) + len(scored)
    n_verifiable = len(scored)
    n_supported = sum(1 for s in scored if s["supported"])
    n_unverifiable = len(unverifiable)
    n_cited_claims = n_verifiable + n_unverifiable
    return {
        "claims": scored,
        "unverifiable_claims": unverifiable,
        "uncited_claims": uncited,
        "n_claims": n_claims,
        "n_cited_claims": n_cited_claims,
        "n_verifiable": n_verifiable,
        "n_supported": n_supported,
        "n_unverifiable": n_unverifiable,
        "faithfulness_rate": (n_supported / n_verifiable) if n_verifiable else None,
        "unverifiable_rate": (n_unverifiable / n_cited_claims) if n_cited_claims else 0.0,
        "uncited_rate": (len(uncited) / n_claims) if n_claims else 0.0,
    }


def score(client, record):
    """Decompose the answer and judge each cited claim. Returns the faithfulness block."""
    answer = record["messages"][-1]["content"] if record.get("messages") else ""
    claims = decompose(client, answer)
    p = plan(record, claims)
    verdicts = [_judge_claim(client, e["claim"], e["articles"]) for e in p["to_judge"]]
    return assemble(p, verdicts)
