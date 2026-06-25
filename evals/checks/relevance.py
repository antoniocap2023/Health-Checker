"""Retrieval relevance — deterministic, against the gold PMIDs.

Recall-led on purpose: gold is a non-exhaustive "acceptable evidence set," so a
retrieved non-gold paper isn't necessarily wrong. Penalizing it (precision) would
mislead. We measure whether the agent found the gold evidence.
"""


def _retrieved_pmids(record):
    return {str(a.get("pmid")) for a in record.get("retrieved", []) if a.get("pmid")}


def relevance(record, gold_pmids):
    gold = {str(p) for p in (gold_pmids or [])}
    retrieved = _retrieved_pmids(record)
    hits = sorted(gold & retrieved)
    return {
        "gold": sorted(gold),
        "retrieved": sorted(retrieved),
        "hits": hits,
        "recall": (len(hits) / len(gold)) if gold else None,
        "hit": bool(hits),
    }
