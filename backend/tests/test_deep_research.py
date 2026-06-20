"""Tests for the deep_research orchestration (deep_research.py).

THE BIG IDEA: test OUR fan-out/streaming logic, not Claude and not NCBI. We
monkeypatch pubmed.fetch_articles / fetch_full_text to return canned data and
pass a fake Anthropic client whose messages.create() echoes a scripted finding.
Then we drain run_streaming() and assert on the events + the final result.
"""
from types import SimpleNamespace

import deep_research


class _FakeMessages:
    """Imitates client.messages: create() returns a message with one text block."""

    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        # Echo which model + a canned finding so tests can assert routing.
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="finding")])


class _FakeClient:
    def __init__(self):
        self.messages = _FakeMessages()


def _articles(pmid, *, pmcid="", abstract="an abstract"):
    return [{
        "pmid": pmid, "title": f"Title {pmid}", "journal": "J", "year": "2021",
        "authors": "Smith J", "publication_types": [], "doi": "", "pmcid": pmcid,
        "full_text_available": bool(pmcid), "abstract": abstract,
    }]


class _Log:
    """Minimal logger stand-in (run_streaming calls .info/.debug/.exception)."""
    def info(self, *a, **k):
        pass
    def debug(self, *a, **k):
        pass
    def exception(self, *a, **k):
        pass


def _drain(gen):
    """Split run_streaming's yields into (events, final_result)."""
    events, result = [], None
    for kind, payload in gen:
        if kind == "event":
            events.append(payload)
        else:
            result = payload
    return events, result


def test_full_text_preferred_when_pmcid_present(monkeypatch):
    monkeypatch.setattr(deep_research.pubmed, "fetch_articles",
                        lambda pmids, rid="-": _articles(pmids[0], pmcid="PMC1"))
    monkeypatch.setattr(deep_research.pubmed, "fetch_full_text",
                        lambda pmcid, rid="-": "FULL BODY TEXT")
    client = _FakeClient()

    papers = [{"pmid": "111", "instructions": "extract the effect size"}]
    events, result = _drain(deep_research.run_streaming(papers, client, "t", _Log()))

    # Start event + one per-paper done event.
    assert events[0]["type"] == "deep_research"
    assert events[0]["papers"] == ["111"]
    done = [e for e in events if e["type"] == "deep_research_paper"]
    assert len(done) == 1
    assert done[0]["source"] == "full_text"
    # Result carries the sub-agent finding and the source.
    assert result["papers"][0]["findings"] == "finding"
    assert result["papers"][0]["source"] == "full_text"
    # The sub-agent ran on the configured (cheaper) model with the full text.
    call = client.messages.calls[0]
    assert call["model"] == deep_research.settings.deep_research_model
    assert "FULL BODY TEXT" in call["messages"][0]["content"]


def test_no_pmcid_returns_no_full_text_without_subagent(monkeypatch):
    # No PMCID -> deep_research does NOT spin up a sub-agent on the abstract (the
    # lead agent already has it); it returns "no_full_text" so the agent falls back.
    monkeypatch.setattr(deep_research.pubmed, "fetch_articles",
                        lambda pmids, rid="-": _articles(pmids[0], pmcid="", abstract="ABS"))
    monkeypatch.setattr(deep_research.pubmed, "fetch_full_text",
                        lambda pmcid, rid="-": "")
    client = _FakeClient()

    papers = [{"pmid": "222", "instructions": "summarize"}]
    events, result = _drain(deep_research.run_streaming(papers, client, "t", _Log()))

    assert result["papers"][0]["source"] == "no_full_text"
    assert "note" in result["papers"][0]
    assert client.messages.calls == []  # no sub-agent call


def test_empty_full_text_returns_no_full_text_without_subagent(monkeypatch):
    # PMCID exists but PMC returned no body (non-OA subset) -> fetch_full_text "".
    monkeypatch.setattr(deep_research.pubmed, "fetch_articles",
                        lambda pmids, rid="-": _articles(pmids[0], pmcid="PMC9", abstract="ABS"))
    monkeypatch.setattr(deep_research.pubmed, "fetch_full_text",
                        lambda pmcid, rid="-": "")
    client = _FakeClient()

    papers = [{"pmid": "333", "instructions": "x"}]
    _, result = _drain(deep_research.run_streaming(papers, client, "t", _Log()))

    assert result["papers"][0]["source"] == "no_full_text"
    assert client.messages.calls == []


def test_missing_article_is_reported_not_fatal(monkeypatch):
    monkeypatch.setattr(deep_research.pubmed, "fetch_articles",
                        lambda pmids, rid="-": [])
    monkeypatch.setattr(deep_research.pubmed, "fetch_full_text",
                        lambda pmcid, rid="-": "")
    client = _FakeClient()

    papers = [{"pmid": "404", "instructions": "x"}]
    events, result = _drain(deep_research.run_streaming(papers, client, "t", _Log()))

    assert result["papers"][0]["source"] == "unavailable"
    # No sub-agent call for a paper we couldn't fetch.
    assert client.messages.calls == []
    done = [e for e in events if e["type"] == "deep_research_paper"]
    assert done[0]["source"] == "unavailable"


