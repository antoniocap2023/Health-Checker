"""Score a populated eval run → per-record scorecards + a light summary.

Reads a run from the eval table, joins each record to its gold row, runs the four
checks + abstention, caches the per-record results to evals/results/<run_id>.checks.json
(so re-scoring/tuning a judge later is free), and prints a quick summary. The
RIGOROUS aggregation (earliest-stage attribution, mean +/- spread) is Phase 4 — this
summary is just enough to eyeball a run.

    backend/venv/bin/python evals/check_run.py --run-id seed-001
"""
import _pathsetup  # noqa: F401  -- backend on path + backend/.env loaded

import argparse
import json
import logging
import os
from statistics import mean

from anthropic import Anthropic

import dataset as ds
from scorecard import score_record
from store import EvalStore

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return round(mean(vals), 3) if vals else None


def summarize(cards):
    answer_cards = [c for c in cards if c["expected_behavior"] == "answer"]
    abstain_cards = [c for c in cards if c["expected_behavior"] == "abstain"]
    return {
        "records": len(cards),
        "validity_ok_rate": _mean([1.0 if c["validity"]["ok"] else 0.0 for c in cards]),
        "relevance_recall": _mean([c["relevance"]["recall"] for c in answer_cards if c["relevance"]]),
        "relevance_hit_rate": _mean([1.0 if c["relevance"]["hit"] else 0.0 for c in answer_cards if c["relevance"]]),
        "faithfulness_rate": _mean([c["faithfulness"]["faithfulness_rate"] for c in answer_cards if c["faithfulness"]]),
        "thoroughness_coverage": _mean([c["thoroughness"]["coverage"] for c in answer_cards if c["thoroughness"]]),
        "abstention_correct_rate": _mean([1.0 if c["abstention"]["correct"] else 0.0 for c in abstain_cards]),
    }


def run_checks(run_id, limit=None, client=None, store=None, log=None):
    client = client or Anthropic()
    store = store or EvalStore()
    log = log or logging.getLogger("healthchecker.eval.check")

    records = store.read_run(run_id)
    gold = {r["question_id"]: r for r in ds.load()}
    if limit is not None:
        records = records[:limit]

    cards, missing = [], []
    for rec in records:
        qid = rec.get("question_id")
        if qid not in gold:
            missing.append(qid)
            continue
        log.info("scoring %s repeat=%s", qid, rec.get("repeat"))
        cards.append(score_record(rec, gold[qid], client))

    if missing:
        log.warning("records with no matching gold row: %s", missing)
    return cards


def main():
    ap = argparse.ArgumentParser(description="Score an eval run through the four checks.")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    logging.basicConfig(level="WARNING", format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("healthchecker").setLevel("INFO")

    cards = run_checks(args.run_id, limit=args.limit)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out = args.out or os.path.join(RESULTS_DIR, f"{args.run_id}.checks.json")
    with open(out, "w") as fh:
        json.dump({"run_id": args.run_id, "cards": cards}, fh, indent=2, default=str)

    summary = summarize(cards)
    print(f"\nwrote {out}")
    print(f"summary (light; Phase 4 does the rigorous aggregation):")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
