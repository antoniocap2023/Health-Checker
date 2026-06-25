# CLAUDE.md

Guidance for Claude Code (and contributors) working in this repo.

## Project

PubMed-grounded health Q&A agent: **React** frontend + **FastAPI** backend driven
by Claude, with an answer grounded *only* in PubMed abstracts and cited by PMID.

```
backend/    FastAPI app — agent loop (agent.py), tools (tools.py), NCBI layer
            (pubmed.py), config (config.py), DynamoDB persistence (storage.py)
frontend/   React chat UI
infra/      AWS CDK (manual `cdk deploy` to dev/prod — no CI/CD yet)
evals/      Deterministic eval suite for the agent (see below)
docs/       eval-design.md — the eval methodology & plan
```

## The eval suite

A deterministic, defensible eval that scores the agent through a 4-stage funnel
(validity → relevance → faithfulness → thoroughness, plus abstention), built to
support an improvement loop. Design and plan live in `docs/eval-design.md`; the
gold dataset and curation rules in `evals/data/README.md`.

- Run scripts with the backend venv from the repo root, e.g.
  `backend/venv/bin/python evals/validate_dataset.py`.
- `evals/_pathsetup.py` puts `backend/` on the path **and loads `backend/.env`**,
  so eval scripts inherit the NCBI/Anthropic keys (NCBI cap respected at 9/sec).
- Eval runs persist to a **separate** DynamoDB table
  (`settings.eval_dynamodb_table_name`), never the production conversations table.

## Eval journal — log every run

**After every eval run, append an entry to [`evals/JOURNAL.md`](evals/JOURNAL.md)**
using the template there: the hypothesis/what changed, the config, the per-stage
results (mean ± spread), where it broke, whether the change beat the noise floor,
and the next thing to try. This log is the project's iteration story — keep it
current so the progression of improvements stays visible.

## Conventions

- NCBI traffic must stay under NCBI's cap. The limiter is **per-process**, so don't
  fan PubMed calls across multiple processes (e.g. `pytest -n`, or many one-shot
  scripts) — run them in a single process.
- Gold evidence for the eval is chosen **independently of the agent**
  (`evals/curate/find_candidates.py`), or the relevance metric becomes circular.
- Backend tests: `cd backend && venv/bin/python -m pytest` (unit, offline by
  default; `--run-integration` / `--run-e2e` hit the network / Claude).
