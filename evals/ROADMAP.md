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
| — | **Judge trust** — 10/10 trap tests (incl. relevance); gray-zone policies recorded | ✅ |
| 5 | **Metric hardening** — relevance → topical hit@k + precision (judge; gold demoted to diagnostic); faithfulness excludes unverifiable (no-abstract) citations; decompose token-truncation fix | ✅ (code; re-score pending) |

Goal (1) is largely in hand: a working, defensible measurement instrument with a noise floor, dev/test split, and trusted judges.

**What the baselines taught us — three measurement bugs, not agent bugs:** (1) baseline-001: myths mislabeled `abstain` → relabeled to `answer`/refute. (2) baseline-002: relevance via exact gold-PMID match under-counts valid alternatives → switched to a topical relevance judge. (3) baseline-002: faithfulness penalized citations to abstract-less papers, and decompose silently truncated long answers to zero claims → both fixed. The eval keeps catching its *own* flaws before they mislead us — that's the trust story.

## Immediate next: re-score baseline-002 under the hardened metrics
Re-score the existing baseline-002 records (no agent re-runs — retrieved sets are stored) to see corrected relevance/faithfulness, and journal it as `baseline-002-rescore`. Then (optionally) a fresh `baseline-003` once the dataset grows.

## What's left

- **Dataset growth (ongoing).** 12 → ~50 questions. **Top priority: add genuine no-evidence questions** to restore the abstention test (currently abstain=0). More questions → tighter, more trustworthy numbers.
- **Phase 5 — Formal judge validation.** Hand-label a sample of real verdicts; compute agreement / Cohen's κ per judge. Upgrades "8/8 trap tests" into a *measured* trust number — the credibility capstone for goal (1).
- **Phase 6 — Online eval.** Point the reference-free checks (validity, faithfulness) at *real* user conversations (read-only sample of the production table). Monitoring real usage, not just the benchmark.
- **Phase 7 — CI/CD (GitHub Actions).** Automate deploys: push → dev, manual button → prod (OIDC, no long-lived keys).
- **Phase 8 — The improvement loop (headline of goal 2).** Weekly job: run the benchmark → failure-attribution points at the weakest stage → an "improvement proposer" suggests a prompt/param change → apply → re-eval → **keep only if it beats the noise floor** → log in `JOURNAL.md`.

  **The auto-improvement agent layer (the "loop engineering" showcase).** A Claude layer *on top of* this eval that closes the loop semi-autonomously:
  1. **Review** — reads the latest run's results, the failure attribution, the per-claim/per-paper judge detail, and the `JOURNAL.md` history.
  2. **Propose** — drafts a concrete, single-variable change for the next run (e.g. "tighten the answering prompt to cite every claim"; "broaden the search query construction") tied to the weakest stage, with the expected metric effect.
  3. **Approve** — presents the proposal; the human simply says **"yes"** (human-in-the-loop gate — no silent self-editing).
  4. **Apply + re-eval + journal** — makes the change, runs `populate`→`check`→`report`, and writes the before/after to the journal, flagging whether the gain beat the noise floor.

  Runs on a weekly cadence. This is the part that demonstrates loop-engineering, not just measurement — the eval becomes the feedback signal an agent optimizes the product against, with a human approval step.

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
- **Uncited vs unverifiable** — an *uncited* claim (no citation at all) is an **agent** problem (must cite everything); tracked as `uncited_claim_rate`, to be fixed in the agent later. A citation is judged against the cited paper's **title + abstract** (a title-only record — warning letters, comments — is verified against its title). Only a cited record with **no title AND no abstract** is *unverifiable* — an **eval** limitation, not an agent fault; tracked as `unverifiable_citation_rate`.
- **Relevance is topical (option A)** — measures "did it retrieve a paper that addresses the question," not evidence quality. Quality-weighting (meta-analysis > small study) is a future option B.
