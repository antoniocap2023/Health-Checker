"""The weekly self-improvement loop — measure, propose, re-eval, keep-if-it-wins, PR.

One cycle:
  1. Benchmark the CURRENT agent on dev (baseline).
  2. A Claude proposer drafts ONE single-variable change (a new grounding-rule bullet)
     targeting the weakest stage.
  3. Apply it to backend/prompts.py; re-benchmark in a FRESH subprocess (so the edit
     takes effect).
  4. The comparator decides keep/revert: the targeted metric must beat the noise floor
     with no other stage regressing beyond noise.
  5. If keep → append a JOURNAL entry and open a PR (the human approval gate; merging it
     triggers the deploy pipeline). If revert → discard the edit.

Safe by construction: one variable, allowlisted lever, a no-regression guard, test split
never touched, and nothing reaches prod without a human merging the PR.

    # cheap local smoke (no PR, reverts the edit after):
    backend/venv/bin/python evals/improve_loop.py --split dev -n 2 --limit 4 --no-pr
    # real cycle (opens a PR on a win):
    backend/venv/bin/python evals/improve_loop.py --split dev -n 3
"""
import _pathsetup  # noqa: F401  -- backend on path + .env

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime

from anthropic import Anthropic

import compare_runs
import journal_entry
import propose_change
from prompts import BASE_SYSTEM_PROMPT
from report import _config_line
from run_benchmark import run_benchmark

EVALS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(EVALS_DIR)
PROMPTS_PY = os.path.join(REPO, "backend", "prompts.py")
JOURNAL = os.path.join(EVALS_DIR, "JOURNAL.md")
RESULTS = os.path.join(EVALS_DIR, "results")
# A stable, unique anchor inside BASE_SYSTEM_PROMPT to insert a new rule before.
_ANCHOR = "For greetings or non-medical small talk, just respond normally without searching."

log = logging.getLogger("healthchecker.eval.improve")


def _git(*args):
    subprocess.run(["git", *args], cwd=REPO, check=True)


def apply_rule(rule_text):
    """Insert one new grounding bullet into backend/prompts.py (before the greeting line)."""
    text = open(PROMPTS_PY).read()
    if _ANCHOR not in text:
        raise RuntimeError("prompts.py anchor not found — can't apply the rule safely")
    open(PROMPTS_PY, "w").write(text.replace(_ANCHOR, f"- {rule_text}\n\n{_ANCHOR}", 1))


def _load_report(run_id):
    return json.load(open(os.path.join(RESULTS, f"{run_id}.report.json")))["report"]


def _benchmark_subprocess(run_id, split, repeats, limit):
    """Run the benchmark in a fresh process so the edited prompts.py is picked up."""
    cmd = [sys.executable, os.path.join(EVALS_DIR, "run_benchmark.py"),
           "--run-id", run_id, "--split", split, "-n", str(repeats)]
    if limit is not None:
        cmd += ["--limit", str(limit)]
    subprocess.run(cmd, cwd=REPO, check=True)
    return _load_report(run_id)


def main():
    ap = argparse.ArgumentParser(description="Weekly self-improvement loop (propose→re-eval→PR).")
    ap.add_argument("--split", choices=["dev"], default="dev", help="always dev (test is never tuned)")
    ap.add_argument("-n", "--repeats", type=int, default=3, help="repeats/question (>=2 for a noise floor)")
    ap.add_argument("--limit", type=int, default=None, help="cap #questions (cheap smoke)")
    ap.add_argument("--k", type=float, default=1.0, help="noise-band multiplier for keep/revert")
    ap.add_argument("--no-pr", action="store_true",
                    help="run the full cycle but print the verdict and revert the edit — no journal, no PR")
    args = ap.parse_args()

    logging.basicConfig(level="WARNING", format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("healthchecker").setLevel("INFO")

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    base_id, cand_id = f"improve-{ts}-base", f"improve-{ts}-cand"
    client = Anthropic()

    # 1. Baseline (current agent, in-process).
    log.info("baseline benchmark %s", base_id)
    base_report = run_benchmark(base_id, split=args.split, repeats=args.repeats, limit=args.limit, client=client)

    # 2. Propose one single-variable change.
    journal_text = open(JOURNAL).read() if os.path.exists(JOURNAL) else ""
    proposal = propose_change.propose(client, base_report, journal_text, BASE_SYSTEM_PROMPT)
    log.info("proposal: target=%s rule=%r", proposal["target_stage"], proposal["rule_text"])
    if not proposal["rule_text"]:
        print("proposer returned no rule — nothing to try this cycle.")
        return

    # 3. Apply + 4. candidate benchmark (fresh subprocess).
    apply_rule(proposal["rule_text"])
    try:
        cand_report = _benchmark_subprocess(cand_id, args.split, args.repeats, args.limit)
    except subprocess.CalledProcessError:
        _git("checkout", "--", PROMPTS_PY)
        raise

    # 5. Decide.
    verdict = compare_runs.decide(base_report, cand_report, proposal["target_metric"], k=args.k)
    print("\n=== proposal ===")
    print(f"  target stage : {proposal['target_stage']}  (metric {proposal['target_metric']})")
    print(f"  new rule     : {proposal['rule_text']}")
    print(f"  hypothesis   : {proposal['hypothesis']}")
    print(f"  verdict      : {verdict['rationale']}")

    if args.no_pr:
        _git("checkout", "--", PROMPTS_PY)  # keep the local tree clean
        print("\n--no-pr: reverted the edit; no journal, no PR.")
        return

    if not verdict["keep"]:
        _git("checkout", "--", PROMPTS_PY)
        print("\nreverted — nothing beat the noise floor this cycle.")
        return

    # 6. Keep → journal + PR (the human gate).
    entry = journal_entry.render(
        cand_report, run_id=cand_id, date=datetime.now().strftime("%Y-%m-%d"),
        hypothesis=f"[auto-improve] {proposal['hypothesis']} — added a grounding rule targeting "
                   f"{proposal['target_stage']}: \"{proposal['rule_text']}\"",
        config=_config_line(cand_report), decision=verdict["rationale"])
    with open(JOURNAL, "a") as fh:
        fh.write("\n\n" + entry + "\n")

    branch = f"improve/{proposal['target_stage']}-{ts}"
    _git("checkout", "-b", branch)
    _git("add", PROMPTS_PY, JOURNAL)
    _git("commit", "-m",
         f"[auto-improve] add {proposal['target_stage']} grounding rule\n\n{verdict['rationale']}\n\n"
         f"Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>")
    _git("push", "-u", "origin", branch)

    body = (f"**Automated improvement proposal** (weekly loop).\n\n"
            f"- **Target stage:** {proposal['target_stage']} (`{proposal['target_metric']}`)\n"
            f"- **Change:** added one grounding rule to `backend/prompts.py`:\n  > {proposal['rule_text']}\n"
            f"- **Hypothesis:** {proposal['hypothesis']}\n"
            f"- **Keep decision:** {verdict['rationale']}\n"
            f"- **Δ target:** {verdict['target_delta']:+} (noise ±{verdict['target_noise']})\n\n"
            f"Merging deploys to dev automatically; prod is a separate manual approval. "
            f"See the appended `JOURNAL.md` entry for the full before/after.\n")
    subprocess.run(["gh", "pr", "create", "--base", "main", "--head", branch,
                    "--title", f"[auto-improve] {proposal['target_stage']}: {proposal['rule_text'][:60]}",
                    "--body", body], cwd=REPO, check=True)
    print(f"\nopened PR from {branch} — review + merge to ship.")


if __name__ == "__main__":
    main()
