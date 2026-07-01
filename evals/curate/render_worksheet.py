"""Render a kappa labeling sheet (JSONL) into a readable markdown worksheet.

    backend/venv/bin/python evals/curate/render_worksheet.py <sheet.jsonl> <out.md>

Shows each item's prompt (verdict hidden), grouped by judge, with an ANSWER KEY block
at the bottom to fill T/F. Kept separate from the shell so code fences survive.
"""
import json
import sys

sheet_path, out_path = sys.argv[1], sys.argv[2]
sheet = [json.loads(l) for l in open(sheet_path) if l.strip()]
order = {"faithfulness": 1, "abstention": 2, "relevance": 3, "thoroughness": 4}
sheet.sort(key=lambda s: (order.get(s["judge"], 9), s["id"]))

FENCE = "`" * 3
out = [
    "# baseline-005 — Cohen's kappa labeling worksheet",
    "",
    "For each item, read the prompt and decide **T** (yes) / **F** (no).",
    "The judge's own verdict is hidden. Fill the ANSWER KEY at the bottom.",
    "",
    "- faithfulness: is the CLAIM supported by the SOURCE(S)?",
    "- abstention: did the answer ABSTAIN (decline for lack of evidence)?",
    "- relevance: is the PAPER topically relevant to the QUESTION?",
    "- thoroughness: does the ANSWER cover the SUB-POINT?",
]
cur = None
for s in sheet:
    if s["judge"] != cur:
        cur = s["judge"]
        out += ["", "---", f"## {cur.upper()}", ""]
    out += [f"### {s['id']}", FENCE, s["prompt"], FENCE, ""]

out += ["", "---", "## ANSWER KEY  (put T or F after each colon)", ""]
out += [f"{s['id']}: " for s in sheet]
open(out_path, "w").write("\n".join(out))
print(f"wrote {out_path} ({len(sheet)} items)")
