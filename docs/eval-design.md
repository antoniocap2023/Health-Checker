# Eval Suite — Design & Plan

## The project

A **deterministic, defensible eval suite** for the PubMed-grounded health agent. Two goals, in order:

1. **Reliably measure** the agent's answer quality — in a way you can defend to a skeptical reviewer.
2. **Support an improvement loop** — change something → re-eval → know whether it actually got better. (And eventually automate the "change something" step.)

The real thing being showcased isn't the checks — it's that you can build an eval you **trust enough to optimize against**.

## What's being evaluated

The agent's own pipeline: **retrieve** (turn question → PubMed query → abstracts) → **ground** (answer only from abstracts, cite by PMID) → **synthesize** (cover the question). Optional `deep_research` adds a full-text read with **two faithfulness hops** (sub-agent extraction, then main-agent use).

## Two evals, two jobs

These are distinct and complementary — not competing.

- **Offline benchmark (curated gold set).** Fixed questions with gold PMIDs + sub-points. Reproducible. This is what you *optimize against*. Lives in its **own eval table**. The improvement loop **requires** this frozen set — real traffic moves every week, so it can't tell you whether a change helped or the questions just got easier.
- **Online eval (real conversations).** Scores what the system actually produced for real users — read **read-only** from the production conversations table, sampling a rolling window (e.g. last week). Realistic and scalable (you sample, never score everything). Real conversations have **no gold labels**, so only the reference-free checks apply.

| Check | Online (real convos) | Offline (curated) |
|---|---|---|
| 1. Validity (cited ⊆ retrieved) | ✅ | ✅ |
| 2. Faithfulness (claim vs cited abstract) | ✅ | ✅ |
| 3. Relevance **vs gold PMIDs** | ❌ no gold | ✅ |
| 4. Thoroughness **vs sub-points** | ❌ no gold | ✅ |
| Abstention | ⚠️ unlabeled | ✅ (labeled thin cases) |

Realism comes from the **agent** being real (both modes) and the **questions** being real (online mode) — **not** from sharing a storage table. Synthetic benchmark runs stay out of the production table so they never pollute real usage data or compete for its throughput.

## The core: a 4-stage funnel that mirrors the pipeline

| # | Stage | Question | Type | Failure points at |
|---|-------|----------|------|-------------------|
| 1 | **Citation validity** | Are cited PMIDs ones it actually retrieved? | Deterministic (set check) | hallucinated refs |
| 2 | **Retrieval relevance** | Does the source match the question? | Reference-based (needs gold PMIDs) | query construction |
| 3 | **Faithfulness** | Is each claim accurate to what the abstract says? | LLM-judge (reference-free) | grounding / answering prompt |
| 4 | **Thoroughness** | Did it address the whole question? | LLM-judge vs sub-point checklist | synthesis |

Operating on **different objects**: validity → cited set; relevance → retrieved set; faithfulness → individual claims.

**Hallucination is priority**, and it's mostly stages 1 + 3 — the *cheap* check (1, fabricated PMID) catches the rare failure; the *dangerous* one (3, misattribution: valid PMID, misread paper) needs the judge. Measured at the **claim level** (fraction of claims that fail), not per-answer.

The faithfulness bar: a claim is fine if it's **accurate enough to what the abstract says** — semantic fidelity, not verbatim. Same bar for deep research.

## Three rules that make it rigorous

- **Failure localization via earliest-stage attribution + conditional scoring** — a single root failure must not smear across three metrics. Report downstream numbers conditioned on upstream passing ("faithfulness, among answers that retrieved relevant evidence: X%"). This is also what makes the improvement loop actionable: each stage maps to a specific knob.
- **Held-out split (dev/test)** — tune against the dev set, report final numbers on a test set you don't touch. Without it, optimizing against the eval = Goodhart, and your numbers are meaningless.
- **No scope creep on quality** — four validated metrics beat ten unvalidated ones. Deliberately leaving out calibration / readability / cost-as-quality.

