"""Aggregate a scored run into a report + a JOURNAL.md entry.

Reads the per-record scorecards (evals/results/<run_id>.checks.json), aggregates them
(metrics, noise floor, conditional scoring, failure attribution), writes the machine
report, prints a summary, and renders the journal entry — optionally appending it to
evals/JOURNAL.md.

    backend/venv/bin/python evals/report.py --run-id baseline-001 --append-journal
"""
import _pathsetup  # noqa: F401  -- backend on path + .env

import argparse
import hashlib
import json
import os
from datetime import datetime

from config import settings

import aggregate as A
import dataset as ds
import journal_entry

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
JOURNAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "JOURNAL.md")


def _config_line(report):
    n = len(report["repeats"])
    qhash = hashlib.sha1(open(ds.DATA_FILE, "rb").read()).hexdigest()[:8]
    return (f"model={settings.model} · judge={settings.eval_judge_model} · "
            f"max_tool_calls={settings.max_tool_calls} · concise_mode={settings.concise_mode} · "
            f"N={n} · scope=dev+test · dataset=questions.jsonl @ {qhash}")


def _print_summary(report):
    print(f"records={report['n_records']} questions={report['n_questions']} repeats={report['repeats']}")
    for split in ("dev", "test"):
        b = report["by_split"][split]
        m, c = b["metrics"], b["conditional"]
        print(f"\n[{split}] n={b['n_records']}")
        print(f"  validity_ok={m['validity_ok_rate']}  fabricated={m['fabricated_pmid_rate']}")
        print(f"  relevance_hit={m['relevance_hit_rate']}  precision={m['relevance_precision']}  gold_recall(diag)={m['relevance_gold_recall']}")
        print(f"  faithfulness={m['faithfulness_rate']}  | retrieval-ok={c['faithfulness_rate_given_retrieval_ok']}  unverifiable={m['unverifiable_citation_rate']}")
        print(f"  thoroughness={m['thoroughness_coverage']}  uncited={m['uncited_claim_rate']}")
        print(f"  abstention_correct={m['abstention_correct_rate']}  false_answer={m['false_answer_rate']}")
        print(f"  attribution={b['attribution']}")


def main():
    ap = argparse.ArgumentParser(description="Aggregate a scored run + render a journal entry.")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--checks", default=None, help="path to <run_id>.checks.json")
    ap.add_argument("--hypothesis", default="baseline (first scored run)")
    ap.add_argument("--decision", default=None, help="the keep/revert decision line (else the baseline default)")
    ap.add_argument("--append-journal", action="store_true", help="append the entry to JOURNAL.md")
    args = ap.parse_args()

    checks = args.checks or os.path.join(RESULTS_DIR, f"{args.run_id}.checks.json")
    cards = json.load(open(checks))["cards"]
    report = A.aggregate(cards)

    out = os.path.join(RESULTS_DIR, f"{args.run_id}.report.json")
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(out, "w") as fh:
        json.dump({"run_id": args.run_id, "report": report}, fh, indent=2, default=str)

    _print_summary(report)
    print(f"\nwrote {out}")

    entry = journal_entry.render(
        report, run_id=args.run_id, date=datetime.now().strftime("%Y-%m-%d"),
        hypothesis=args.hypothesis, config=_config_line(report), decision=args.decision,
    )

    if args.append_journal:
        with open(JOURNAL, "a") as fh:
            fh.write("\n\n" + entry + "\n")
        print(f"appended entry to {JOURNAL}")
    else:
        print("\n--- JOURNAL entry (use --append-journal to write it) ---\n")
        print(entry)


if __name__ == "__main__":
    main()
