"""PubMed data layer for the chat backend.

One public function, `search_and_fetch`, runs NCBI E-utilities esearch
(sorted by relevance) followed by efetch, and returns the matching articles
with their abstracts. This is what the `search_pubmed` Claude tool calls.

NCBI E-utilities docs: https://www.ncbi.nlm.nih.gov/books/NBK25501/
"""
import json
import logging
import random
import time
from datetime import date
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from config import settings
from ratelimit import SlidingWindowRateLimiter

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

logger = logging.getLogger("healthchecker.pubmed")

# Optional study-type filter. The keys are the stable values we expose to the
# LLM (see SearchPubmedTool in tools.py); the values are PubMed's exact Publication
# Type strings. When the model asks for some of these, we AND a parenthesized
# OR-group onto the query so it can request, e.g., only meta-analyses and
# systematic reviews (the strongest evidence).
_PUBLICATION_TYPES = {
    "meta-analysis": "Meta-Analysis",
    "systematic-review": "Systematic Review",
    "randomized-controlled-trial": "Randomized Controlled Trial",
    "review": "Review",
    "guideline": "Guideline",
}


# Administrative / formatting publication-type tags PubMed attaches to almost
# every record. They carry no evidence-quality signal, so we drop them to keep
# each article's `publication_types` meaningful (the funding tags all share the
# "Research Support" prefix, so we match that rather than listing each variant).
_NOISE_PUBLICATION_TYPES = {"Journal Article", "English Abstract"}


def _meaningful_pub_types(art):
    """Study-type tags worth showing the model — noise/format tags removed.

    A denylist (not an allowlist): we keep everything except the known-noise
    tags, so informative-but-uncommon designs (Observational Study, Practice
    Guideline, ...) are never dropped just because we didn't enumerate them.
    """
    types = []
    for node in art.findall(".//PublicationTypeList/PublicationType"):
        t = (node.text or "").strip()
        if not t or t in _NOISE_PUBLICATION_TYPES or t.startswith("Research Support"):
            continue
        types.append(t)
    return types


def _build_term(query, publication_types):
    """Compose the esearch `term`: the query AND'd with any study-type filter.

    The study-type filter becomes a parenthesized OR-group AND'd to the query, so
    the base relevance match still applies and the filter only narrows it. With
    no (or unknown) types the term is just the bare query — identical to before,
    so existing behavior and tests are untouched. The date range is handled
    separately via esearch's mindate/maxdate params, not here.
    """
    if not publication_types:
        return query
    tags = [
        f'"{_PUBLICATION_TYPES[pt]}"[Publication Type]'
        for pt in publication_types
        if pt in _PUBLICATION_TYPES
    ]
    if not tags:
        return query
    return f"({query}) AND ({' OR '.join(tags)})"


# One shared limiter for all PubMed traffic in THIS process (the class now lives
# in ratelimit.py). Rate and window come from settings: 9/sec with an NCBI key,
# 2/sec without. On AWS this same limiter moves into a single pubmed-proxy
# instance so the cap holds across every backend container, not just this process.
_rate_limiter = SlidingWindowRateLimiter(settings.ncbi_rate_limit, settings.ncbi_window_seconds)