## Two cross-cutting additions (essential to the goals)

- **Judge validation (meta-eval)** — hand-label a sample, measure how often each judge agrees with you (agreement rate / κ). This is the spine of "I can reliably eval." Stages 3 and 4 (and relevance, if judged) each get validated separately.
- **Variance / consistency** — the agent is stochastic, so run each question N times and report numbers **with their spread**. This (a) is on-theme for a "deterministic eval" project and (b) gives you the **noise floor**: a measured improvement only counts if it exceeds run-to-run noise.

## Pinned explicitly so it doesn't fall between stages

- **Abstention / no-evidence case** — measured as a **false-answer rate on known-thin questions** (PubMed weak, or retrieval stubbed empty). The most demo-damaging failure; the best controlled experiment.
- **One operational guardrail** (cost/latency sanity) — only so the loop can't "improve" thoroughness by making it 5× slower.

## The dataset (offline benchmark)

~50 hand-curated questions, each tagged with:

- gold **PMIDs** (for relevance),
- a **sub-point checklist** (makes thoroughness deterministic),
- a **question type** — `factual | comparative | thin_evidence | adversarial`; thin/adversarial drive the abstention test.
- a **split** tag — `dev` (~30, optimize against) / `test` (~20, touch only for final numbers).

## Architecture: the conversations DB is the eval's source of record

Both eval modes read evidence from DynamoDB. Two enabling decisions:

- **Enrich persistence.** Today only `{role, content}` text is saved, which lacks the retrieved set every check needs. We extend what the agent persists into a full **evidence record**: the queries it issued, the articles it retrieved (PMID + abstract per search), and the cited PMIDs. Because `retrieved[]` carries the abstract text, faithfulness reads evidence straight from the record — no PubMed re-fetch. This applies to **both** tables, so real production conversations also become scoreable (and the UI could show sources).
- **Separate offline table; real table read-only.** Benchmark runs persist to a dedicated eval table, tagged `run_id` + `question_id`. Online eval reads the production conversations table read-only. An `is_eval` / `run_id` discriminator exists regardless, so "same table" buys no simplicity — only data-hygiene cost.

### The evidence record (persisted in both tables)
```
{ conversation_id, run_id, question_id,          # run_id/question_id only on benchmark runs
  messages: [...],                               # existing transcript
  queries: ["aspirin migraine prophylaxis", ...],
  retrieved: [{pmid, abstract, year, pub_types}, ...],   # union across searches
  cited_pmids: ["123", ...],                     # parsed from the answer (Layer A)
  created_at, updated_at }
```

### End-to-end flow (offline benchmark)
`populate` (run each dataset question through the real agent N×, pointed at the eval table) → `read` (load all records for a `run_id`) → `score` (join each record to its dataset row by `question_id`, run the checks) → `report`. The whole run is keyed by `run_id`; comparing two runs is how "did this change beat the noise floor?" works.

## How a claim is tied to its source (PMID)

The agent cites **inline**: `...reduces risk (PMID: 40123456).` Two layers:

