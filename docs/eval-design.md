# Eval Suite — Design

## The project

A **deterministic, defensible eval suite** for the PubMed-grounded health agent. Two goals, in order:

1. **Reliably measure** the agent's answer quality — in a way you can defend to a skeptical reviewer.
2. **Support an improvement loop** — change something → re-eval → know whether it actually got better. (And eventually automate the "change something" step.)

The real thing being showcased isn't the checks — it's that you can build an eval you **trust enough to optimize against**.

## What's being evaluated

The agent's own pipeline: **retrieve** (turn question → PubMed query → abstracts) → **ground** (answer only from abstracts, cite by PMID) → **synthesize** (cover the question). Optional `deep_research` adds a full-text read with **two faithfulness hops** (sub-agent extraction, then main-agent use).

## The core: a 4-stage funnel that mirrors that pipeline

| # | Stage | Question | Type | Failure points at |
|---|-------|----------|------|-------------------|
| 1 | **Citation validity** | Are cited PMIDs ones it actually retrieved? | Deterministic (set check) | hallucinated refs |
| 2 | **Retrieval relevance** | Does the source match the question? | Reference-based (needs gold PMIDs) | query construction |
| 3 | **Faithfulness** | Is each claim accurate to what the abstract says? | LLM-judge (reference-free) | grounding / answering prompt |
| 4 | **Thoroughness** | Did it address the whole question? | LLM-judge vs sub-point checklist | synthesis |

Operating on **different objects**: validity → cited set; relevance → retrieved set; faithfulness → individual claims.

**Hallucination is priority**, and it's mostly stages 1 + 3 — with the insight that the *cheap* check (1, fabricated PMID) catches the rare failure, and the *dangerous* one (3, misattribution: valid PMID, misread paper) needs the judge. Measured at the **claim level** (fraction of claims that fail), not per-answer.

The faithfulness bar: a claim is fine if it's **accurate enough to what the abstract says** — semantic fidelity, not verbatim. Same bar for deep research.

## Three rules that make it rigorous

- **Failure localization via earliest-stage attribution + conditional scoring** — a single root failure must not smear across three metrics. Report downstream numbers conditioned on upstream passing ("faithfulness, among answers that retrieved relevant evidence: X%"). This is also what makes the improvement loop actionable: each stage maps to a specific knob.
- **Held-out split (dev/test)** — tune against the dev set, report final numbers on a test set you don't touch. Without it, optimizing against the eval = Goodhart, and your numbers are meaningless.
- **No scope creep on quality** — four validated metrics beat ten unvalidated ones. Deliberately leaving out calibration / readability / cost-as-quality.

## Two cross-cutting additions (off the quality axis, but essential to the goals)

- **Judge validation (meta-eval)** — hand-label a sample, measure how often each judge agrees with you (agreement rate / κ). This is the spine of "I can reliably eval." Stages 3 and 4 (and relevance, if judged) each get validated separately.
- **Variance / consistency** — the agent is stochastic, so run each question N times and report numbers **with their spread**. This (a) is on-theme for a "deterministic eval" project and (b) gives you the **noise floor**: a measured improvement only counts if it exceeds run-to-run noise. Without it the improvement loop is built on sand.

## Pinned explicitly so it doesn't fall between stages

- **Abstention / no-evidence case** — measured as a **false-answer rate on known-thin questions** (questions where PubMed is weak, or retrieval stubbed empty). The most demo-damaging failure; the best controlled experiment.
- **One operational guardrail** (cost/latency sanity) — only so the loop can't "improve" thoroughness by making it 5× slower.

## The dataset

~50 hand-curated questions, each tagged with:

- gold **PMIDs** (for relevance),
- a **sub-point checklist** (makes thoroughness deterministic),
- a **question type** — including deliberate **adversarial / thin** cases for the abstention test.

---

## Deployment context (so the eval's place is clear)

Deployment is **manual AWS CDK** (`cdk deploy HealthChecker-{dev,prod}` from `infra/`), **not** GitHub-driven — there is no CI/CD. Pushing to GitHub deploys nothing. Evals are therefore **decoupled from deploy**: they run locally (or later in CI) against the agent code directly and never touch dev/prod. Push-to-deploy (a GitHub Action running `cdk deploy`) is a possible future add, explicitly out of scope here.

## How a claim is tied to its source (PMID)

The agent is prompted to cite **inline**: `...reduces risk (PMID: 40123456).` So an answer is sentences with `(PMID: …)` markers. Two layers:

