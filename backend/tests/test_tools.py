"""Tests for evidence extraction — the inputs the eval suite later scores.

Two small, deterministic pieces (no Claude, no NCBI):
  - SearchPubmedTool.collect_evidence: turn a tool input + result into the
    {queries, retrieved} we persist.
  - agent._cited_pmids: parse the PMIDs an answer actually cited (Layer A).
"""
import agent
from tools import SearchPubmedTool


def test_collect_evidence_extracts_query_and_articles():
    tool = SearchPubmedTool()
    result = {
        "total_matches": 2,
        "articles": [
            {"pmid": "111", "title": "T1", "abstract": "A1",
             "year": 2020, "publication_types": ["Review"]},
            {"pmid": "222", "title": "T2", "abstract": "A2", "year": 2019},
        ],
    }
    ev = tool.collect_evidence({"query": "aspirin headache"}, result)

    assert ev["queries"] == ["aspirin headache"]
    assert [r["pmid"] for r in ev["retrieved"]] == ["111", "222"]
    assert ev["retrieved"][0] == {
        "pmid": "111", "title": "T1", "abstract": "A1",
        "year": 2020, "pub_types": ["Review"],
    }
    # Missing publication_types defaults to an empty list.
    assert ev["retrieved"][1]["pub_types"] == []


def test_collect_evidence_ignores_error_result():
    """An error result (or anything without articles) yields no evidence."""
    tool = SearchPubmedTool()
    assert tool.collect_evidence({"query": "x"}, {"error": "boom"}) == {}
    assert tool.collect_evidence({"query": "x"}, None) == {}


def test_cited_pmids_dedupes_and_is_case_insensitive():
    text = "First (PMID: 111). Second (pmid:222). Repeat (PMID: 111)."
    assert agent._cited_pmids(text) == ["111", "222"]


def test_cited_pmids_empty_when_none_cited():
    assert agent._cited_pmids("No citations here.") == []
