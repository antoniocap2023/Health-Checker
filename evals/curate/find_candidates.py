"""Curation helper — propose candidate gold PMIDs for a draft question.

This runs an INDEPENDENT PubMed search through the raw data layer (pubmed.py),
NOT the agent. That independence is the whole point: gold evidence must be chosen
without the agent in the loop, or the relevance metric becomes circular. The
output is a list to pick gold from by hand; nothing is written.

    backend/venv/bin/python evals/curate/find_candidates.py "aspirin preeclampsia" \\
        --n 12 --types meta-analysis systematic-review --min-year 2010
"""
import os
import sys

# This script lives in evals/curate/, so add evals/ to the path before the shim.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _pathsetup  # noqa: F401,E402  -- side effect: puts backend/ on sys.path

import argparse  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Find candidate gold PMIDs for a question.")
    ap.add_argument("query", help="focused PubMed query (not the raw question)")
    ap.add_argument("--n", type=int, default=12, help="how many candidates to show")
    ap.add_argument("--types", nargs="*", default=None,
                    help="restrict to study types (e.g. meta-analysis systematic-review)")
    ap.add_argument("--min-year", type=int, default=None, help="earliest publication year")
    args = ap.parse_args()

    import pubmed

    total, ids = pubmed._esearch(
        args.query, args.n, "curate",
        publication_types=args.types, min_year=args.min_year,
    )
    articles = pubmed.fetch_articles(ids, rid="curate")
    print(f"query={args.query!r}  total_matches={total}  showing {len(articles)}\n")
    for a in articles:
        pts = ", ".join(a.get("publication_types", [])) or "-"
        abstract = (a.get("abstract") or "").replace("\n", " ")
        print(f"PMID {a['pmid']} · {a.get('year', '?')} · {pts}")
        print(f"  {a.get('title', '')}")
        print(f"  {abstract[:240]}{'…' if len(abstract) > 240 else ''}\n")


if __name__ == "__main__":
    main()