- **Layer A — deterministic:** regex every `(PMID: \d+)`. Powers **Stage 1 (validity)** directly — cited set ⊆ retrieved set — with no ambiguity.
- **Layer B — claim ↔ evidence pairing (for Stage 3):** an LLM decomposer splits the answer into atomic claims, grounded by the inline markers. Each claim is attached the PMID(s) cited in its sentence (fallback: nearest citation in the same paragraph).
  - **Support rule:** a claim is faithful if supported by **at least one** of its attached PMIDs.
  - A factual claim with **no** nearby PMID → flagged as an **uncited claim** (hallucination sub-type), not sent to the faithfulness judge.
  - The decomposer is itself a validated judge (hard cases: trailing citations covering several sentences; connective/hedge sentences that aren't claims).

---

## Architecture: the conversations DB is the eval's source of record

The eval reads from the conversations database, scores what it finds, and returns a result. Two decisions make that work:

- **Enrich persistence.** Today only `{role, content}` text is saved, which lacks the retrieved set every check needs. We extend what the agent persists into a full **evidence record**: the queries it issued, the articles it retrieved (PMID + abstract per search), and the cited PMIDs — alongside the existing transcript. Backend change in `agent.py` (accumulate the trace through the loop) + `storage.py` (new fields). Side benefit: the UI could show real sources.
- **Separate eval table.** Eval runs persist to a dedicated table (config-driven `dynamodb_table_name`, e.g. `health-checker-eval-conversations`), tagged with a `run_id` and the dataset `question_id`. Synthetic eval data never mixes with real user conversations.

**End-to-end flow:** `populate` (run curated questions through the real agent → enriched records land in the eval table) → `read` (pull all records for a `run_id`) → `score` (join each record to the dataset by `question_id`, run the checks) → `report`.

### The evidence record (persisted to the eval table)
```
{ conversation_id, run_id, question_id,
  messages: [...],                       # existing transcript
  queries: ["aspirin migraine prophylaxis", ...],
  retrieved: [{pmid, abstract, pub_types, year}, ...],   # union across searches
  cited_pmids: ["123", ...],             # parsed from the answer (Layer A)
  created_at, updated_at }
```

## Implementation plan

Build order produces something usable each phase, and lands the rigor pieces (judge validation, variance) before the improvement loop that depends on them.

### Phase 0 — Enrich persistence
- `agent.py`: accumulate `queries` + `retrieved` across the loop's tool calls; hand them to the persistence hook.
- `storage.py` + config: new evidence-record fields; eval-table name via config.

### Phase 1 — Dataset
- `evals/data/questions.jsonl`: `{ question_id, question, type, gold_pmids[], subpoints[], notes }`.
- `type ∈ {factual, comparative, thin_evidence, adversarial}`; thin/adversarial drive the abstention test.
- **dev/test split** by tag (~30 dev / ~20 test). Start ~10 to build the harness, grow to ~50.

### Phase 2 — Populate & read
- `populate`: run each dataset question through the real agent `N` times (variance), pointed at the eval table, tagging each record with `run_id` + `question_id`.
- `read`: load all records for a `run_id` from DynamoDB — this is the eval's input.

### Phase 3 — The four checks (each consumes an evidence record + its dataset row)
1. **Validity** (deterministic): cited ⊆ retrieved; fabricated-PMID rate + uncited-claim rate.
2. **Relevance** (reference-based): retrieved vs `gold_pmids` (precision/recall@k); optional judge.
3. **Faithfulness** (judge): Layer-B claim decomposition → per-(claim, cited-abstract) support verdict → claim-level faithfulness rate.
4. **Thoroughness** (judge): answer vs `subpoints` checklist → fraction covered.
- **Abstention check** on thin/adversarial items: did it correctly refuse → false-answer rate.

### Phase 4 — Scoring & report
- **Earliest-stage attribution + conditional scoring** (downstream metrics conditioned on upstream passing).
- Aggregate across the `N` runs per question → **mean ± spread** (the noise floor).
- Emit a JSON results file + a human-readable report.

### Phase 5 — Judge validation (meta-eval)
- Hand-label a sample; compute **judge-vs-human agreement / κ** for the faithfulness, thoroughness (and relevance) judges separately. Gates whether their numbers are trustworthy.

### Phase 6 — Improvement loop (later)
- Registry of **knobs** (system prompt, query construction, retrieval params), each mapped to the stage it targets.
- Change a knob → re-eval on **dev** → keep it only if the gain **exceeds the noise floor** → final number reported once on the **held-out test** set.
- Eventually automate the "propose a change" step.

