"""Citation validity — deterministic, no model.

The cheap, exact half of hallucination detection: did the answer cite any PMID it
never actually retrieved? `cited ∖ retrieved` are fabricated citations.
"""


def _retrieved_pmids(record):
    return {str(a.get("pmid")) for a in record.get("retrieved", []) if a.get("pmid")}


def validity(record):
    cited = [str(p) for p in record.get("cited_pmids", [])]
    retrieved = _retrieved_pmids(record)
    fabricated = [p for p in cited if p not in retrieved]
    n_cited = len(cited)
    return {
        "n_cited": n_cited,
        "fabricated_pmids": fabricated,
        "fabricated_rate": (len(fabricated) / n_cited) if n_cited else 0.0,
        "ok": not fabricated,
    }
