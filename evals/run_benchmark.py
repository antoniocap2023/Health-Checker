"""Run the whole benchmark in one shot: populate -> check -> aggregate -> report.

The offline eval is three CLIs (populate, check_run, report); the improvement loop needs
them as one call. This composes the importable functions in-process and writes the same
`results/<run_id>.checks.json` + `results/<run_id>.report.json` the manual flow produces,
and returns the aggregated report dict.

    backend/venv/bin/python evals/run_benchmark.py --run-id try-001 --split dev -n 3
    backend/venv/bin/python evals/run_benchmark.py --run-id try-001 --dry-run   # cost preflight
"""
import _pathsetup  # noqa: F401  -- backend on path + .env

import argparse
import json
import logging
import os

from anthropic import Anthropic

import aggregate as A
import dataset as ds
from check_run import run_checks, run_checks_batched
from populate import run_populate
from store import EvalStore

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")


def _count(split, repeats, limit):
    rows = [r for r in ds.load() if split == "all" or r.get("split") == split]
    if limit is not None:
        rows = rows[:limit]
    return len(rows) * repeats


def run_benchmark(run_id, *, split="dev", repeats=3, limit=None, batch=True,
                  client=None, store=None, log=None):
    """Populate the agent over the split, score it, aggregate → report dict (also persisted)."""
    client = client or Anthropic()
    store = store or EvalStore()
    log = log or logging.getLogger("healthchecker.eval.benchmark")

    run_populate(run_id, repeats=repeats, split=split, limit=limit, client=client, store=store, log=log)
    cards = (run_checks_batched if batch else run_checks)(run_id, client=client, store=store, log=log)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(os.path.join(RESULTS_DIR, f"{run_id}.checks.json"), "w") as fh:
        json.dump({"run_id": run_id, "cards": cards}, fh, indent=2, default=str)
    report = A.aggregate(cards)
    with open(os.path.join(RESULTS_DIR, f"{run_id}.report.json"), "w") as fh:
        json.dump({"run_id": run_id, "report": report}, fh, indent=2, default=str)
    return report


def main():
    ap = argparse.ArgumentParser(description="Run populate+check+aggregate as one benchmark.")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    ap.add_argument("-n", "--repeats", type=int, default=3)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-batch", action="store_true", help="score judges sequentially (default: batched)")
    ap.add_argument("--dry-run", action="store_true", help="print the Opus run count and exit (no calls)")
    args = ap.parse_args()

    logging.basicConfig(level="WARNING", format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("healthchecker").setLevel("INFO")

    if args.dry_run:
        print(f"DRY RUN {args.run_id}: split={args.split} n={args.repeats} "
              f"=> {_count(args.split, args.repeats, args.limit)} Opus agent runs (no calls made)")
        return

    report = run_benchmark(args.run_id, split=args.split, repeats=args.repeats,
                           limit=args.limit, batch=not args.no_batch)
    dev = report["by_split"]["dev"]["metrics"]
    print(f"\n{args.run_id}: faithfulness={dev['faithfulness_rate']} "
          f"relevance_hit={dev['relevance_hit_rate']} thoroughness={dev['thoroughness_coverage']}")
    print(f"wrote {os.path.join(RESULTS_DIR, args.run_id + '.report.json')}")


if __name__ == "__main__":
    main()
