"""Load and filter the eval dataset (evals/data/questions.jsonl).

Shared by the populate harness (Phase 2) and the checks (Phase 3) so both read the
gold the same way.
"""
import json
import os

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "questions.jsonl")


def load(path=DATA_FILE):
    """Return the dataset rows (list of dicts), one per non-blank JSONL line."""
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


def by_split(rows, split):
    """Filter rows to a split: 'dev', 'test', or 'all' (no filter)."""
    if split == "all":
        return list(rows)
    return [r for r in rows if r.get("split") == split]
