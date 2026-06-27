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


### Run baseline-002 — 2026-06-25

**Hypothesis / what changed since last run:** baseline (first scored run)

**Config:** model=claude-opus-4-8 · judge=claude-sonnet-4-6 · max_tool_calls=12 · concise_mode=True · N=3 · scope=dev+test · dataset=questions.jsonl @ 8c451b5f

**Results (mean ± spread):**

| stage | dev | test |
|---|---|---|
| Validity — fabricated-PMID rate | 0.00 | 0.00 |
| Validity — uncited-claim rate | 0.10 | 0.04 |
| Relevance — recall@k vs gold | 0.58 ± 0.12 | 0.58 ± 0.12 |
| Faithfulness — claim-level rate | 0.92 ± 0.02 | 0.92 ± 0.04 |
| Thoroughness — sub-point coverage | 0.97 ± 0.00 | 1.00 ± 0.00 |
| Abstention — false-answer rate | n/a | n/a |

**Judge validation:** 8/8 trap tests pass (see `JUDGE_TRUST.md`); formal agreement / κ is Phase 5.

**Observations:** Dev failures localize to: faithfulness (10), relevance (7), thoroughness (1); 6 ok. Faithfulness | retrieval-ok = 0.93.

**Decision:** baseline — establishes the noise floor; nothing to beat yet.

**Next iteration:** Largest failure bucket is **faithfulness** → tighten the answering/grounding prompt.


### Methodology change — metric hardening — 2026-06-25 (no agent change)

