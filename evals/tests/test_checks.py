"""Deterministic checks — validity + relevance. No model, no network."""
import _pathsetup  # noqa: F401  -- backend on path

from checks.relevance import relevance
from checks.validity import validity


def _record(cited, retrieved_pmids):
    return {
        "cited_pmids": cited,
        "retrieved": [{"pmid": p, "abstract": "A", "title": "T"} for p in retrieved_pmids],
    }


def test_validity_flags_fabricated():
    v = validity(_record(["111", "222", "999"], ["111", "222"]))
    assert v["fabricated_pmids"] == ["999"]
    assert v["n_cited"] == 3
    assert round(v["fabricated_rate"], 3) == 0.333
    assert v["ok"] is False


def test_validity_clean():
    v = validity(_record(["111"], ["111", "222"]))
    assert v["fabricated_pmids"] == []
    assert v["fabricated_rate"] == 0.0
    assert v["ok"] is True


def test_relevance_full_partial_miss():
    full = relevance(_record([], ["111", "222"]), ["111", "222"])
    assert full["recall"] == 1.0 and full["hit"] is True

    partial = relevance(_record([], ["111", "333"]), ["111", "222"])
    assert partial["hits"] == ["111"] and partial["recall"] == 0.5 and partial["hit"] is True

    miss = relevance(_record([], ["111", "222"]), ["333"])
    assert miss["hits"] == [] and miss["recall"] == 0.0 and miss["hit"] is False