def test_each_paper_yields_one_done_event(monkeypatch):
    monkeypatch.setattr(deep_research.pubmed, "fetch_articles",
                        lambda pmids, rid="-": _articles(pmids[0], pmcid="PMC1"))
    monkeypatch.setattr(deep_research.pubmed, "fetch_full_text",
                        lambda pmcid, rid="-": "body")
    client = _FakeClient()

    papers = [{"pmid": str(i), "instructions": "x"} for i in range(3)]
    events, result = _drain(deep_research.run_streaming(papers, client, "t", _Log()))

    done = [e for e in events if e["type"] == "deep_research_paper"]
    assert len(done) == 3
    assert len(result["papers"]) == 3
    assert {p["pmid"] for p in result["papers"]} == {"0", "1", "2"}


def test_bare_pmid_strings_are_accepted(monkeypatch):
    # The schema asks for {pmid, instructions} objects, but the model sometimes
    # passes a list of bare PMID strings. That used to crash on p.get(...); now we
    # coerce each entry and fall back to the goal for the missing instruction.
    monkeypatch.setattr(deep_research.pubmed, "fetch_articles",
                        lambda pmids, rid="-": _articles(pmids[0], pmcid="PMC1"))
    monkeypatch.setattr(deep_research.pubmed, "fetch_full_text",
                        lambda pmcid, rid="-": "FULL BODY TEXT")
    client = _FakeClient()

    papers = ["11832527", "26377054"]  # bare strings, not dicts
    events, result = _drain(
        deep_research.run_streaming(papers, client, "t", _Log(), goal="effect sizes"))

    assert events[0]["papers"] == ["11832527", "26377054"]
    assert len(result["papers"]) == 2
    assert all(p["source"] == "full_text" for p in result["papers"])
    # The missing per-paper instruction fell back to the shared goal.
    assert "effect sizes" in client.messages.calls[0]["messages"][0]["content"]


def test_papers_passed_as_a_string_is_not_iterated_by_character(monkeypatch):
    # The crash mode: model sent `papers` as a STRING, and `for p in papers` over a
    # str iterates characters — "21399917" became 8 bogus 1-char "papers" that each
    # hit NCBI with a 400. We now JSON-parse the string back into one paper object.
    monkeypatch.setattr(deep_research.pubmed, "fetch_articles",
                        lambda pmids, rid="-": _articles(pmids[0], pmcid="PMC1"))
    monkeypatch.setattr(deep_research.pubmed, "fetch_full_text",
                        lambda pmcid, rid="-": "FULL BODY TEXT")
    client = _FakeClient()

    papers = '[{"pmid": "21399917", "instructions": "what happened to the kidney"}]'
    events, result = _drain(deep_research.run_streaming(papers, client, "t", _Log()))

    assert events[0]["papers"] == ["21399917"]  # one paper, not one-per-character
    assert len(result["papers"]) == 1
    assert result["papers"][0]["source"] == "full_text"


def test_non_numeric_pmid_is_rejected_without_network(monkeypatch):
    # A stray non-numeric id must not be sent to NCBI (it answers with a 400).
    def _boom(pmids, rid="-"):
        raise AssertionError("fetch_articles should not be called for a junk pmid")
    monkeypatch.setattr(deep_research.pubmed, "fetch_articles", _boom)
    monkeypatch.setattr(deep_research.pubmed, "fetch_full_text",
                        lambda pmcid, rid="-": "")
    client = _FakeClient()

    papers = ["<", "p", "abc"]  # all non-numeric
    _, result = _drain(deep_research.run_streaming(papers, client, "t", _Log()))

    assert all(p["source"] == "error" for p in result["papers"])
    assert client.messages.calls == []  # no sub-agent calls either


def test_clamps_to_max_papers(monkeypatch):
    monkeypatch.setattr(deep_research.pubmed, "fetch_articles",
                        lambda pmids, rid="-": _articles(pmids[0], pmcid="PMC1"))
    monkeypatch.setattr(deep_research.pubmed, "fetch_full_text",
                        lambda pmcid, rid="-": "body")
    monkeypatch.setattr(deep_research.settings, "deep_research_max_papers", 2)
    client = _FakeClient()

    papers = [{"pmid": str(i), "instructions": "x"} for i in range(5)]
    events, result = _drain(deep_research.run_streaming(papers, client, "t", _Log()))

    assert events[0]["dropped"] == 3
    assert len(events[0]["papers"]) == 2
    assert len(result["papers"]) == 2