- **Layer A — deterministic:** regex every `(PMID: \d+)`. Powers **Stage 1 (validity)** directly — cited set ⊆ retrieved set — with no ambiguity.
- **Layer B — claim ↔ evidence pairing (Stage 3):** an LLM decomposer splits the answer into atomic claims, grounded by the inline markers. Each claim is attached the PMID(s) cited in its sentence (fallback: nearest citation in the same paragraph).
  - **Support rule:** a claim is faithful if supported by **at least one** of its attached PMIDs.
  - A factual claim with **no** nearby PMID → flagged as an **uncited claim** (hallucination sub-type), not sent to the faithfulness judge.
  - The decomposer is itself a validated judge (hard cases: trailing citations covering several sentences; connective/hedge sentences that aren't claims).

## Deployment & automation (future — kept in mind now)

Current deploy is **manual AWS CDK** (`cdk deploy HealthChecker-{dev,prod}` from `infra/`); there is no CI/CD yet. The target shape:

- **CI/CD (GitHub Actions).** Push to `main` → run backend tests → (optionally the eval as a gate) → `cdk deploy HealthChecker-dev` automatically. Prod stays a **manual** `workflow_dispatch` button → `cdk deploy HealthChecker-prod`. AWS auth via an **OIDC role**, not long-lived keys.
- **Weekly auto-eval (cron).** A scheduled workflow runs the **offline benchmark** (regression number + improvement suggestions) **and** samples **last week's real conversations** (production health), posts the report (issue / artifact / Slack), and an **improvement proposer** drafts concrete change suggestions from the *localized* failures. A human reviews, applies, and triggers the manual prod deploy.

Design choices these force **now**: the eval must run **headless** (config-driven tables, keys from env, no interactive auth), and `run_id` is the first-class handle (local runs, the CI gate, and the weekly job are all "produce a `run_id`, score it, diff it against the last").

---

## Implementation plan

Build order produces something usable each phase, and lands the rigor pieces (judge validation, variance) before the automation that depends on them. Phases 6–8 are explicitly later.

### Phase 0 — Enrich persistence
- `backend/agent.py`: accumulate `queries` + `retrieved` (PMID + abstract) across the loop's tool calls; parse `cited_pmids` from the final answer; pass all to the persistence hook (`on_complete`).
- `backend/storage.py`: `save()` writes the new evidence-record fields (reused for both tables).
- `backend/config.py`: add the eval-table name. `backend/main.py`: thread the enriched fields into `on_complete`.

### Phase 1 — Dataset
- `evals/data/questions.jsonl`: `{ question_id, question, type, gold_pmids[], subpoints[], split, notes }`. Start ~10 to build the harness, grow to ~50.

### Phase 2 — Populate & read (offline)
- `evals/populate.py`: run each dataset question through the real agent `N` times (variance), pointed at the eval table, tagging `run_id` + `question_id`.
- `evals/store.py`: load all records for a `run_id` (reuse `ConversationStore` against the eval table).

### Phase 3 — The four checks (each consumes an evidence record + its dataset row)
- `evals/checks/validity.py` (deterministic): cited ⊆ retrieved; fabricated-PMID + uncited-claim rates.
- `evals/checks/relevance.py`: retrieved vs `gold_pmids` (precision/recall@k); optional judge.
- `evals/checks/faithfulness.py` + `evals/judges/decomposer.py`: Layer-B claims → per-(claim, cited-abstract) verdict → claim-level rate.
- `evals/checks/thoroughness.py`: answer vs `subpoints` → fraction covered.
- `evals/checks/abstention.py`: on thin/adversarial rows, did it correctly refuse → false-answer rate.

### Phase 4 — Scoring & report
- `evals/score.py`: earliest-stage attribution + conditional scoring; aggregate across the `N` runs → **mean ± spread** (noise floor).
- `evals/report.py`: JSON results + human-readable report. `evals/run_eval.py`: CLI orchestrating populate → read → score → report.

### Phase 5 — Judge validation (meta-eval)
- Hand-label a sample; compute **judge-vs-human agreement / κ** for faithfulness, thoroughness (and relevance) judges separately. Gates whether their numbers are trustworthy.

### Phase 6 (later) — Online eval over real conversations
- Read the production table read-only, sample a rolling window, run the **reference-free** subset (validity, faithfulness, reference-free relevance/completeness). Reuses Phase 3 checks.

### Phase 7 (later) — CI/CD (GitHub Actions)
- `deploy-dev.yml` (push → tests → `cdk deploy dev`), `deploy-prod.yml` (manual). OIDC role for AWS.

### Phase 8 (later) — Weekly automation + improvement loop
- Scheduled workflow: benchmark + sampled real traffic → report → **improvement proposer** maps localized failures to knobs (system prompt, query construction, retrieval params) → human applies → keep a change only if the gain **exceeds the noise floor** → final number reported once on the **held-out test** set → manual prod deploy.