# Only these statuses are worth retrying. A 400 (bad query) would fail identically
# every time, so retrying it just wastes the rate budget — let it raise immediately.
# The retry COUNT and backoff base are tunable in settings (ncbi_max_retries /
# ncbi_backoff_base); the sliding-window limiter keeps us UNDER the cap proactively,
# while this retry path is the reactive safety net for a 429/5xx/network blip.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _request_with_retry(req, parse, rid, label):
    """Rate-limited HTTP GET with exponential backoff + jitter on transient errors.

    `parse(resp)` turns the open response into the value we want (JSON or XML).
    We acquire a rate-limiter slot before EVERY attempt, because each retry is a
    fresh request that counts against NCBI's cap. On a transient failure we sleep
    for a random time in [0, _BACKOFF_BASE * 2**attempt) — "full jitter" — so that
    concurrent threads don't all retry in lockstep and re-create the overload.
    Non-transient errors (e.g. HTTP 400) and the final attempt re-raise.
    """
    for attempt in range(settings.ncbi_max_retries + 1):
        _rate_limiter.acquire(rid)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return parse(resp)
        except (urllib.error.URLError, TimeoutError) as exc:
            # HTTPError is a subclass of URLError and carries a status code; for it
            # we retry only the codes above. A bare URLError/TimeoutError is a
            # network-level problem (DNS, connection, read timeout) — always transient.
            transient = (
                exc.code in _RETRYABLE_STATUS
                if isinstance(exc, urllib.error.HTTPError)
                else True
            )
            if not transient or attempt == settings.ncbi_max_retries:
                raise
            delay = random.uniform(0, settings.ncbi_backoff_base * (2 ** attempt))
            logger.warning(
                "[%s] %s attempt %d/%d failed (%s) — backing off %.2fs",
                rid, label, attempt + 1, settings.ncbi_max_retries + 1, exc, delay,
            )
            time.sleep(delay)


def _esearch(query, max_results, rid, *, publication_types=None, min_year=None, max_year=None):
    """Return the list of most-relevant PMIDs for `query` (plus total count).

    Optional filters narrow the search: `publication_types` restricts to study
    types (AND'd onto the query term), and `min_year`/`max_year` bound the
    publication date. Passing none of them reproduces the original behavior.
    """
    term = _build_term(query, publication_types)
    params = {
        "db": "pubmed",
        "term": term,
        "retmax": max_results,
        "retmode": "json",
        "sort": "relevance",  # Best Match — the esearch API default is most-recent.
        "tool": "health-checker",
    }
    # Bound the publication date when either end is given. NCBI's mindate/maxdate
    # need a datetype and expect both ends, so we fill an open end with a very
    # wide default (pdat = publication date).
    if min_year or max_year:
        params["datetype"] = "pdat"
        params["mindate"] = str(min_year) if min_year else "1800"
        params["maxdate"] = str(max_year) if max_year else "3000"
    if settings.ncbi_api_key:
        params["api_key"] = settings.ncbi_api_key

    url = f"{ESEARCH_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "health-checker/1.0"})
    # HTTP request #1 of 2 per search. Log the full term (with any filters) going
    # out and (below) what came back with timing, so NCBI traffic is visible.
    logger.info("[%s] HTTP esearch GET term=%r retmax=%s api_key=%s",
                rid, term, max_results, "yes" if settings.ncbi_api_key else "no")
    started = time.perf_counter()
    data = _request_with_retry(req, json.load, rid, "esearch")
    elapsed_ms = (time.perf_counter() - started) * 1000

    result = data.get("esearchresult", {})
    if "ERROR" in result:
        logger.warning("[%s] HTTP esearch ERROR=%s (%.0f ms)", rid, result["ERROR"], elapsed_ms)
        raise RuntimeError(result["ERROR"])
    count, ids = int(result.get("count", 0)), result.get("idlist", [])
    logger.info("[%s] HTTP esearch OK count=%d ids=%s (%.0f ms)", rid, count, ids, elapsed_ms)
    return count, ids


def _extract_year(art):
    """Best-effort publication year as a string ('' if none).

    Prefer the structured PubDate/Year; fall back to the free-text MedlineDate
    (e.g. "2019 Nov-Dec") and finally the electronic ArticleDate.
    """
    year = art.findtext(".//Journal/JournalIssue/PubDate/Year")
    if year:
        return year.strip()
    medline = art.findtext(".//Journal/JournalIssue/PubDate/MedlineDate", default="")
    if medline[:4].isdigit():
        return medline[:4]
    return (art.findtext(".//ArticleDate/Year", default="") or "").strip()


