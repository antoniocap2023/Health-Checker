# Eval Journal

A running log of **every eval run** on the PubMed-grounded health agent: the
results, what they told us, and the change we tried next. Read top-to-bottom to
follow the iteration story — measure → diagnose → change one thing → re-measure →
keep it only if it beat the noise.

See [`../docs/eval-design.md`](../docs/eval-design.md) for the methodology and
[`data/README.md`](data/README.md) for the dataset.

## How to read an entry

Each run is scored through a 4-stage funnel that mirrors the agent's pipeline, so a
failure points at a specific knob:

| stage | measures | a failure here means fix… |
|---|---|---|
| Validity | cited PMIDs ⊆ retrieved (deterministic) | hallucinated citations |
| Relevance | retrieved vs gold PMIDs | query construction |
| Faithfulness | each claim accurate to its cited abstract | the answering/grounding prompt |
| Thoroughness | answer covers the gold sub-points | synthesis |
| Abstention | refuses on thin/adversarial questions | over-confidence prompt |

Numbers are reported **mean ± spread across N runs/question** (the noise floor). A
change "counts" only if its effect exceeds that spread. `dev` is what we tune
against; `test` numbers are reported but never optimized (no Goodhart).

---

## Entry template (copy for each run)

```
## Run <run_id> — YYYY-MM-DD

**Hypothesis / what changed since last run:** <the one thing we changed and why,
or "baseline" for the first run>

**Config:** model=<…> · prompt=<version/hash> · max_tool_calls=<…> ·
concise_mode=<…> · N=<runs/question> · scope=<dev | dev+test> ·
dataset=questions.jsonl @ <git sha>

**Results (mean ± spread):**

| stage | dev | test |
|---|---|---|
| Validity — fabricated-PMID rate | | |
| Validity — uncited-claim rate | | |
| Relevance — recall@k vs gold | | |
| Faithfulness — claim-level rate | | |
| Thoroughness — sub-point coverage | | |
| Abstention — false-answer rate | | |

**Judge validation:** <agreement / κ vs hand labels, when measured>

**Observations:** <where it broke; which stage owns the failures>

**Decision:** <did the change beat the noise floor? keep / revert>

**Next iteration:** <the knob we'll try next and the expected effect>
```

---

## Run history

### Setup — 2026-06-25 (no results yet)
- **Phase 0:** agent persists an evidence record (queries, retrieved abstracts,
  cited PMIDs) so runs are scoreable.
- **Phase 1:** 12-question gold seed (`data/questions.jsonl`), dev/test = 8/4,
  with 4 abstain cases; validator + independent candidate finder in place.
- **Next:** Phase 2 (populate the eval table + read records back), then Phase 3
  (the four checks) — the first scored run will be the **baseline** entry here.


### Run baseline-001 — 2026-06-25

**Hypothesis / what changed since last run:** baseline (first scored run)

**Config:** model=claude-opus-4-8 · judge=claude-sonnet-4-6 · max_tool_calls=12 · concise_mode=True · N=3 · scope=dev+test · dataset=questions.jsonl @ d862ab26

**Results (mean ± spread):**

| stage | dev | test |
|---|---|---|
| Validity — fabricated-PMID rate | 0.00 | 0.02 |
| Validity — uncited-claim rate | 0.06 | 0.07 |
| Relevance — recall@k vs gold | 0.70 ± 0.08 | 0.61 ± 0.16 |
| Faithfulness — claim-level rate | 0.95 ± 0.02 | 0.89 ± 0.06 |
| Thoroughness — sub-point coverage | 0.93 ± 0.02 | 0.94 ± 0.04 |
| Abstention — false-answer rate | 1.00 | 0.67 |

**Judge validation:** 8/8 trap tests pass (see `JUDGE_TRUST.md`); formal agreement / κ is Phase 5.

**Observations:** Dev attribution was abstention (9), faithfulness (6), relevance (2); 7 ok — but **inspecting the answers showed the abstention "failures" were mislabeled, not agent errors.** The adversarial questions (alkaline water, ear candling) have PubMed evidence debunking them, so the agent correctly *refuted* them with citations (e.g. q011 cited a cancer-diet review) — which our `abstain` label wrongly scored as a failure. The judge was right; the dataset was wrong. Genuine signals to act on later: relevance recall 0.70 (misses ~1/3 of gold) and faithfulness 0.95 | retrieval-ok.

**Decision:** baseline established (noise floor recorded). Its main value was surfacing a dataset-labeling bug, not an agent bug.

**Next iteration:** **Relabel the dataset** — refutable myths → `answer` (with debunking gold + refutation sub-points); reserve `abstain` for genuine no-evidence questions. Then re-baseline (`baseline-002`) and turn to relevance/faithfulness.
