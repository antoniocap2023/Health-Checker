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
| 5 | **Metric hardening** — relevance → topical hit@k + precision (judge; gold demoted to diagnostic); faithfulness title-fallback + excludes only text-less citations; decompose token-truncation fix; judges batchable (`--batch`) | ✅ (re-scored → `baseline-002-rescore`) |

Goal (1) is largely in hand: a working, defensible measurement instrument with a noise floor, dev/test split, and trusted judges.

**What the baselines taught us — three measurement bugs, not agent bugs:** (1) baseline-001: myths mislabeled `abstain` → relabeled to `answer`/refute. (2) baseline-002: relevance via exact gold-PMID match under-counts valid alternatives → switched to a topical relevance judge. (3) baseline-002: faithfulness penalized citations to abstract-less papers, and decompose silently truncated long answers to zero claims → both fixed. The eval keeps catching its *own* flaws before they mislead us — that's the trust story.

## Immediate next: ✅ done — re-scored as `baseline-002-rescore`
Re-scored the stored baseline-002 records under the hardened metrics (no agent re-runs). Result (see `JOURNAL.md`): relevance **0.58 → 0.96–1.00** topical hit@k (old 0.58 preserved as `gold_recall` diagnostic), faithfulness held at **0.92** with **unverifiable-rate 0.00** (q012 0.4 → 1.0 via the title fallback). Confirms baseline-002's weak numbers were measurement artifacts. Next genuine target is **faithfulness**; a fresh `baseline-003` once the dataset grows.

## What's left

- **Dataset growth (ongoing).** Now **27 questions** (was 12; dev/test = 18/9), **abstain restored to 6** (4 dev / 2 test) via genuine no-evidence questions — independently verified to return ~0 PubMed results (see `evals/curate/expand_batch.py`). Next: continue toward ~50 for a tighter noise floor.
- **Phase 5 — Formal judge validation.** ✅ **Done (2026-07-01).** Blind hand-labeled 39 `baseline-005` verdicts → **overall κ=0.85** (faithfulness 0.80 / abstention 1.00 / relevance 0.80 / thoroughness 0.80); judges err *conservative* (under-credit). See `JUDGE_TRUST.md`. Harness: `evals/kappa_harness.py`. (Re-validate the new 3-way abstention judge's `no_evidence`-vs-`affirmed` split on a future run.)
- **Phase 6 — Online eval.** ✅ **Built (2026-07-01).** `evals/online_eval.py` scores a recent-N sample of *real* conversations from either the **dev or prod** table (`--source dev|prod`, read-only) with the reference-free checks — validity, relevance (topical), faithfulness, and the abstention *outcome* — reusing the offline judge/batch machinery (no gold, no backend change; prod/dev already persist the evidence record). Reports + persists reference-free metrics and flags problem conversations (fabricated citations, unsupported claims). Reader: `ConversationStore.scan_sample`; gold-free assembler: `scorecard.assemble_online`. *Next:* run it against live dev/prod data; a `created_at` GSI would make recency cheaper.
- **Phase 7 — CI/CD (GitHub Actions).** ✅ **Built (2026-07-01, awaiting one-time enablement).** `infra/cicd_stack.py` (`HealthChecker-cicd`) provisions a GitHub **OIDC provider + deploy role** (no long-lived keys); `.github/workflows/deploy.yml` does **push→dev auto, prod behind a GH-Environment manual approval** (OIDC assume → `cdk deploy`; QEMU/buildx for the ARM64 images). *One-time human steps:* deploy the cicd stack from a laptop, set the `prod` Environment reviewers, add API keys as GH secrets. *Not yet live-tested* (needs those steps + a push).
- **Phase 8 — The improvement loop (headline of goal 2).** ✅ **Built (2026-07-01).** The hand-run loop is now automated: `evals/run_benchmark.py` (populate→check→aggregate in one call), `evals/propose_change.py` (a **Claude proposer** that drafts one single-variable grounding-rule change targeting the weakest stage), `evals/compare_runs.py` (a **programmatic keep/revert** comparator — target beats the noise floor AND no stage regresses), and `evals/improve_loop.py` (measure→propose→apply→re-eval→decide→**open a PR** on a win / revert otherwise). `.github/workflows/weekly-improve.yml` runs it on demand (`workflow_dispatch`) with an **optional weekly cron** (commented until enabled — no standing paid cron). **The PR is the human approval gate; merging ships via Phase 7** (dev auto, prod manual). *Next:* enable the cron + run one live cycle.

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

- **Batch the judges — ✅ built (`check_run --batch`).** The judge calls now run through the **Batches API** (50% off, async) via a two-phase pipeline (independent calls → faithfulness, which depends on decompose). Sequential stays the **default** (best for `--limit`/dev runs); `--batch` is opt-in and, since a batch can take up to ~1h, must be run in the **background**. Discounts only the (cheaper, Sonnet) judge side; the Opus agent loops in `populate` can't be batched. **Pays off with run *frequency*, not dataset size** — turn it on for the Phase-8 improvement loop / weekly cron, where cumulative judge volume is where 50% adds up.
- Cheaper levers available now: smaller `N` for routine re-baselines (N=1 for quick checks), dev-only runs during the improvement loop.

## Known gaps / decisions on record
- **abstain restored: 6** (4 dev / 2 test) as of the 2026-06-27 dataset growth (was 0 after the myth-relabeling) — the abstention check is live again.
- **Judge policies** (recorded in `JUDGE_TRUST.md`): faithfulness lenient on fuzzy quantifiers; abstention strict (hedged claim = answered).
- **Thin-evidence behavior** = `answer` with an explicit "evidence is limited" sub-point (no separate calibration check yet).
- **Uncited vs unverifiable** — an *uncited* claim (no citation at all) is an **agent** problem (must cite everything); tracked as `uncited_claim_rate`, to be fixed in the agent later. A citation is judged against the cited paper's **title + abstract** (a title-only record — warning letters, comments — is verified against its title). Only a cited record with **no title AND no abstract** is *unverifiable* — an **eval** limitation, not an agent fault; tracked as `unverifiable_citation_rate`.
- **Relevance is topical (option A)** — measures "did it retrieve a paper that addresses the question," not evidence quality. Quality-weighting (meta-analysis > small study) is a future option B.
