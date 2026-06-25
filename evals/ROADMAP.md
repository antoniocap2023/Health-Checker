# Eval Suite — Roadmap & Status

Where the eval project is and what's next. Companion to
[`docs/eval-design.md`](../docs/eval-design.md) (the *why*),
[`JOURNAL.md`](JOURNAL.md) (run history), and [`JUDGE_TRUST.md`](JUDGE_TRUST.md)
(judge trust). Last updated 2026-06-25.

## The two goals

1. **Reliably measure** the agent's answer quality — defensibly enough to optimize against.
2. **Automate improvements + re-eval** — change something → re-measure → keep it only if it truly helped.

## Status — done so far

| Phase | What | State |
|---|---|---|
| 0 | Agent persists an **evidence record** (queries, retrieved abstracts, cited PMIDs) so runs are scoreable | ✅ |
| 1 | **Gold dataset** seed (`data/questions.jsonl`, 12 Qs, dev/test split) + validator + independent candidate finder | ✅ |
| 2 | **Populate + read** — run the agent into a separate eval table, tagged by `run_id` | ✅ |
| 3 | **The four checks** — validity, relevance (deterministic) + faithfulness, thoroughness, abstention (Sonnet judges, temp 0) | ✅ |
| 4 | **Aggregation + journal** — metrics, noise floor, conditional scoring, failure attribution; baseline entry | ✅ |
| — | **Judge trust** — 8/8 trap tests; gray-zone policies recorded | ✅ |

Goal (1) is largely in hand: a working, defensible measurement instrument with a noise floor, dev/test split, and trusted judges.

**What baseline-001 taught us:** the eval's first run surfaced a *dataset-labeling bug, not an agent bug* — myths with debunking evidence were mislabeled `abstain`. Fixed by relabeling (myths → `answer`/refute; thin questions → `answer` with caveats). Known gap created: **abstain=0** in the seed (see below).

## Immediate next: `baseline-002`

Re-run on the corrected dataset to lock in the clean bar:
```
backend/venv/bin/python evals/populate.py  --run-id baseline-002 -n 3
backend/venv/bin/python evals/check_run.py --run-id baseline-002
backend/venv/bin/python evals/report.py    --run-id baseline-002 --append-journal
```
Expect: myths (q011/q012) score as refutations; thin Qs (q009/q010) tested on the "flag limited evidence" sub-point; abstention reads `n/a`; carry-over signals = relevance recall ~0.70 and some faithfulness dings. This entry becomes the bar future changes must beat.

## What's left

- **Dataset growth (ongoing).** 12 → ~50 questions. **Top priority: add genuine no-evidence questions** to restore the abstention test (currently abstain=0). More questions → tighter, more trustworthy numbers.
- **Phase 5 — Formal judge validation.** Hand-label a sample of real verdicts; compute agreement / Cohen's κ per judge. Upgrades "8/8 trap tests" into a *measured* trust number — the credibility capstone for goal (1).
- **Phase 6 — Online eval.** Point the reference-free checks (validity, faithfulness) at *real* user conversations (read-only sample of the production table). Monitoring real usage, not just the benchmark.
- **Phase 7 — CI/CD (GitHub Actions).** Automate deploys: push → dev, manual button → prod (OIDC, no long-lived keys).
- **Phase 8 — The improvement loop (headline of goal 2).** Weekly job: run the benchmark → failure-attribution points at the weakest stage → an "improvement proposer" suggests a prompt/param change → apply → re-eval → **keep only if it beats the noise floor** → log in `JOURNAL.md`.

## The payoff — the iterations themselves

This is where the project comes alive and the showcase lives: use the eval to drive real improvements, one stage at a time. Example first iteration — relevance is ~0.70:
1. Hypothesize: query construction misses ~1/3 of gold papers.
2. Change how the agent turns a question into a PubMed query.
3. Re-run the benchmark.
4. Did recall beat the **±0.08 noise floor**? If yes, keep + journal it; if no, revert.

Repeat across stages (relevance → faithfulness → thoroughness). Each logged entry in `JOURNAL.md` is the iteration story.

## Cost optimizations (future)

- **Batch the judges — only once eval runs get frequent.** `check_run`'s judge calls are independent one-shot `messages.create` calls — a clean fit for the **Batches API** (50% off, async, ~1h). *Not worth it yet:* judge cost is ~$2 per run at 12 questions, ~$8–9 at 50 (Sonnet, ~½¢/call), so 50% off saves a dollar or two per run — less than the cost of restructuring `check_run` into submit-batch + poll + out-of-order handling. **The trigger is run *frequency*, not dataset size:** build it when the improvement loop (Phase 8) is re-running the benchmark dozens of times, or the weekly cron is live — cumulative judge volume is where 50% adds up. Discounts only the (cheaper, Sonnet) judge side; the Opus agent loops in `populate` can't be batched.
- Cheaper levers available now: smaller `N` for routine re-baselines (N=1 for quick checks), dev-only runs during the improvement loop.

## Known gaps / decisions on record
- **abstain=0** in the seed — restore during dataset growth (genuine no-evidence questions).
- **Judge policies** (recorded in `JUDGE_TRUST.md`): faithfulness lenient on fuzzy quantifiers; abstention strict (hedged claim = answered).
- **Thin-evidence behavior** = `answer` with an explicit "evidence is limited" sub-point (no separate calibration check yet).
