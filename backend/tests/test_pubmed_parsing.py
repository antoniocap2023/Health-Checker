"""Tests for the PubMed parsing helpers _esearch and _efetch (pubmed.py).

THE BIG IDEA: NCBI returns JSON (esearch) and XML (efetch). The interesting,
breakable logic is how we turn those raw bytes into clean Python values. We feed
canned bytes through a fake `urlopen` (no network) and assert on the parsed
output. We also no-op the rate limiter so timing never enters into it.

Note `_request_with_retry` calls `parse(resp)`, where `resp` is the object our
fake urlopen yields. `_esearch` uses `json.load(resp)` and `_efetch` uses
`ET.parse(resp)`, and both of those accept a file-like object — so our fake
hands back an `io.BytesIO` wrapping the canned bytes.
"""
import datetime
import io
import urllib.parse

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
    """Applied to every test in this file: make acquire() a no-op."""
    monkeypatch.setattr(pubmed._rate_limiter, "acquire", lambda rid="-": None)


def _serve(monkeypatch, data: bytes):
    """Make pubmed's urlopen return `data` as the (single) HTTP response."""
    monkeypatch.setattr(
        pubmed.urllib.request, "urlopen",
        lambda req, timeout=None: _FakeResponse(data),
    )


# ---- esearch (JSON) --------------------------------------------------------

def test_esearch_parses_count_and_ids(monkeypatch):
    body = b'{"esearchresult": {"count": "42", "idlist": ["111", "222"]}}'
    _serve(monkeypatch, body)

    count, ids = pubmed._esearch("aspirin headache", max_results=2, rid="t")

    assert count == 42                 # parsed from string to int
    assert ids == ["111", "222"]


def test_esearch_handles_zero_results(monkeypatch):
    # The empty-result branch: count 0 and an empty idlist, parsed cleanly.
    body = b'{"esearchresult": {"count": "0", "idlist": []}}'
    _serve(monkeypatch, body)

    count, ids = pubmed._esearch("no such thing", max_results=3, rid="t")

    assert count == 0
    assert ids == []


def test_esearch_raises_on_api_error(monkeypatch):
    # NCBI signals query problems via an ERROR field rather than an HTTP error.
    body = b'{"esearchresult": {"ERROR": "Invalid db name"}}'
    _serve(monkeypatch, body)

    with pytest.raises(RuntimeError, match="Invalid db name"):
        pubmed._esearch("???", max_results=3, rid="t")


# ---- efetch (XML) ----------------------------------------------------------

_STRUCTURED_ABSTRACT_XML = b"""<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>111</PMID>
      <Article>
        <Journal><Title>Journal of Tests</Title></Journal>
        <ArticleTitle>Aspirin and headaches</ArticleTitle>
        <Abstract>
          <AbstractText Label="BACKGROUND">Pain is common.</AbstractText>
          <AbstractText Label="RESULTS">Aspirin helps.</AbstractText>
        </Abstract>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>
"""


def test_efetch_joins_structured_abstract(monkeypatch):
    _serve(monkeypatch, _STRUCTURED_ABSTRACT_XML)

    articles = pubmed._efetch(["111"], rid="t")

    assert len(articles) == 1
    art = articles[0]
    assert art["pmid"] == "111"
    assert art["title"] == "Aspirin and headaches"
    assert art["journal"] == "Journal of Tests"
    # The two labeled sections are joined with the label prefix, newline-separated.
    assert art["abstract"] == "BACKGROUND: Pain is common.\nRESULTS: Aspirin helps."


_NO_ABSTRACT_XML = b"""<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>222</PMID>
      <Article>
        <Journal><Title>Journal of Tests</Title></Journal>
        <ArticleTitle>An article with no abstract</ArticleTitle>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>
"""


def test_efetch_handles_missing_abstract(monkeypatch):
    _serve(monkeypatch, _NO_ABSTRACT_XML)

    articles = pubmed._efetch(["222"], rid="t")

    assert len(articles) == 1
    assert articles[0]["abstract"] == "(no abstract available)"


def test_efetch_empty_ids_returns_empty(monkeypatch):
    # With no ids there's nothing to fetch — should short-circuit, no HTTP call.
    called = False

    def boom(req, timeout=None):
        nonlocal called
        called = True
        raise AssertionError("urlopen should not be called for empty ids")

    monkeypatch.setattr(pubmed.urllib.request, "urlopen", boom)

    assert pubmed._efetch([], rid="t") == []
    assert called is False


# ---- search filters: term building (_build_term) ---------------------------

def test_build_term_no_filters_is_bare_query():
    # No filter => the term is exactly the query, so behavior is unchanged.
    assert pubmed._build_term("aspirin headache", None) == "aspirin headache"


def test_build_term_single_publication_type():
    assert pubmed._build_term("aspirin", ["meta-analysis"]) == (
        '(aspirin) AND ("Meta-Analysis"[Publication Type])'
    )


def test_build_term_multiple_types_are_or_joined():
    assert pubmed._build_term("statins", ["meta-analysis", "systematic-review"]) == (
        '(statins) AND ("Meta-Analysis"[Publication Type] '
        'OR "Systematic Review"[Publication Type])'
    )


def test_build_term_unknown_type_is_ignored():
    # An unrecognized value yields no tags, so we fall back to the bare query.
    assert pubmed._build_term("x", ["bogus"]) == "x"


# ---- relative recency: last_n_years -> absolute min_year (no model math) ----

def test_resolve_min_year_from_last_n_years():
    # "last 5 years" in 2026 is inclusive of 2026 => 2022..2026.
    today = datetime.date(2026, 6, 18)
    assert pubmed._resolve_min_year(None, 5, today) == 2022


