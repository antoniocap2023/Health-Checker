"""Integration tests: hit the REAL PubMed/NCBI E-utilities.

Unlike the unit tests (which feed canned bytes through a fake urlopen), these
make actual network calls to NCBI. They catch things mocks can't: NCBI changing
their response shape, our URL/params being wrong, the real rate limiter behaving
under real latency, and our XML/JSON parsing against real-world data.

These are FREE (no Claude, just NCBI) but require the network, so they're gated:
    pytest --run-integration
By default they're skipped. The `pytestmark` below tags every test in this file
with the `integration` marker that conftest.py's gating looks for.

STAYING UNDER NCBI's 10 req/sec CAP
-----------------------------------
Every real call here goes through pubmed's shared SlidingWindowRateLimiter
(9 req/sec), so the suite can't exceed NCBI's limit — see
test_rate_limiter.py::test_production_limiter_stays_under_ncbi_cap. One caveat:
do NOT run these with a process-parallel runner (e.g. `pytest -n` / pytest-xdist).
Each process gets its OWN limiter, so N workers => up to N*9 req/sec against NCBI.
Run integration/e2e tests in a single process (the default).
"""
import pytest

import pubmed

pytestmark = pytest.mark.integration


def test_search_and_fetch_returns_real_articles():
    """A well-known query should return parseable articles with PMIDs + abstracts."""
    result = pubmed.search_and_fetch(
        "aspirin myocardial infarction prevention", max_results=2, rid="itest"
    )

    assert result["total_matches"] > 0
    articles = result["articles"]
    assert 1 <= len(articles) <= 2

    for art in articles:
        # PMIDs are numeric strings; title/journal/abstract are non-empty strings.
        assert art["pmid"].isdigit(), f"unexpected pmid: {art['pmid']!r}"
        assert art["title"].strip()
        assert isinstance(art["abstract"], str) and art["abstract"].strip()


def test_search_and_fetch_caps_results_at_three():
    """The public function clamps max_results to 1..3 (see search_and_fetch)."""
    result = pubmed.search_and_fetch("diabetes", max_results=10, rid="itest")
    assert len(result["articles"]) <= 3


def test_esearch_contract_idlist_matches_count():
    """Verify NCBI's real contract: the id list length is min(total, retmax).

    (We don't try to force a zero-result query here — NCBI's query translation
    fuzzy-matches surprisingly hard, so guaranteeing zero from a live call is
    flaky. The zero-results parsing branch is covered deterministically in
    test_pubmed_parsing.py instead.)
    """
    count, ids = pubmed._esearch("cancer immunotherapy", max_results=3, rid="itest")

    assert isinstance(count, int) and count >= 0
    assert isinstance(ids, list)
    assert len(ids) == min(count, 3)
