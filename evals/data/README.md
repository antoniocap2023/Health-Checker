# Eval dataset — `questions.jsonl`

The frozen gold set the **offline benchmark** scores the agent against. One JSON
object per line.

## Schema

| field | type | meaning |
|---|---|---|
| `question_id` | string `q\d+` | unique id |
| `question` | string | the user question, as asked |
| `type` | enum | `factual` \| `comparative` \| `thin_evidence` \| `adversarial` |
| `expected_behavior` | enum | `answer` (evidence exists) \| `abstain` (agent should refuse) |
| `gold_pmids` | string[] | acceptable evidence set (digit strings); `[]` when `abstain` |
| `subpoints` | string[] | facts a thorough answer must cover; `[]` when `abstain` |
| `split` | enum | `dev` (tune against) \| `test` (final numbers only) |
| `notes` | string | why these PMIDs are gold — the defensibility trail |

```json
{"question_id":"q001","question":"Does low-dose aspirin reduce preeclampsia risk in high-risk pregnancies?","type":"factual","expected_behavior":"answer","gold_pmids":["12345678"],"subpoints":["effect on incidence","timing/dose","safety"],"split":"dev","notes":"USPSTF-level meta-analytic evidence"}
```

## What "gold" means

`gold_pmids` is an **acceptable, sufficient-quality evidence set a good retrieval
*should* surface** — not exhaustive, not "the one right paper." Relevance is later
scored as overlap/recall of the agent's retrieved set against this gold.

## Curation methodology (why it's defensible)

- **Gold is chosen independently of the agent.** Candidate PMIDs come from a direct
  PubMed search via `evals/curate/find_candidates.py` (the raw `pubmed.py` data
  layer), **never** from running the agent and copying what it retrieved. Using the
  agent's own output as gold would make the relevance metric circular.
- **Every row is human-approved.** Drafts (question, candidate gold, sub-points) are
  reviewed and edited before being written here; `notes` records the rationale.
- **`abstain` rows are deliberate negatives.** `thin_evidence`/`adversarial`
  questions (weak or no PubMed support) test that the agent refuses instead of
  confabulating — the abstention check.
- **dev/test split.** Tune only against `dev`; report final numbers on `test` so the
  improvement loop can't overfit the benchmark (Goodhart).

## Validate

```bash
backend/venv/bin/python evals/validate_dataset.py             # offline schema/integrity
backend/venv/bin/python evals/validate_dataset.py --check-pmids   # also verify PMIDs resolve
```