def _extract_authors(art):
    """Compact author label for citations: 'Smith J', 'Smith J et al.', or ''.

    We deliberately don't return the full list — for triage the first author plus
    "et al." is enough, and it keeps the payload (and the model's context) small.
    """
    names = []
    for a in art.findall(".//AuthorList/Author"):
        last = a.findtext("LastName")
        if last:
            initials = a.findtext("Initials", default="")
            names.append(f"{last} {initials}".strip())
        else:
            collective = a.findtext("CollectiveName")
            if collective:
                names.append(collective.strip())
    if not names:
        return ""
    return names[0] if len(names) == 1 else f"{names[0]} et al."


def _extract_article_ids(art):
    """Return (doi, pmcid) from the ArticleIdList — each '' when absent.

    A pmcid means the open-access full text lives in PMC and is fetchable later;
    that's the breadcrumb a future per-paper deep-read step needs.
    """
    doi = pmcid = ""
    for node in art.findall(".//ArticleIdList/ArticleId"):
        id_type = node.get("IdType")
        if id_type == "doi" and not doi:
            doi = (node.text or "").strip()
        elif id_type == "pmc" and not pmcid:
            pmcid = (node.text or "").strip()
    return doi, pmcid


def _efetch(ids, rid):
    """Fetch metadata + abstract for `ids` via efetch (PubMed returns XML)."""
    if not ids:
        return []

    params = {
        "db": "pubmed",
        "id": ",".join(ids),
        "rettype": "abstract",
        "retmode": "xml",
        "tool": "health-checker",
    }
    if settings.ncbi_api_key:
        params["api_key"] = settings.ncbi_api_key

    url = f"{EFETCH_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "health-checker/1.0"})
    # HTTP request #2 of 2 per search: fetch the abstracts for the PMIDs found.
    logger.info("[%s] HTTP efetch GET ids=%s", rid, ids)
    started = time.perf_counter()
    root = _request_with_retry(req, lambda resp: ET.parse(resp).getroot(), rid, "efetch")
    elapsed_ms = (time.perf_counter() - started) * 1000

    articles = []
    for art in root.findall(".//PubmedArticle"):
        pmid = art.findtext(".//PMID", default="")
        title_el = art.find(".//ArticleTitle")
        title = "".join(title_el.itertext()) if title_el is not None else ""
        journal = art.findtext(".//Journal/Title", default="")

        # Structured abstracts split into multiple labeled sections — join them.
        parts = []
        for node in art.findall(".//Abstract/AbstractText"):
            text = "".join(node.itertext()).strip()
            label = node.get("Label")
            parts.append(f"{label}: {text}" if label else text)
        abstract = "\n".join(parts) if parts else "(no abstract available)"

        # Cheap metadata that's already in this XML — tiny next to the abstract,
        # but lets the model weigh evidence (study type, year) and cite properly
        # (authors, DOI). pmcid doubles as a "full text fetchable" flag.
        pub_types = _meaningful_pub_types(art)
        doi, pmcid = _extract_article_ids(art)

        articles.append({
            "pmid": pmid,
            "title": title.strip(),
            "journal": journal.strip(),
            "year": _extract_year(art),
            "authors": _extract_authors(art),
            "publication_types": pub_types,
            "doi": doi,
            "pmcid": pmcid,
            "full_text_available": bool(pmcid),
            "abstract": abstract,
        })
    logger.info("[%s] HTTP efetch OK articles=%d (%.0f ms)", rid, len(articles), elapsed_ms)
    return articles


def fetch_articles(pmids, rid="-"):
    """Public wrapper around `_efetch`: metadata + abstract for the given PMIDs.

    Returns the same list of article dicts `search_and_fetch` builds (incl. the
    `pmcid` breadcrumb and `abstract`), but keyed off explicit PMIDs rather than a
    relevance search. This is the entry point the deep-research step uses to look
    up a paper it was handed by an earlier `search_pubmed` call.
    """
    return _efetch(list(pmids), rid)


