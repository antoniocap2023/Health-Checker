"""Inspect a populated eval run (Phase 3 imports EvalStore.read_run directly).

    backend/venv/bin/python evals/read.py --run-id seed-001
    backend/venv/bin/python evals/read.py --run-id seed-001 --json
"""
import _pathsetup  # noqa: F401  -- side effect: backend on path + backend/.env loaded

import argparse
import json
from collections import Counter

from store import EvalStore


def _answer(record):
    msgs = record.get("messages", [])
    if msgs and msgs[-1].get("role") == "assistant":
        return msgs[-1].get("content", "")
    return ""


def main():
    ap = argparse.ArgumentParser(description="Inspect an eval run by run_id.")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--json", action="store_true", help="dump full records as JSON")
    args = ap.parse_args()

    records = EvalStore().read_run(args.run_id)
    if args.json:
        print(json.dumps(records, indent=2, default=str))
        return

    per_q = Counter(r.get("question_id") for r in records)
    print(f"run_id={args.run_id}  records={len(records)}  questions={len(per_q)}")
    print(f"per-question counts: {dict(sorted(per_q.items()))}\n")
    for r in sorted(records, key=lambda r: (r.get("question_id", ""), r.get("repeat", 0))):
        ans = _answer(r).replace("\n", " ")
        print(f"{r.get('question_id')} r{r.get('repeat')} · "
              f"retrieved={len(r.get('retrieved', []))} cited={len(r.get('cited_pmids', []))}")
        print(f"    {ans[:90]}{'…' if len(ans) > 90 else ''}")


if __name__ == "__main__":
    main()
