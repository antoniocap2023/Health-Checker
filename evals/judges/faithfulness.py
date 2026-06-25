"""Faithfulness — is each cited claim actually supported by its cited abstract(s)?

The dangerous-hallucination check: a claim can carry a valid (retrieved) PMID and
still misstate what that paper found. We decompose the answer once, then judge each
claim that has a citation against the abstract(s) it cites (read straight from the
record — Phase 0 stored them). A claim whose cited PMID wasn't retrieved has no
abstract to stand on, so it's unsupported.
"""
from judges.client import judge
from judges.decompose import decompose

_SYSTEM = """You judge whether a single medical CLAIM is supported by the provided \
ABSTRACT(S).

- "Supported" means the abstract(s) state or directly imply the claim — a semantic \
match, not a verbatim one. Reasonable paraphrase is fine.
- If the abstracts do not substantiate the claim (or contradict it, or simply don't \
address it), it is NOT supported.
- Judge ONLY against the provided abstracts, not your own knowledge.
- Give a one-sentence reason."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "supported": {"type": "boolean"},
        "reasoning": {"type": "string"},
    },
    "required": ["supported", "reasoning"],
}


def _abstracts_by_pmid(record):
    return {str(a.get("pmid")): a for a in record.get("retrieved", []) if a.get("pmid")}


def _judge_claim(client, claim, pmids, by_pmid):
    available = [by_pmid[p] for p in pmids if p in by_pmid]
    if not available:
        # Every cited PMID is fabricated (not in the retrieved set) → nothing to
        # support the claim. Don't spend a judge call.
        return {"supported": False,
                "reasoning": "cited PMID(s) were not in the retrieved set (no abstract to support the claim)"}
    blocks = "\n\n".join(
        f"PMID {a.get('pmid')} — {a.get('title','')}\n{a.get('abstract','')}" for a in available
    )
    user = f"CLAIM:\n{claim}\n\nABSTRACT(S):\n{blocks}"
    return judge(client, _SYSTEM, user, _SCHEMA)


def score(client, record):
    """Decompose the answer and judge each cited claim. Returns the faithfulness block."""
    answer = record["messages"][-1]["content"] if record.get("messages") else ""
    by_pmid = _abstracts_by_pmid(record)
    claims = decompose(client, answer)

    scored, uncited = [], []
    for c in claims:
        pmids = c.get("cited_pmids", [])
        if not pmids:
            uncited.append(c["claim"])
            continue
        verdict = _judge_claim(client, c["claim"], pmids, by_pmid)
        scored.append({
            "claim": c["claim"],
            "cited_pmids": pmids,
            "supported": bool(verdict.get("supported")),
            "reasoning": verdict.get("reasoning", ""),
        })

    n_cited_claims = len(scored)
    n_supported = sum(1 for s in scored if s["supported"])
    n_claims = len(claims)
    return {
        "claims": scored,
        "n_claims": n_claims,
        "n_cited_claims": n_cited_claims,
        "n_supported": n_supported,
        "faithfulness_rate": (n_supported / n_cited_claims) if n_cited_claims else None,
        "uncited_claims": uncited,
        "uncited_rate": (len(uncited) / n_claims) if n_claims else 0.0,
    }