Inspecting baseline-002 showed its weak relevance/faithfulness numbers were **measurement bugs, not agent regressions** (same lesson as baseline-001's labeling bug). Three fixes, all to the eval (the agent is unchanged):

- **Relevance → topical judge.** Replaced exact gold-PMID overlap (which can never be exhaustive — q011 scored 0.0 despite a correct refutation because the agent found a *different* valid debunking paper) with a reference-free topical relevance judge. Headline is now **hit@k + precision**; the old gold-PMID overlap is kept as a **diagnostic** (`gold_recall`). Gold abstracts/sub-points calibrate the judge.
- **Faithfulness → judge against title+abstract; exclude only text-less citations.** The source for a cited claim is the paper's **title + abstract**. Some real PubMed records (warning letters, comments — q012 cited several) have a title but no abstract; the title often conveys the claim ("Ear candling warning"), so those are now **judged against the title** (supporting only what the title can carry — no assumed numbers/effect sizes). Only a cited paper with **no title AND no abstract** is `unverifiable` — it can't be machine-checked, so it doesn't count against faithfulness and is reported separately as **`unverifiable_citation_rate`**. A claim citing a PMID *not* retrieved (real fabrication) still counts as unsupported.
- **Decompose token fix.** The decomposer ran at 1024 max-tokens and silently truncated long answers to **zero claims** (q007 → 0 claims; at 4096 → 20). Raised `eval_decompose_max_tokens` to 4096, so long answers are scored instead of dropped.

**Distinction on record:** an **uncited** claim (no citation at all) is an **agent** problem — the agent must cite everything — and is tracked as `uncited_claim_rate` (fix in the agent later). An **unverifiable** citation (cited but the source has no abstract) is an **eval** limitation, not an agent fault.

**Judge trust:** re-ran `judge_trust.py` → **10/10** trap tests (added 2 relevance traps; existing faithfulness/thoroughness/abstention still pass). Tests green (33 evals + 63 backend).

**Next:** re-score the baseline-002 records under these v2 metrics (no agent re-runs) → log as `baseline-002-rescore` to show the corrected numbers side-by-side with baseline-002.


### Run baseline-002-rescore — 2026-06-25

**Hypothesis / what changed since last run:** re-score of baseline-002 under hardened metrics (topical relevance + title-fallback faithfulness); no agent change

**Config:** model=claude-opus-4-8 · judge=claude-sonnet-4-6 · max_tool_calls=12 · concise_mode=True · N=3 · scope=dev+test · dataset=questions.jsonl @ 8c451b5f

**Results (mean ± spread):**

| stage | dev | test |
|---|---|---|
| Validity — fabricated-PMID rate | 0.00 | 0.00 |
| Validity — uncited-claim rate | 0.13 | 0.02 |
| Relevance — hit@k (topical) | 0.96 ± 0.06 | 1.00 ± 0.00 |
| Relevance — precision | 0.78 ± 0.07 | 0.76 ± 0.01 |
| Relevance — gold recall (diagnostic) | 0.58 | 0.58 |
| Faithfulness — claim-level rate | 0.92 ± 0.01 | 0.90 ± 0.04 |
| Faithfulness — unverifiable-citation rate | 0.00 | 0.00 |
| Thoroughness — sub-point coverage | 0.97 ± 0.00 | 1.00 ± 0.00 |
| Abstention — false-answer rate | n/a | n/a |

**Judge validation:** 10/10 trap tests pass (see `JUDGE_TRUST.md`; +2 relevance traps); formal agreement / κ is Phase 5.

**Observations:** Same stored records as baseline-002, re-scored under the v2 metrics — so every delta is a measurement correction, not an agent change. Two corrections land as designed:
- **Relevance 0.58 → 0.96–1.00.** Measured topically (hit@k), the agent retrieves an on-topic paper almost every time; the old 0.58 was exact-gold-PMID overlap under-counting valid alternatives, and is preserved unchanged as the `gold_recall` diagnostic (0.58 ✓). Precision ~0.77 (≈¾ of retrieved papers are on-topic).
- **Faithfulness 0.92, unverifiable-rate 0.00.** The title-fallback judged the abstract-less citations (warning letters/comments) against their titles instead of dropping them; **q012 went 0.4 → 1.0** across all three repeats with zero unverifiable. Faithfulness held at 0.92 — no longer dragged down by an eval limitation.

Dev failure attribution is now faithfulness (14), relevance (1), 9 ok — and unlike baseline-002 this is a *trustworthy* signal: faithfulness is genuinely the weakest stage, not an artifact.

**Decision:** Confirms the metric-hardening hypothesis — baseline-002's weak relevance/faithfulness were **measurement artifacts, not agent faults**. This re-score is the corrected noise floor going forward; supersedes baseline-002's relevance/faithfulness numbers.

**Next iteration:** With the metrics trustworthy, **faithfulness (~0.92, claim-weighted) is the real first improvement target** → tighten the answering/grounding prompt to keep paraphrases tight to the cited source. In parallel: grow the dataset (restore genuine abstain cases) and do the Phase-5 formal judge validation (κ).


### Analysis — baseline-002-rescore faithfulness deep-dive — 2026-06-27 (no agent change)

Read every unsupported claim in `baseline-002-rescore` to understand the ~0.92 faithfulness number before acting on it. The misses split into **two very different buckets**, and investigating the larger one flipped its root cause.

**Bucket A — "bibliographic" misses are mostly an EVAL artifact, not an agent fault.** The single biggest contributor was q004 (rates 0.93 / 0.71 / 0.79), and most of its misses are the *same* error repeated: the agent wrote "a **2024** meta-analysis" for PMID 36103100, which the judge flagged as "published in **2022**." Investigation:
- The agent is **not inventing the year** — it faithfully echoed our own metadata. `backend/pubmed.py:_extract_year` stores `PubDate/Year` = the **journal-issue year (2024)**; the agent reported exactly that.
- A live efetch of 36103100 shows **both years are real**: `PubDate/Year = 2024` (issue, vol 38/4) vs `ArticleDate (Electronic) = 2022` (online-first). Classic epub-vs-issue ambiguity.
- The **judge said "2022" using outside knowledge** — its instruction is "judge ONLY against the provided source," but the abstract states neither year, so it anchored on the epub convention from prior knowledge. It went off-script (and picked the other valid convention).
- Same story for "~61,000 vs 61,589" — a reasonable rounding the judge nitpicked, contradicting our own recorded "lenient on fuzzy quantifiers" policy.
- **Conclusion:** these are date-convention + over-strict-rounding artifacts (eval/judge side), not the agent being sloppy. The recurring lesson again: the eval flags its own measurement flaws.

**Bucket B — "overreach / misattribution" are GENUINE agent grounding errors (the part that matters):**
- **q007: drug-class swap** — "*SGLT2* inhibitors reduced all-cause mortality vs DPP-4" when the source attributes that to *GLP-1 agonists*; plus certainty inflation ("high certainty" where the source says "moderate").
- **q006:** "NSAIDs carry cardiovascular and kidney risks" cited to a paper that only covers *GI* events (a risk added that isn't in the source). This one is **systematically low** (0.80 / 0.89 / 0.80) — a reproducible grounding bug.
- **q008:** "CBT and antidepressants are broadly similar" / "choice comes down to patient preference" attributed to three papers, none of which make that comparison — cross-source synthesis overreach.
- **q011:** specific alkaline-water/tumor-pH mechanism claims pinned to a general cancer-diet review.

**Stability.** q009/q010/q012 are 1.0 across all repeats; the faithfulness spread is concentrated in q004/q007/q008, where the overreach is *intermittent* (e.g. q007 = 0.95 / 0.75 / 0.95 — it overreaches on one run only). So a prompt fix targeting overreach should cut **variance**, not just lift the mean.

**Relevance precision (0.77) is dragged by the same hard questions.** q008 (21/33 retrieved off-topic) and q011 (16/18 off-topic) tank precision via keyword-collision retrieval (q011 pulled hydrotherapy / photoacidity-laser papers). Hit-rate stays ~0.97, so answers aren't starved — but **q008 and q011 are simultaneously the worst-precision AND the overreach-faithfulness questions**: noisy retrieval on broad/adversarial questions feeds loose citation.

**Implications for the metric.** Because every claim is pooled into one rate, a wrong publication year counts identically to a drug-class swap — so Bucket A (largely artifacts) dilutes and masks Bucket B (the dangerous part). Splitting faithfulness into a **clinical-assertion** sub-rate (the one to optimize/gate on) and a **bibliographic-detail** sub-rate would separate signal from noise.

**Where the agent prompt lives:** `backend/prompts.py` (`build_system_prompt`). The grounding rules exist ("answer ONLY from abstracts; cite every claim") but are too coarse to stop the overreach. The year issue is **not** prompt-fixable (the agent is faithful to our data) — that's eval/data-side.

**Decision / next:** Bucket A is not an agent bug; do **not** chase it with an agent prompt change. The real, prompt-fixable target is **Bucket B (overreach)** → `baseline-003`. Plus two eval-hygiene items: the clinical/bibliographic faithfulness split, and the year-convention/judge-outside-knowledge fix.