# The PMC full-text efetch returns JATS XML. The article body lives under <body>;
# the abstract/front-matter live elsewhere, so we read <body> specifically (and
# its absence is exactly how we detect a non-open-access record — see below).
PMC_EFETCH_URL = EFETCH_URL  # same endpoint, different db= param (pmc vs pubmed)


def fetch_full_text(pmcid, rid="-"):
    """Fetch the open-access full text for a PMCID, or '' when unavailable.

    Full text is NOT in the pubmed database — it lives in PMC, a separate db keyed
    by PMCID, and only for the open-access subset. We efetch `db=pmc` and parse the
    JATS XML <body>. When there's no <body> (the record isn't open access, or PMC
    returned only front-matter), we return '' so the caller falls back to the
    abstract. Reuses the shared rate limiter + retry so PMC traffic obeys the same
    NCBI cap as everything else.
    """
    if not pmcid:
        return ""
    # NCBI's db=pmc wants the bare numeric id, not the "PMC" prefix.
    numeric = pmcid.upper().removeprefix("PMC").strip()
    if not numeric:
        return ""

    params = {
        "db": "pmc",
        "id": numeric,
        "retmode": "xml",
        "tool": "health-checker",
    }
    if settings.ncbi_api_key:
        params["api_key"] = settings.ncbi_api_key

    url = f"{PMC_EFETCH_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "health-checker/1.0"})
    logger.info("[%s] HTTP pmc efetch GET pmcid=%s", rid, pmcid)
    started = time.perf_counter()
    root = _request_with_retry(req, lambda resp: ET.parse(resp).getroot(), rid, "pmc-efetch")
    elapsed_ms = (time.perf_counter() - started) * 1000

    # The body holds the actual article sections. itertext() flattens the nested
    # JATS markup (<sec>, <p>, <title>, inline tags) into readable prose; we drop
    # blank fragments and join so paragraphs stay separated.
    body = root.find(".//body")
    if body is None:
        logger.info("[%s] HTTP pmc efetch OK but no <body> (not open access) (%.0f ms)",
                    rid, elapsed_ms)
        return ""
    parts = [t.strip() for t in body.itertext() if t and t.strip()]
    text = "\n".join(parts)
    logger.info("[%s] HTTP pmc efetch OK body_chars=%d (%.0f ms)", rid, len(text), elapsed_ms)
    return text


def _resolve_min_year(min_year, last_n_years, today):
    """Turn a relative 'last N years' into an absolute min_year — the model never
    does this arithmetic; the server does it against its own clock.

    `last_n_years` wins when both are given. "Last N years" is inclusive of the
    current year, so the lower bound is today.year - (N - 1) — e.g. in 2026 the
    last 5 years is 2022..2026.
    """
    if last_n_years:
        return today.year - (int(last_n_years) - 1)
    return min_year


def search_and_fetch(
    query: str,
    max_results: int = 3,
    *,
    publication_types: list | None = None,
    min_year: int | None = None,
    max_year: int | None = None,
    last_n_years: int | None = None,
    rid: str = "-",
) -> dict:
    """Search PubMed (by relevance) and return the top articles with abstracts.

    Optional filters: `publication_types` (study types to restrict to),
    `min_year`/`max_year` (explicit publication-date bounds), and `last_n_years`
    (relative recency — resolved to a min_year against the server clock, so the
    caller never computes a year). `rid` tags this search's log lines.
    Returns {"total_matches": int, "articles": [{pmid, title, journal, abstract}]}.
    """
    max_results = max(1, min(int(max_results), 3))
    min_year = _resolve_min_year(min_year, last_n_years, date.today())
    total, ids = _esearch(
        query, max_results, rid,
        publication_types=publication_types, min_year=min_year, max_year=max_year,
    )
    articles = _efetch(ids, rid)
    return {"total_matches": total, "articles": articles}
