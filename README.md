# Health-Checker — a PubMed-grounded medical Q&A agent with a self-improving eval harness

A health question-answering agent that answers **only** from peer-reviewed PubMed
abstracts and cites every claim by PMID — wrapped in a **deterministic, defensible
evaluation suite** and an **automated improvement loop** that measures the agent, proposes
a change, keeps it only if it beats the noise floor, and ships it through a human-approved
PR.

The interesting part isn't the chatbot — it's the **measurement and the loop around it**:
how do you *trust* an LLM's answers enough to optimize them, and then let the system improve
itself safely?

---

## What it does

- **Grounded answers, cited by PMID.** A Claude agent (FastAPI backend + React frontend)
  searches PubMed, reads abstracts, and answers *only* from what it retrieved — every claim
  carries its `(PMID: …)`. On a question with no credible evidence it says so instead of
  confabulating.
- **A 4-stage evaluation funnel** that mirrors the agent's pipeline, so a failure points at a
  specific knob to fix:

  | stage | measures | a failure here means fix… |
  |---|---|---|
  | Validity | cited PMIDs ⊆ retrieved (deterministic) | hallucinated citations |
  | Relevance | did retrieval surface on-topic evidence (topical LLM judge) | query construction |
  | Faithfulness | is each claim accurate to its cited abstract (LLM judge) | the grounding prompt |
  | Thoroughness | does the answer cover the gold sub-points | synthesis |
  | Abstention | does it decline on no-evidence questions | over-confidence |

- **Trusted judges.** The LLM judges are validated with trap tests *and* a measured
  **Cohen's κ ≈ 0.85** against human labels (they err conservative — they under-credit, never
  over-credit). See [`evals/JUDGE_TRUST.md`](evals/JUDGE_TRUST.md).
- **A self-improving loop** (human-gated): a Claude "proposer" reads the failure attribution
  and drafts *one* single-variable change; it's re-evaluated and **kept only if it beats the
  noise floor with no other stage regressing**; a winning change opens a **PR** (the human
  approval gate) that, on merge, **auto-deploys** via GitHub Actions + AWS OIDC.

---

## Why it's engineered the way it is

A few decisions that define the project (the full story is in
[`evals/JOURNAL.md`](evals/JOURNAL.md), which logs every run and every decision):

- **Noise floor + dev/test split.** Every metric is reported as mean ± spread across repeats;
  a change "counts" only if it clears that spread. `dev` is tuned against; `test` is reported
  but never optimized (no Goodhart).
- **The eval keeps catching its *own* measurement flaws** — repeatedly, weak numbers turned
  out to be the *metric* mis-measuring, not the agent misbehaving (exact-gold-overlap relevance,
  abstract-less citations, an epub-vs-issue publication-year mismatch, an over-strict abstention
  definition). The recurring lesson: *fix the measurement to reflect reality before you optimize
  against it.*
- **Gold evidence is chosen independently of the agent**, or the relevance metric becomes
  circular.
- **The autonomous loop is safe by construction:** one variable per cycle from an allow-list,
  a no-regression guard, `test` never optimized, and nothing reaches production without a human
  merging the PR (prod deploy is a separate manual approval). In its first live run, the guard
  *autonomously reverted* a change that improved thoroughness but regressed faithfulness.

---

## Architecture

```
backend/    FastAPI app — agent loop (agent.py), tools (tools.py), NCBI layer
            (pubmed.py), config, DynamoDB persistence (storage.py)
frontend/   React chat UI
infra/      AWS CDK — one stack, deployed per-env (dev/prod) to a tiny EC2 box;
            + a CI/CD trust stack (GitHub OIDC deploy role)
evals/      the evaluation suite: gold dataset, the four checks, LLM judges,
            aggregation, judge-trust (κ), online eval, and the improvement loop
docs/       eval-design.md — the methodology & rationale
.github/    deploy (push→dev, manual→prod) + weekly self-improvement workflows
```

## The evaluation suite (the heart of the project)

- **Gold dataset** — curated PubMed-grounded questions (factual / comparative / thin-evidence /
  adversarial + genuine no-evidence abstain cases), with gold evidence chosen independently.
- **Offline benchmark** — `populate` (run the agent) → `check` (score the four stages) →
  `aggregate` (metrics, noise floor, earliest-stage failure attribution) → journal.
- **Judge trust** — trap tests + Cohen's κ validation ([`evals/kappa_harness.py`](evals/kappa_harness.py)).
- **Online eval** — the reference-free checks pointed at *real* production conversations
  (read-only), to confirm the benchmark generalizes to live usage.
- **The improvement loop** — `run_benchmark` → `propose_change` (Claude) → `compare_runs`
  (keep/revert) → `improve_loop` (opens the PR).

Read [`docs/eval-design.md`](docs/eval-design.md) for the *why*, and
[`evals/ROADMAP.md`](evals/ROADMAP.md) for status and the phased build.

---

## Quickstart

**Run the app locally** (Docker):
```bash
cp backend/.env.example backend/.env     # add your ANTHROPIC_API_KEY (+ optional NCBI_API_KEY)
docker compose up --build                # frontend → http://localhost:8080
```

**Run without Docker** (two terminals):
```bash
cd backend && python -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/uvicorn main:app --reload       # backend on :8000
cd frontend && npm install && npm run dev # frontend dev server
```

**Tests** — three tiers, fastest/cheapest first (integration/e2e are skipped by default):
```bash
cd backend && venv/bin/python -m pytest           # unit: clock/network/Claude all faked (~1s)
venv/bin/python -m pytest --run-integration       # + real PubMed/NCBI calls
venv/bin/python -m pytest --run-e2e               # + one real Claude call (spends a few cents)
backend/venv/bin/python -m pytest evals/tests     # the eval-suite tests
```
> All PubMed traffic flows through one shared 9 req/sec limiter (under NCBI's cap), so run the
> integration/e2e tests in a **single process** (no `pytest -n`) or workers can collectively exceed it.

**Run the evaluation:**
```bash
backend/venv/bin/python evals/validate_dataset.py                       # validate the gold set
backend/venv/bin/python evals/run_benchmark.py --run-id demo --split dev -n 3
```

## Tech stack
Python · FastAPI · Anthropic Claude (agentic tool use + LLM-as-judge) · NCBI E-utilities
(PubMed) · React · AWS (DynamoDB, EC2, CDK, IAM/OIDC) · Docker · GitHub Actions.

## License
[MIT](LICENSE).
