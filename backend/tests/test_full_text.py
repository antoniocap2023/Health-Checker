"""Tests for the PMC full-text fetch helpers (pubmed.fetch_full_text / fetch_articles).

Same approach as test_pubmed_parsing.py: feed canned bytes through a fake urlopen
(no network) and assert on the parsed output, with the rate limiter no-op'd so
timing never enters in. fetch_full_text hits `db=pmc` and reads the JATS <body>;
its absence (a non-open-access record) must yield "" so the caller falls back to
the abstract.

NOTE on real PMC behavior: `efetch db=pmc` only returns the <body> for articles
in the genuine PMC Open Access subset. A free-to-read but non-OA-subset article
comes back with `<front>` only (no body) — which is exactly why fetch_full_text
returns "" in that case and the caller falls back to the abstract.
"""
import io
import json
import urllib.parse
import urllib.request

import pytest

import pubmed


class _FakeResponse:
    """Context-manager response whose body is a file-like BytesIO of `data`."""

    def __init__(self, data: bytes):
        self._stream = io.BytesIO(data)

    def __enter__(self):
        return self._stream

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    monkeypatch.setattr(pubmed._rate_limiter, "acquire", lambda rid="-": None)


def _serve(monkeypatch, data: bytes):
    monkeypatch.setattr(
        pubmed.urllib.request, "urlopen",
        lambda req, timeout=None: _FakeResponse(data),
    )


_PMC_FULL_TEXT_XML = b"""<?xml version="1.0"?>
<pmc-articleset>
  <article>
    <front><article-meta><title-group>
      <article-title>Aspirin and headaches</article-title>
    </title-group></article-meta></front>
    <body>
      <sec>
        <title>Introduction</title>
        <p>Headache is common.</p>
      </sec>
      <sec>
        <title>Results</title>
        <p>Aspirin reduced pain by 40%.</p>
      </sec>
    </body>
  </article>
</pmc-articleset>
"""


def test_fetch_full_text_reads_body(monkeypatch):
    _serve(monkeypatch, _PMC_FULL_TEXT_XML)

    text = pubmed.fetch_full_text("PMC9999999", rid="t")

    # Body sections are flattened to readable text; front matter (the title) is
    # NOT included because we read <body> specifically.
    assert "Headache is common." in text
    assert "Aspirin reduced pain by 40%." in text
    assert "Introduction" in text
    assert "Aspirin and headaches" not in text


def test_fetch_full_text_strips_pmc_prefix(monkeypatch):
    # db=pmc wants the bare numeric id — assert we send it without the "PMC".
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FakeResponse(_PMC_FULL_TEXT_XML)

    monkeypatch.setattr(pubmed.urllib.request, "urlopen", fake_urlopen)
    pubmed.fetch_full_text("PMC1234567", rid="t")

    assert "id=1234567" in captured["url"]
    assert "db=pmc" in captured["url"]


_PMC_NO_BODY_XML = b"""<?xml version="1.0"?>
<pmc-articleset>
  <article>
    <front><article-meta><title-group>
      <article-title>Closed access paper</article-title>
    </title-group></article-meta></front>
  </article>
</pmc-articleset>
"""


def test_fetch_full_text_no_body_returns_empty(monkeypatch):
    # Non-open-access records come back without a <body>; we return "" so the
    # caller falls back to the abstract.
    _serve(monkeypatch, _PMC_NO_BODY_XML)
    assert pubmed.fetch_full_text("PMC5555555", rid="t") == ""


def test_fetch_full_text_blank_pmcid_skips_http(monkeypatch):
    def boom(req, timeout=None):
        raise AssertionError("urlopen should not be called for a blank pmcid")

    monkeypatch.setattr(pubmed.urllib.request, "urlopen", boom)
    assert pubmed.fetch_full_text("", rid="t") == ""


def test_fetch_articles_delegates_to_efetch(monkeypatch):
    # The public wrapper is just _efetch on explicit PMIDs — feed it canned XML.
    body = b"""<?xml version="1.0"?>
<PubmedArticleSet><PubmedArticle><MedlineCitation>
  <PMID>111</PMID>
  <Article><Journal><Title>J</Title></Journal>
  <ArticleTitle>T</ArticleTitle>
  <Abstract><AbstractText>A.</AbstractText></Abstract></Article>
</MedlineCitation></PubmedArticle></PubmedArticleSet>
"""
    _serve(monkeypatch, body)

    arts = pubmed.fetch_articles(["111"], rid="t")

    assert len(arts) == 1
    assert arts[0]["pmid"] == "111"
    assert arts[0]["abstract"] == "A."


# ---- integration: real PMC full text (opt-in, free) ------------------------

@pytest.mark.integration
def test_fetch_full_text_real_open_access():
    # Discover a genuine OA-subset PMCID at run time (specific ids churn, and only
    # the OA subset returns a <body> via efetch), then assert we get real text.
    params = {"db": "pmc", "term": "open access[filter] AND aspirin",
              "retmax": "3", "retmode": "json", "tool": "health-checker"}
    if pubmed._API_KEY:
        params["api_key"] = pubmed._API_KEY
    url = f"{pubmed.ESEARCH_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "health-checker/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        ids = json.load(resp)["esearchresult"]["idlist"]
    assert ids, "expected the PMC open-access subset to have aspirin articles"

    text = pubmed.fetch_full_text(f"PMC{ids[0]}", rid="it")
    assert isinstance(text, str)
    assert len(text) > 500  # a real article body, not just front matter
