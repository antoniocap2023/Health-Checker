"""Improvement proposer — a Claude layer that drafts ONE single-variable agent change.

Reads the latest benchmark report (its failure attribution → the weakest funnel stage),
the recent JOURNAL history, and the current system prompt, then proposes exactly one new
grounding rule (a single concise bullet) targeting that stage. Kept to ONE variable and
one lever (a prompt-rule addition) so the change is a clean, reviewable diff and the
keep/revert comparator can attribute the effect. Reuses the judges' forced-tool
structured-output path (`judges.client.judge`), so the output is schema-validated.

The human PR gate + the no-regression comparator are what make an autonomous proposer
safe here — this only *drafts*; nothing ships without a merge.
"""
import _pathsetup  # noqa: F401  -- backend on path + .env

import argparse
import json

from compare_runs import STAGE_METRIC
from judges.client import judge

_SYSTEM = """You improve a PubMed-grounded medical agent's system prompt.

Propose EXACTLY ONE new grounding rule — a single concise bullet (<= 40 words) — to ADD \
to the prompt, targeting the weakest eval stage named in the input, to improve that \
stage WITHOUT regressing the others (validity, relevance, faithfulness, thoroughness).

Rules for your proposal:
- It must be a GENERAL instruction (never about a specific question or PMID), consistent \
in tone with the existing rules.
- Change ONE thing only; do not restate rules already present.
- Return: the target stage, the rule text (no leading "- "), and a one-sentence hypothesis \
of the expected metric effect."""

SCHEMA = {
    "type": "object",
    "properties": {
        "target_stage": {"type": "string", "enum": list(STAGE_METRIC.keys())},
        "rule_text": {"type": "string"},
        "hypothesis": {"type": "string"},
    },
    "required": ["target_stage", "rule_text", "hypothesis"],
}


def weakest_stage(report):
    """The funnel stage to target: the largest targetable failure bucket, else the
    lowest headline metric among the prompt-addressable stages."""
    dev = report["by_split"]["dev"]
    fails = {k: v for k, v in dev["attribution"].items() if k in STAGE_METRIC and v > 0}
    if fails:
        return max(fails, key=fails.get)
    m = dev["metrics"]
    candidates = {s: m.get(STAGE_METRIC[s]) for s in ("faithfulness", "relevance", "thoroughness")}
    candidates = {s: v for s, v in candidates.items() if v is not None}
    return min(candidates, key=candidates.get) if candidates else "faithfulness"


def _build(report, journal_text, base_prompt, stage):
    m = report["by_split"]["dev"]["metrics"]
    user = (
        f"WEAKEST STAGE TO TARGET: {stage}\n\n"
        f"CURRENT DEV METRICS:\n{json.dumps(m, indent=2)}\n\n"
        f"RECENT JOURNAL (most recent entries):\n{journal_text[-4000:]}\n\n"
        f"CURRENT SYSTEM PROMPT (add your rule as one more bullet, don't duplicate):\n{base_prompt}"
    )
    return _SYSTEM, user


def parse(inp):
    inp = inp or {}
    stage = inp.get("target_stage") if inp.get("target_stage") in STAGE_METRIC else "faithfulness"
    return {"target_stage": stage, "target_metric": STAGE_METRIC[stage],
            "rule_text": (inp.get("rule_text") or "").strip(),
            "hypothesis": (inp.get("hypothesis") or "").strip()}


def propose(client, report, journal_text, base_prompt, model=None):
    """Draft one single-variable change → {target_stage, target_metric, rule_text, hypothesis}.

    Runs on the judge model (Sonnet) by default — capable enough for a one-shot rule
    suggestion, and it accepts `temperature` (the `judge()` helper forces temp 0; Opus 4.8
    deprecates `temperature`, so don't point this at the Opus agent model)."""
    stage = weakest_stage(report)
    system, user = _build(report, journal_text, base_prompt, stage)
    return parse(judge(client, system, user, SCHEMA, model=model))


def main():
    ap = argparse.ArgumentParser(description="Propose one improvement from a benchmark report.")
    ap.add_argument("--report", required=True, help="path to a .report.json")
    ap.add_argument("--journal", default=None, help="path to JOURNAL.md (for history context)")
    args = ap.parse_args()

    import os
    from anthropic import Anthropic
    from prompts import BASE_SYSTEM_PROMPT

    d = json.load(open(args.report))
    report = d.get("report", d)
    journal_text = open(args.journal).read() if args.journal and os.path.exists(args.journal) else ""
    proposal = propose(Anthropic(), report, journal_text, BASE_SYSTEM_PROMPT)
    print(json.dumps(proposal, indent=2))


if __name__ == "__main__":
    main()
