"""Populate the eval table by running the real agent over the gold dataset.

Drives `agent.run_chat_stream` IN-PROCESS (same code path as production) once per
question per repeat, and persists each run's evidence record to the separate eval
table tagged with run_id/question_id/repeat. No scoring here — this produces the
material Phase 3 grades.

    backend/venv/bin/python evals/ensure_eval_table.py            # once
    backend/venv/bin/python evals/populate.py --run-id seed-001 -n 3
    backend/venv/bin/python evals/populate.py --run-id smoke -n 1 --limit 2 --split dev

Cost: each run is a REAL Claude + PubMed call. Total = repeats x #questions.
"""
import _pathsetup  # noqa: F401  -- side effect: backend on path + backend/.env loaded

import argparse
import logging
import uuid
from datetime import datetime

import agent
from anthropic import Anthropic

import dataset as ds
from store import EvalStore


def run_populate(run_id, repeats=3, split="all", limit=None, client=None, store=None, log=None):
    """Run the agent over the dataset and persist tagged records. Returns a summary
    dict {run_id, questions, repeats, written, failures}."""
    client = client or Anthropic()
    store = store or EvalStore()
    log = log or logging.getLogger("healthchecker.eval.populate")

    rows = ds.by_split(ds.load(), split)
    if limit is not None:
        rows = rows[:limit]

    written = failures = 0
    total = len(rows) * repeats
    log.info("POPULATE run_id=%s questions=%d repeats=%d (=%d agent runs) split=%s",
             run_id, len(rows), repeats, total, split)

    for row in rows:
        qid = row["question_id"]
        for repeat in range(repeats):
            request_id = uuid.uuid4().hex[:8]
            rlog = agent.logger_for(request_id)
            messages = [{"role": "user", "content": row["question"]}]
            captured = {}

            def on_complete(final_messages, evidence):
                captured["messages"] = final_messages
                captured["evidence"] = evidence

            try:
                # Exhaust the NDJSON stream; we persist via the on_complete hook.
                for _ in agent.run_chat_stream(messages, client, request_id, rlog, on_complete):
                    pass
                if "messages" not in captured:
                    raise RuntimeError("stream ended without on_complete (no answer)")
                conversation_id = uuid.uuid4().hex
                store.save_record(run_id, qid, repeat, conversation_id,
                                  captured["messages"], captured["evidence"])
                written += 1
                log.info("  ok %s repeat=%d (%d/%d)", qid, repeat, written + failures, total)
            except Exception as exc:  # noqa: BLE001 - isolate one bad run from the batch
                failures += 1
                log.exception("  FAILED %s repeat=%d: %s", qid, repeat, exc)

    log.info("DONE run_id=%s written=%d failures=%d", run_id, written, failures)
    return {"run_id": run_id, "questions": len(rows), "repeats": repeats,
            "written": written, "failures": failures}


def main():
    ap = argparse.ArgumentParser(description="Run the agent over the dataset into the eval table.")
    ap.add_argument("--run-id", default=None, help="handle for this run (default: run-<timestamp>)")
    ap.add_argument("-n", "--repeats", type=int, default=3, help="runs per question (variance)")
    ap.add_argument("--split", choices=["dev", "test", "all"], default="all")
    ap.add_argument("--limit", type=int, default=None, help="cap #questions (smoke tests)")
    ap.add_argument("--dry-run", action="store_true", help="list what would run, make no calls")
    args = ap.parse_args()

    logging.basicConfig(level="WARNING", format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("healthchecker").setLevel("INFO")

    run_id = args.run_id or f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    if args.dry_run:
        rows = ds.by_split(ds.load(), args.split)
        if args.limit is not None:
            rows = rows[:args.limit]
        print(f"DRY RUN run_id={run_id} split={args.split} "
              f"questions={len(rows)} repeats={args.repeats} "
              f"=> {len(rows) * args.repeats} agent runs (no calls made)")
        for r in rows:
            print(f"  {r['question_id']}: {r['question']}")
        return

    summary = run_populate(run_id, repeats=args.repeats, split=args.split, limit=args.limit)
    print(f"\n{summary}")


if __name__ == "__main__":
    main()
