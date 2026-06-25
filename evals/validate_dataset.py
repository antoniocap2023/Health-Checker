"""Validate the eval dataset (evals/data/questions.jsonl).

Schema + integrity checks that catch the mistakes hand-curation makes: a missing
field, a bad enum, an answerable question with no gold evidence, an abstain
question that was given gold anyway, a duplicate id, a malformed PMID.

The default run is offline (no model, no network) so it's cheap to run on every
edit. ``--check-pmids`` additionally confirms every gold PMID actually resolves on
PubMed (catches typos and dead ids), which is the one network-dependent check.

    backend/venv/bin/python evals/validate_dataset.py [--check-pmids] [--file PATH]
"""
import _pathsetup  # noqa: F401  -- side effect: puts backend/ on sys.path

import argparse
import json
import os
import re
from collections import Counter

TYPES = {"factual", "comparative", "thin_evidence", "adversarial"}
BEHAVIORS = {"answer", "abstain"}
SPLITS = {"dev", "test"}
REQUIRED = (
    "question_id", "question", "type", "expected_behavior",
    "gold_pmids", "subpoints", "split", "notes",
)
_QID_RE = re.compile(r"^q\d+$")

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "questions.jsonl")


def validate_rows(rows):
    """Return a list of human-readable error strings; [] means valid. No network."""
    errors = []
    seen = set()
    for i, row in enumerate(rows):
        tag = row.get("question_id", f"row[{i}]")

        for field in REQUIRED:
            if field not in row:
                errors.append(f"{tag}: missing field '{field}'")

        qid = row.get("question_id")
        if qid is not None:
            if not _QID_RE.match(str(qid)):
                errors.append(f"{tag}: question_id must match q\\d+")
            if qid in seen:
                errors.append(f"{tag}: duplicate question_id")
            seen.add(qid)

        if row.get("type") not in TYPES:
            errors.append(f"{tag}: type must be one of {sorted(TYPES)}")
        if row.get("split") not in SPLITS:
            errors.append(f"{tag}: split must be one of {sorted(SPLITS)}")
        behavior = row.get("expected_behavior")
        if behavior not in BEHAVIORS:
            errors.append(f"{tag}: expected_behavior must be one of {sorted(BEHAVIORS)}")

        gold = row.get("gold_pmids")
        if not isinstance(gold, list):
            errors.append(f"{tag}: gold_pmids must be a list")
            gold = None
        subs = row.get("subpoints")
        if not isinstance(subs, list):
            errors.append(f"{tag}: subpoints must be a list")
            subs = None

        # Cross-field rules tie the label to its evidence requirements.
        if behavior == "answer":
            if gold is not None and not gold:
                errors.append(f"{tag}: answer rows need at least one gold PMID")
            if subs is not None and not subs:
                errors.append(f"{tag}: answer rows need at least one subpoint")
        elif behavior == "abstain":
            if gold:
                errors.append(f"{tag}: abstain rows must have empty gold_pmids")

        if isinstance(gold, list):
            for pmid in gold:
                if not (isinstance(pmid, str) and pmid.isdigit()):
                    errors.append(f"{tag}: gold PMID {pmid!r} must be a digit string")

    return errors


def check_pmids_exist(rows):
    """Network check: every gold PMID resolves on PubMed. Returns error strings."""
    import pubmed

    wanted = sorted({p for r in rows for p in r.get("gold_pmids", []) if isinstance(p, str)})
    if not wanted:
        return []
    found = {a["pmid"] for a in pubmed.fetch_articles(wanted, rid="validate")}
    return [f"gold PMID {p} did not resolve on PubMed" for p in wanted if p not in found]


def load_rows(path):
    rows = []
    with open(path) as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{n}: invalid JSON: {exc}")
    return rows


def summarize(rows):
    types = Counter(r.get("type") for r in rows)
    splits = Counter(r.get("split") for r in rows)
    abstain = sum(1 for r in rows if r.get("expected_behavior") == "abstain")
    return types, splits, abstain


def main():
    ap = argparse.ArgumentParser(description="Validate the eval dataset.")
    ap.add_argument("--file", default=DATA_FILE, help="path to questions.jsonl")
    ap.add_argument("--check-pmids", action="store_true", help="verify gold PMIDs resolve on PubMed (network)")
    args = ap.parse_args()

    rows = load_rows(args.file)
    errors = validate_rows(rows)
    # Only spend network calls if the data is otherwise well-formed.
    if args.check_pmids and not errors:
        errors += check_pmids_exist(rows)

    types, splits, abstain = summarize(rows)
    print(f"{len(rows)} rows | types={dict(types)} | splits={dict(splits)} | abstain={abstain}")
    if errors:
        print(f"\nFAILED ({len(errors)} error(s)):")
        for err in errors:
            print(f"  - {err}")
        raise SystemExit(1)
    print("OK")


if __name__ == "__main__":
    main()