def test_resolve_min_year_passes_through_explicit_min_year():
    today = datetime.date(2026, 6, 18)
    assert pubmed._resolve_min_year(2015, None, today) == 2015


def test_resolve_min_year_relative_wins_over_explicit():
    # If both are given, the relative window takes precedence.
    today = datetime.date(2026, 6, 18)
    assert pubmed._resolve_min_year(2000, 3, today) == 2024


def test_resolve_min_year_none_when_unset():
    today = datetime.date(2026, 6, 18)
    assert pubmed._resolve_min_year(None, None, today) is None


# ---- search filters: what _esearch actually sends to NCBI ------------------

def _capture_esearch_url(monkeypatch, body, **kwargs):
    """Run _esearch against a canned body, returning the decoded request URL."""
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FakeResponse(body)

    monkeypatch.setattr(pubmed.urllib.request, "urlopen", fake_urlopen)
    pubmed._esearch("statins cognition", max_results=3, rid="t", **kwargs)
    return urllib.parse.unquote_plus(captured["url"])


def test_esearch_applies_publication_type_and_date_filters(monkeypatch):
    body = b'{"esearchresult": {"count": "1", "idlist": ["1"]}}'
    url = _capture_esearch_url(
        monkeypatch, body,
        publication_types=["meta-analysis", "systematic-review"],
        min_year=2020, max_year=2024,
    )
    # Study types land in the term; the date range uses esearch's own params.
    assert '"Meta-Analysis"[Publication Type]' in url
    assert '"Systematic Review"[Publication Type]' in url
    assert "datetype=pdat" in url
    assert "mindate=2020" in url
    assert "maxdate=2024" in url


def test_esearch_without_filters_sends_bare_query(monkeypatch):
    body = b'{"esearchresult": {"count": "0", "idlist": []}}'
    url = _capture_esearch_url(monkeypatch, body)
    assert "term=statins cognition" in url
    assert "mindate" not in url
    assert "Publication Type" not in url


def test_esearch_open_ended_date_fills_the_other_bound(monkeypatch):
    # Only a lower bound => maxdate defaults wide so NCBI gets a valid range.
    body = b'{"esearchresult": {"count": "0", "idlist": []}}'
    url = _capture_esearch_url(monkeypatch, body, min_year=2015)
    assert "mindate=2015" in url
    assert "maxdate=3000" in url


# ---- efetch metadata extraction (study type, year, authors, ids) -----------

_RICH_METADATA_XML = b"""<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>333</PMID>
      <Article>
        <Journal>
          <JournalIssue><PubDate><Year>2021</Year></PubDate></JournalIssue>
          <Title>Journal of Tests</Title>
        </Journal>
        <ArticleTitle>Statins and cognition</ArticleTitle>
        <Abstract><AbstractText>Some findings.</AbstractText></Abstract>
        <AuthorList>
          <Author><LastName>Smith</LastName><Initials>J</Initials></Author>
          <Author><LastName>Doe</LastName><Initials>A</Initials></Author>
        </AuthorList>
        <PublicationTypeList>
          <PublicationType>Journal Article</PublicationType>
          <PublicationType>Meta-Analysis</PublicationType>
          <PublicationType>Research Support, Non-U.S. Gov't</PublicationType>
          <PublicationType>English Abstract</PublicationType>
          <PublicationType>Systematic Review</PublicationType>
        </PublicationTypeList>
      </Article>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="pubmed">333</ArticleId>
        <ArticleId IdType="doi">10.1000/xyz123</ArticleId>
        <ArticleId IdType="pmc">PMC9999999</ArticleId>
      </ArticleIdList>
    </PubmedData>
  </PubmedArticle>
</PubmedArticleSet>
"""


def test_efetch_extracts_metadata(monkeypatch):
    _serve(monkeypatch, _RICH_METADATA_XML)

    art = pubmed._efetch(["333"], rid="t")[0]

    assert art["year"] == "2021"
    assert art["authors"] == "Smith J et al."  # first author + et al. for 2+
    # Noise tags (Journal Article, Research Support, English Abstract) are
    # stripped; meaningful study types are kept in their original order.
    assert art["publication_types"] == ["Meta-Analysis", "Systematic Review"]
    assert art["doi"] == "10.1000/xyz123"
    assert art["pmcid"] == "PMC9999999"
    assert art["full_text_available"] is True   # derived from the pmcid


def test_efetch_metadata_defaults_when_absent(monkeypatch):
    # The minimal record (no dates/authors/ids) should yield empty defaults,
    # not raise — and full_text_available is False with no pmcid.
    _serve(monkeypatch, _NO_ABSTRACT_XML)

    art = pubmed._efetch(["222"], rid="t")[0]

    assert art["year"] == ""
    assert art["authors"] == ""
    assert art["publication_types"] == []
    assert art["doi"] == ""
    assert art["pmcid"] == ""
    assert art["full_text_available"] is False


_SINGLE_AUTHOR_XML = b"""<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>444</PMID>
      <Article>
        <Journal><Title>Journal of Tests</Title></Journal>
        <ArticleTitle>Solo work</ArticleTitle>
        <AuthorList>
          <Author><LastName>Lee</LastName><Initials>K</Initials></Author>
        </AuthorList>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>
"""


def test_efetch_single_author_has_no_et_al(monkeypatch):
    _serve(monkeypatch, _SINGLE_AUTHOR_XML)
    assert pubmed._efetch(["444"], rid="t")[0]["authors"] == "Lee K"
