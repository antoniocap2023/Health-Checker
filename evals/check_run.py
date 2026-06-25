"""Score a populated eval run → per-record scorecards + a light summary.

Reads a run from the eval table, joins each record to its gold row, runs the four
checks + abstention, caches the per-record results to evals/results/<run_id>.checks.json
(so re-scoring/tuning a judge later is free), and prints a quick summary. The
RIGOROUS aggregation (earliest-stage attribution, mean +/- spread) is Phase 4 — this
summary is just enough to eyeball a run.

    backend/venv/bin/python evals/check_run.py --run-id seed-001            # sequential
    backend/venv/bin/python evals/check_run.py --run-id seed-001 --batch    # Batches API

The judge calls can run through the Anthropic Batches API (`--batch`, 50% off). A
batch is async and can take up to ~1h, so run `--batch` in the BACKGROUND. The
default (sequential) is best for small/dev/`--limit` runs.
"""
import _pathsetup  # noqa: F401  -- backend on path + backend/.env loaded

import argparse
import json
import logging
import os
from statistics import mean

from anthropic import Anthropic

import dataset as ds
import scorecard
from judges import abstention, decompose, faithfulness, relevance_judge, thoroughness
from judges.client import build_request, run_batch
from scorecard import assemble_record, score_record
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
        "relevance_hit_rate": _mean([1.0 if c["relevance"]["hit"] else 0.0 for c in answer_cards if c["relevance"]]),
        "relevance_precision": _mean([c["relevance"]["precision"] for c in answer_cards if c["relevance"]]),
        "faithfulness_rate": _mean([c["faithfulness"]["faithfulness_rate"] for c in answer_cards if c["faithfulness"]]),
        "unverifiable_citation_rate": _mean([c["faithfulness"]["unverifiable_rate"] for c in answer_cards if c["faithfulness"]]),
        "thoroughness_coverage": _mean([c["thoroughness"]["coverage"] for c in answer_cards if c["thoroughness"]]),
        "abstention_correct_rate": _mean([1.0 if c["abstention"]["correct"] else 0.0 for c in abstain_cards]),
    }


def _load_pairs(run_id, store, log, limit=None):
    """Read a run and join each record to its gold row → [(record, gold_row), ...]."""
    records = store.read_run(run_id)
    gold = {r["question_id"]: r for r in ds.load()}
    if limit is not None:
        records = records[:limit]
    pairs, missing = [], []
    for rec in records:
        qid = rec.get("question_id")
        if qid not in gold:
            missing.append(qid)
            continue
        pairs.append((rec, gold[qid]))
    if missing:
        log.warning("records with no matching gold row: %s", missing)
    return pairs


def run_checks(run_id, limit=None, client=None, store=None, log=None):
    """Sequential: score each record with inline judge calls."""
    client = client or Anthropic()
    store = store or EvalStore()
    log = log or logging.getLogger("healthchecker.eval.check")

    cards = []
    for rec, gold_row in _load_pairs(run_id, store, log, limit):
        log.info("scoring %s repeat=%s", gold_row.get("question_id"), rec.get("repeat"))
        cards.append(score_record(rec, gold_row, client))
    return cards


def run_checks_batched(run_id, client=None, store=None, log=None, limit=None):
    """Batched: run the judge calls through the Anthropic Batches API in two phases.

    Phase 1 (independent): per record — abstention; for answer rows also decompose,
    thoroughness (when subpoints exist), and relevance per retrieved paper.
    Phase 2: per-claim faithfulness, built from Phase 1's decompose results (faithfulness
    depends on decompose). Then assemble scorecards from the two result dicts — no calls.

    A batch can take up to ~1h; run this in the background.
    """
    client = client or Anthropic()
    store = store or EvalStore()
    log = log or logging.getLogger("healthchecker.eval.check")

    pairs = _load_pairs(run_id, store, log, limit)

    # ---- Phase 1: all independent judge calls ----
    # custom_id must match ^[a-zA-Z0-9_-]{1,64}$ (Batches API), so use "_" separators.
    reqs1 = []
    for i, (rec, gold_row) in enumerate(pairs):
        answer = scorecard._answer_text(rec)
        reqs1.append(build_request(f"{i}_abst", *abstention.build(answer), abstention.SCHEMA))
        if gold_row.get("expected_behavior") == "answer":
            reqs1.append(build_request(f"{i}_dec", *decompose.build(answer), decompose.SCHEMA,
                                       max_tokens=decompose.MAX_TOKENS))
            subpoints = gold_row.get("subpoints", [])
            if subpoints:
                reqs1.append(build_request(f"{i}_thor", *thoroughness.build(answer, subpoints),
                                           thoroughness.SCHEMA))
            question = gold_row.get("question", "")
            for j, art in enumerate(rec.get("retrieved", [])):
                reqs1.append(build_request(f"{i}_rel_{j}",
                                           *relevance_judge.build_paper(question, subpoints, art),
                                           relevance_judge.SCHEMA))
    log.info("phase 1 batch: %d requests", len(reqs1))
    res1 = run_batch(client, reqs1, log=log)

    # ---- Phase 2: faithfulness per claim (depends on Phase 1's decompose) ----
    reqs2, plans = [], {}
    for i, (rec, gold_row) in enumerate(pairs):
        if gold_row.get("expected_behavior") != "answer":
            continue
        claims = decompose.parse(res1.get(f"{i}_dec"))
        p = faithfulness.plan(rec, claims)
        plans[i] = p
        for k, entry in enumerate(p["to_judge"]):
            reqs2.append(build_request(f"{i}_faith_{k}",
                                       *faithfulness.build_claim(entry["claim"], entry["articles"]),
                                       faithfulness.SCHEMA))
    log.info("phase 2 batch: %d requests", len(reqs2))
    res2 = run_batch(client, reqs2, log=log)

    # ---- Assemble scorecards from the two result dicts (no model calls) ----
    cards = []
    for i, (rec, gold_row) in enumerate(pairs):
        parts = {"abstention": res1.get(f"{i}_abst")}
        if gold_row.get("expected_behavior") == "answer":
            subpoints = gold_row.get("subpoints", [])
            parts["decompose"] = res1.get(f"{i}_dec")
            parts["thoroughness"] = res1.get(f"{i}_thor") if subpoints else None
            parts["relevance"] = [res1.get(f"{i}_rel_{j}")
                                  for j in range(len(rec.get("retrieved", [])))]
            parts["faithfulness"] = [res2.get(f"{i}_faith_{k}")
                                     for k in range(len(plans[i]["to_judge"]))]
        cards.append(assemble_record(rec, gold_row, parts))
    return cards


def main():
    ap = argparse.ArgumentParser(
        description="Score an eval run through the four checks. "
                    "Use --batch for the Anthropic Batches API (50%% off, async ~1h — "
                    "run it in the BACKGROUND); the default is sequential.")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--batch", action="store_true",
                    help="run judges via the Batches API (50%% off, async; a batch can "
                         "take up to ~1h, so run this in the background)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    logging.basicConfig(level="WARNING", format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("healthchecker").setLevel("INFO")

    if args.batch:
        cards = run_checks_batched(args.run_id, limit=args.limit)
    else:
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
