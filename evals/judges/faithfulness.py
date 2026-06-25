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


def _has_abstract(article):
    return bool((article.get("abstract") or "").strip())


def _judge_claim(client, claim, articles):
    """Judge one claim against the cited articles that actually have abstract text."""
    blocks = "\n\n".join(
        f"PMID {a.get('pmid')} — {a.get('title','')}\n{a.get('abstract','')}" for a in articles
    )
    user = f"CLAIM:\n{claim}\n\nABSTRACT(S):\n{blocks}"
    return judge(client, _SYSTEM, user, _SCHEMA)


def score(client, record):
    """Decompose the answer and judge each cited claim. Returns the faithfulness block.

    Three buckets per cited claim:
      - verifiable: cited a retrieved paper WITH an abstract → judged supported/not.
      - unverifiable: cited a retrieved paper that has NO abstract (warning letters,
        comments) — we can't machine-check it, so it does NOT count against
        faithfulness; reported separately as `unverifiable_rate`.
      - fabricated: cited a PMID not in the retrieved set → counts as unsupported.
    """
    answer = record["messages"][-1]["content"] if record.get("messages") else ""
    by_pmid = _abstracts_by_pmid(record)
    claims = decompose(client, answer)

    scored, uncited, unverifiable = [], [], []
    for c in claims:
        pmids = c.get("cited_pmids", [])
        if not pmids:
            uncited.append(c["claim"])
            continue
        with_abstract = [by_pmid[p] for p in pmids if p in by_pmid and _has_abstract(by_pmid[p])]
        in_retrieved = [p for p in pmids if p in by_pmid]
        if with_abstract:
            verdict = _judge_claim(client, c["claim"], with_abstract)
            scored.append({
                "claim": c["claim"], "cited_pmids": pmids,
                "supported": bool(verdict.get("supported")),
                "reasoning": verdict.get("reasoning", ""),
            })
        elif in_retrieved:
            # Cited a real retrieved paper, but it has no abstract to check against —
            # don't penalize faithfulness; surface separately.
            unverifiable.append({
                "claim": c["claim"], "cited_pmids": pmids,
                "reason": "cited paper(s) retrieved but have no abstract to verify against",
            })
        else:
            # Cited PMID(s) not in the retrieved set → fabricated → unsupported.
            scored.append({
                "claim": c["claim"], "cited_pmids": pmids, "supported": False,
                "reasoning": "cited PMID(s) were not in the retrieved set",
            })

    n_claims = len(claims)
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
