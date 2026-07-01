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


### Methodology change — faithfulness judge: clinical content only (Step 1) — 2026-06-27 (no agent change)

Acted on the deep-dive above. Tightened the **faithfulness judge prompt** (`evals/judges/faithfulness.py`) so it stops scoring citation metadata as clinical un-faithfulness:
- **Judge clinical content only** — do not mark a claim unsupported over bibliographic details (publication year, authors, exact participant counts). Online-first vs journal-issue years are both acceptable.
- **No outside knowledge** — judge only from the shown title+abstract (the judge had been anchoring on a paper's real epub year it "knew").
- **Grade meaning, not decimals** — reasonable rounding ("~61,000" for 61,589) is supported (aligns the judge with the already-recorded lenient-on-quantifiers policy it was violating).

The clinical/bibliographic *split* (separate sub-rates) was considered and **deferred** — the prompt patch removes the noise from the single number, which is enough until Phase-8 needs a hard gate.

**Judge trust:** added 2 policy traps (rounding → supported; year-not-in-abstract → supported); `judge_trust.py` now **12/12** (discrimination preserved — reversed/off-topic/distorted still fail). Tests green (39 evals).

**Re-score (free, same stored `baseline-002` answers, tightened judge) → `baseline-002-rescore2`:**

| stage | dev | test |
|---|---|---|
| Faithfulness — claim-level rate | **0.94** (was 0.92) | **0.95** (was 0.90) |
| Faithfulness — unverifiable rate | 0.00 | 0.00 |
| Relevance — hit@k | 0.96 | 1.00 |
| Relevance — precision | 0.76 | 0.76 |
| Thoroughness | 0.97 | 1.00 |

**Validation that it removed the *right* misses:** unsupported claims dropped **39 → 26**; the 13 removed are the year/rounding/metadata artifacts (q002 "2025 meta-analysis…15,800", q004 "2026 trial"/"2024 review", q003 "slightly lower"), while the genuine overreach is **retained** — q007's SGLT2-vs-GLP-1 drug-class swap, q008's cross-source CBT/antidepressant synthesis, q006's exercise safety-profile overreach. Dev attribution: faithfulness 14 → **11**. Caveat: not a perfectly controlled A/B (decompose also wiggles at temp 0 — relevance precision drifted ~0.015 with no relevance-judge change, a useful judge-noise read), so the structural 39→26 drop is firmer evidence than the +0.02 rate.

**This is the clean clinical floor for Step 2:** dev faithfulness **0.939** (claim-weighted), with the remaining failures concentrated in real overreach. Next: the agent-side anti-overreach prompt change (`backend/prompts.py`) → re-populate dev → `baseline-003`, keep only if it beats this floor + the ~±0.015–0.02 judge noise.


### Run baseline-003 — 2026-06-27

**Hypothesis / what changed since last run:** First real agent iteration. Added three anti-overreach grounding rules to `backend/prompts.py` (the agent's system prompt), targeting the overreach bucket from the deep-dive: (1) ground each claim in the *specific* paper cited for it; (2) don't combine multiple papers into a claim none of them makes; (3) preserve the source's strength of language (no certainty upgrades, association→causation, or subgroup over-generalization). Judge unchanged from Step 1. Re-populated **dev** (8 Q × N=3 = 24 Opus runs); scored under the tightened judge.

**Config:** model=claude-opus-4-8 · judge=claude-sonnet-4-6 (Step-1 prompt) · max_tool_calls=12 · concise_mode=True · N=3 · scope=dev · dataset=questions.jsonl @ 8c451b5f

**Results (dev, claim-weighted; vs floor = baseline-002-rescore2):**

| stage | floor | baseline-003 | Δ |
|---|---|---|---|
| Validity — fabricated | 0.00 | 0.00 | — |
| Relevance — hit@k | 0.96 | 1.00 | ~flat (retrieval re-ran) |
| Relevance — precision | 0.76 | 0.79 | ~flat |
| **Faithfulness — claim-level** | **0.939** | **0.963** | **+0.024** (≈ noise band) |
| **Thoroughness — coverage** | **0.969** | **0.896** | **−0.073** (real regression) |
| Uncited-claim rate | 0.12 | 0.10 | — |

Attribution: faithfulness 11 → **8** (better); thoroughness 0 → **4** (worse).

**Observations:** The change hit its target — **q007 (the SGLT2-vs-GLP-1 drug-class swap) rose 0.88 → 0.97**, and no question regressed on faithfulness. But it **over-corrected**: thoroughness fell on exactly two questions, q001 (1.00→0.75) and q011 (1.00→0.67), well outside thoroughness's historically ±0.00 spread. The dropped sub-points are diagnostic — q001 "safety in pregnancy" and **q011 "the claim lacks scientific/biological plausibility"** (the *debunking* point). The grounding rules made the agent too timid to state anything it couldn't pin to one explicit abstract, so it suppressed legitimate safety notes and myth-refutations — bad behavior for a health agent. Rule (2) ("don't combine papers") is the likely culprit.

**Decision:** **Do not keep as-is; do not fully revert.** A faithfulness gain (~at noise) bought at a clear thoroughness loss is not a win by our own criterion — but q007's fix is real and worth keeping. → **Refine** for `baseline-004`: keep rules (1) and (3); soften rule (2) so the agent may still synthesize and *must still address every aspect of the question* — when evidence is indirect or absent, say so explicitly (e.g. "no trial directly tested X") rather than omitting the sub-point.

**Next iteration:** `baseline-004` — refined prompt per above; re-populate dev; target faithfulness ≥ 0.96 **without** thoroughness dropping below ~0.96.


### Run baseline-004 — 2026-06-27  ✅ KEPT (first improvement to beat the noise floor)

**Hypothesis / what changed since last run:** Refined the baseline-003 prompt to keep the faithfulness win without the thoroughness regression. Kept rules (1) ground-each-claim-in-its-cited-paper and (3) preserve-strength-of-language; **softened** rule (2) from "do not combine papers" → "you may synthesize, but cite each paper for the part it supports and don't assert a comparison no single paper makes"; **added** a coverage guard: still address every aspect of the question — if evidence for an aspect (safety, biological plausibility) is indirect/weak/absent, say so explicitly rather than omitting it. Judge unchanged. Re-populated dev (24 Opus runs); scored via `--batch`.

**Config:** model=claude-opus-4-8 · judge=claude-sonnet-4-6 (Step-1 prompt) · max_tool_calls=12 · concise_mode=True · N=3 · scope=dev · dataset=questions.jsonl @ 8c451b5f · scoring=Batches API

**Results (dev, claim-weighted; floor = baseline-002-rescore2, prior = baseline-003):**

| stage | floor | b003 | **b004** | b004 vs floor |
|---|---|---|---|---|
| Faithfulness — claim-level | 0.938 | 0.961 | **0.965** | **+0.027** (clears ±0.015–0.02 noise) |
| Thoroughness — coverage | 0.969 | 0.896 | **0.948** | −0.021 (b003 regression recovered) |
| Relevance — hit@k | 0.96 | 1.00 | 0.92 | q011 retrieval noise (orthogonal) |
| Uncited-claim rate | 0.12 | 0.10 | 0.11 | ~flat |

**Observations:** The refinement achieved both goals. **q007 — the dangerous SGLT2-vs-GLP-1 drug-class swap — went 0.88 (floor) → 1.00 and held** from b003; q011 faithfulness also rose 0.82 → 0.93. Thoroughness recovered from b003's break: **q011's myth-debunking sub-point fully restored (0.67 → 1.00)** and q001 partially (0.75 → 0.83), lifting the dev mean 0.896 → 0.948. The only soft spot is q001 "safety in pregnancy" still at 0.83 (vs 1.00 floor) and the q011 relevance dip — the latter is the known keyword-collision retrieval on the hardest adversarial question (q011 r0/r2 retrieved water-therapy papers, 0 relevant), unaffected by this grounding change, and q011 still answered well (faith 0.93 / thoro 1.00) on those runs.

**Decision:** **KEEP.** Faithfulness beat the floor by +0.027 (outside the ~±0.015–0.02 judge-noise band) with the dangerous misattribution eliminated, and thoroughness returned to ~floor (within noise). A clean win by our criterion: faithfulness up, no stage left meaningfully worse. First agent change kept in the improvement loop.

**Caveats:** single N=3 dev run — the faithfulness gain is the firm result; the thoroughness "recovery to floor" is within noise and the q001 safety sub-point is still slightly down. Test split deliberately **not** scored (held out, no Goodhart) — score it once as a final confirmation when ready.

**Next iteration:** Two candidates: (a) close the residual q001 "safety in pregnancy" coverage gap; (b) the bigger structural lever — **query construction** to fix q011's keyword-collision retrieval (relevance precision/hit), the recurring weak spot. Also: grow the dataset (more questions tighten the noise floor and make small wins like this one easier to confirm).


### Dataset growth — 12 → 27 questions, abstain restored 0 → 6 — 2026-06-27

Grew the gold set to tighten the noise floor and **restore the abstention test** (dormant since baseline-001's myth-relabeling left abstain=0). Added **15 rows** (9 `answer` + 6 `abstain`); dataset is now **27** (dev/test = 18/9).

- **9 answer questions** across types — factual (folic acid→NTD, Mediterranean diet→CVD, HPV→cervical cancer, statins primary prevention), comparative (GLP-1 vs SGLT2 weight, antidepressant efficacy/acceptability, intermittent fasting vs continuous restriction), thin_evidence (cold-water immersion), adversarial-answer (vaccines→autism, a refutable myth). Gold curated **independently** via `evals/curate/expand_batch.py` (raw `pubmed.py`, not the agent) — Cochrane/USPSTF/landmark NMAs (e.g. Cipriani 2018 for antidepressants, Taylor 2014 for vaccine-autism).
- **6 genuine abstain questions** (the real gap) — folk-remedy claims (amethyst→eczema, banana peel→migraine, coconut-oil gargle→tinnitus, salt lamp→asthma, copper bracelet→insomnia, magnetic insoles→CFS). Each **independently verified to return ~0 PubMed results** — no studies, *not even debunking* — which is what separates a true `abstain` from an adversarial-`answer` myth like q011/q012 (those have debunking papers to cite). `gold_pmids: []`, `subpoints: []`.
- **Note on gold's role:** since the metric hardening, relevance is the topical *judge* and gold is only the `gold_recall` diagnostic + a schema requirement — so gold was kept light-but-defensible and the curation effort went into **subpoints** (they now feed both the relevance-judge rubric and the thoroughness checklist) and getting the abstain set right.
- Validated: `validate_dataset.py --check-pmids` → 27 rows, all gold PMIDs resolve, OK.

**Composition:** types={factual: 9, comparative: 6, thin_evidence: 3, adversarial: 9}, splits={dev: 18, test: 9}, abstain=6 (4 dev / 2 test).

**Next:** dropped triptan-vs-NSAID from this batch (no clean head-to-head gold). Future baselines (incl. the held-out test confirmation and `baseline-003+`) should re-populate against this larger set; the abstention false-answer rate is now measurable again. Continue toward ~50 to further tighten the floor.


### Run baseline-005 — 2026-06-27  (final baseline on the 27-question set, N=3)

**Hypothesis / what changed since last run:** No agent change vs baseline-004 — this is the final reference run on the **full 27-question set** (N=3, dev+test = 81 Opus runs) with the kept baseline-004 grounding prompt, scored under the Step-1 judge (batched). Purpose: one clean headline scorecard, and the first measurement of the **restored abstention test**.

**Config:** model=claude-opus-4-8 · judge=claude-sonnet-4-6 (Step-1 prompt) · max_tool_calls=12 · concise_mode=True · N=3 · scope=dev+test · dataset=questions.jsonl (27 Q) · scoring=Batches API

**Results (mean):**

| stage | dev | test |
|---|---|---|
| Validity — fabricated | 0.00 | 0.00 |
| Relevance — hit@k | 0.98 | 1.00 |
| Relevance — precision | 0.79 | 0.79 |
| Faithfulness — claim-level | 0.96 | 0.95 |
| Thoroughness — coverage | 0.96 | 0.98 |
| **Abstention — correct** | **0.25** | **0.17** |

Dev attribution: ok 25, faithfulness 15, abstention 9, thoroughness 4, relevance 1.

**Observations:** On **answerable** questions the agent is strong and stable across the larger set — the kept baseline-004 prompt holds (faithfulness 0.95–0.96, thoroughness 0.96–0.98, validity 1.0, no false refusals on any of the 21 answerable questions). The headline is the **restored abstention test, which scored 0.22 overall — but inspection shows this is mostly a measurement problem, not an agent failure** (the 4th time the eval has caught its own measurement flaw):
- On all 6 no-evidence questions the agent gives a truthful, calibrated *"No — there's no evidence for this; my search found nothing on-point,"* and never confabulates a cure.
- It is **not** abstaining via empty retrieval: it retrieves 2–9 tangential papers per abstain question (keyword-collision retrieval — e.g. halotherapy papers for "salt lamp asthma"), then correctly concludes none support the specific claim.
- The **abstention judge is inconsistent**: identical-style answers get different verdicts across a question's own 3 repeats (q023 True/True/False; q024 False/False/True; q027 True/False/False). That run-to-run flipping on the same input is the signature of an unreliable judge, and is what drags the score to ~0.22.
- Deeper definitional issue exposed: the abstention judge demands a *clean* "I can't answer," so a confident, correct "there's no evidence for this" is scored as "answered." For a health agent that calibrated "no" is arguably the *ideal* response — so the bar itself may be wrong for no-evidence questions.

**Decision:** Keep baseline-005 as the final reference scorecard. The answerable-question quality is confirmed at scale. The abstention number is **not** actioned as an agent fix — the evidence says the agent behaves well and the **abstention judge/label definition is the weak link**. This is the motivation for the Phase-5 κ validation (next): blind human labels will quantify the abstention judge's (un)reliability.

**Next:** (1) Cohen's κ judge validation — expect a low abstention κ confirming the above. (2) One closing change after reviewing all findings.


### Phase 5 — Cohen's κ judge validation — 2026-07-01

Hand-labeled a **blind** stratified sample of 39 `baseline-005` verdicts (verdict hidden) and scored agreement with `evals/kappa_harness.py`. Result: **overall κ=0.847** ("almost perfect"), per-judge faithfulness 0.80 / abstention 1.00 / relevance 0.80 / thoroughness 0.80 — all above the ≥0.6 trustworthy bar. Full table + reproduction in `JUDGE_TRUST.md`.

**This is the credibility capstone for goal (1):** the funnel's judges now have a *measured* judge–human agreement, not just trap-test discrimination.

**Two findings — one of which corrects the baseline-005 note above:**
- **Judges are conservative, not lenient.** All 3 disagreements were human=yes / judge=no — the judge withheld credit a human would give. So the reported scores slightly *under*-state agent quality (the safe direction).
- **The abstention judge is reliable (κ=1.0) — revising the baseline-005 hypothesis.** I had attributed the low abstention_correct (0.22) to an inconsistent judge; the κ data shows the judge matches the human per-item every time. So the low abstention number is really (a) the **agent** giving substantive "no, and here's what I found" answers instead of clean refusals, and (b) a **label-definition** question — should a calibrated "there's no evidence for this" count as a correct abstention? That reframes the one remaining weak number as an agent/dataset-definition issue, not a measurement bug.

**Next:** final review of all findings → one closing change to wrap up.


### Closing change — redefine abstention success (no-evidence questions) — 2026-07-01 (metric fix, no agent change)

The κ result said the abstention judge was reliable, so baseline-005's 0.22 abstention_correct was a **definition** problem, not a judge or agent bug: the old check demanded a *bare* refusal, marking the agent's calibrated *"No — there's no credible evidence for this"* as a failure. For a health agent that calibrated "no" is the *ideal* response. Fixed the definition (the 4th and final "the eval was mismeasuring" correction).

**Change (eval-side):** the abstention judge now returns a 3-way `outcome` — `declined` (bare refusal) / `no_evidence` (correct negative finding, no positive claim) / `affirmed` (substantive positive claim). Scoring:
- **abstain-expected question:** correct = outcome ≠ `affirmed` (declined *or* reported no evidence; only confabulating a positive claim is a false answer).
- **answer-expected question:** correct = outcome ≠ `declined` (a real answer, affirming or refuting; a bare refusal is a false refusal). Unchanged from before in practice.

**Re-score (existing baseline-005 answers, abstention judge only — no agent re-run):**

| metric | baseline-005 (old def) | **closing (new def)** |
|---|---|---|
| Abstention — correct | 0.22 | **1.00** (18/18) |

All 18 abstain records classified `no_evidence` — **0 `affirmed`, 0 `declined`**. So the agent never confabulates on no-evidence questions and always gives the calibrated negative finding; the old metric was penalizing correct behavior. Every other stage is unchanged (answers held fixed).

**Honest caveat:** this modifies the abstention judge (binary → 3-way). The κ=1.0 we measured validated the `declined`-vs-not detection, which is preserved; the new `no_evidence`-vs-`affirmed` split was not separately κ-validated (here it's unambiguous — 18/18 `no_evidence`, but a future run should re-trap/re-κ it). `judge_trust.py` abstention traps updated to the 3-way outcome (declined / no_evidence / affirmed); offline tests green (40).

**Decision / wrap-up:** KEEP. This closes the **current refinement portion** — metric hardening, judge trust (κ), and the abstention definition. Where it stands: a trustworthy measurement instrument (funnel + noise floor + κ=0.85 judges that err conservative) and a demonstrated improvement loop (baseline-003→004 kept a real dangerous-bug fix), with the agent shown strong on answerable questions and correctly calibrated on no-evidence ones.

**This is a checkpoint, not the end.** The roadmap continues from here — next up: **dataset growth toward ~50**, then **Phase 6 (online eval on real conversations)**, **Phase 7 (CI/CD)**, and **Phase 8 (automating the improvement loop we just ran by hand)**. Those are the planned next steps, not dropped scope.


### Phase 6 — Online eval built — 2026-07-01 (skipped further dataset growth per decision)

Built `evals/online_eval.py`: score a recent-N sample of **real** conversations from the **dev or prod** table (`--source dev|prod`, read-only) with the reference-free checks — the point being to monitor *actual usage*, not just the 27-question benchmark.

- **No backend change needed.** Exploration confirmed prod & dev already persist the full evidence record (`queries`/`retrieved`/`cited_pmids`) via the agent's `on_complete`, same shape as eval records. Two tables exist: `health-checker-conversations-{dev,prod}`.
- **Reference-free reuse.** Validity (deterministic), relevance (topical judge, `subpoints=[]`), faithfulness (`plan`+judge), and the abstention **outcome** all run without gold — reusing the exact offline judge `build/parse/assemble` helpers + the two-phase batch pipeline. The question is recovered from the conversation's first user turn (`scorecard._question_text`), not a gold row. New code is thin: `ConversationStore.scan_sample` (bounded read-only scan), `scorecard.assemble_online` (gold-free assembler), and the CLI.
- **Excluded (need gold):** thoroughness, relevance gold-recall, abstention correctness/false-answer.
- **Output:** reference-free metrics (validity_ok / fabricated_pmid / relevance hit+precision / faithfulness / unverifiable / uncited / abstention-outcome distribution) + **flagged conversations** (fabricated citations, unsupported claims) + a persisted `results/online-<env>-<ts>.json`.
- **Caveats on record:** recent-N without a `created_at` GSI means a bounded scan (`--max-scan`); evidence is last-write-wins per conversation, so scoring reflects the latest answered turn.

Tests: 44 evals (+4 online, fake store + fake client) + 63 backend green. Also ran it **live, read-only** against real tables: `prod` and the legacy `health-checker-conversations` each currently hold 1 conversation, both scored end-to-end (real Sonnet abstention: one `declined`, one `affirmed`; validity + persist worked). The dev table isn't deployed right now (dev uses `RemovalPolicy.DESTROY`); the retrieval→relevance→faithfulness path is covered by the offline test since the sparse live data had no retrieval-bearing turn. Decision: **skipped further dataset growth** (27 makes the point) and moved to Phase 6. **Next:** re-run once there's more real traffic (or redeploy dev + chat to seed it); then Phase 7/8.


### Online run online-prod-20260701 — 2026-07-01 (first full live online eval)

Deployed `HealthChecker-prod`, asked 3 medical questions through the live UI (one per conversation), then ran `online_eval --source prod` against the prod table — the **full online funnel on real traffic**, all three with retrieval.

**Results (reference-free, n=3, all with retrieval):**

| metric | value |
|---|---|
| validity_ok_rate | **1.00** (fabricated_pmid_rate 0.00) |
| faithfulness_rate | **1.00** (unverifiable 0.00, uncited 0.00) |
| relevance_hit_rate | 1.00 |
| relevance_precision | 0.72 |
| abstention_outcomes | `{no_evidence: 1, affirmed: 2}` |

**Observations:** On the safety-critical axes the agent scored **perfectly on live traffic** — zero fabricated citations, every claim faithful to its source, every claim cited. The two answerable questions (aspirin, SGLT2-vs-DPP4) came back `affirmed`; the **alkaline-water myth was correctly handled as `no_evidence`** (refuted without confabulating) — the exact behavior the closing abstention-definition change credits. **No conversations flagged.** The only soft number, relevance precision **0.72**, matches the offline benchmark (~0.79 dev) — retrieval pulls in some off-topic papers but doesn't hurt answers.

**Takeaway:** the online eval **agrees with the offline benchmark** — the measurement generalizes from the 27-question benchmark to real usage. That's the whole point of Phase 6. Report persisted at `evals/results/online-prod-20260701-141545.json` (gitignored). Prod is a retained table, so these conversations survive `cdk destroy` and can be re-scored anytime.


### Phase 7 + 8 — automated improve-and-ship loop built — 2026-07-01

Built the hands-off weekly system: the loop **improves** the agent and **ships** the win, with a PR as the human gate. This automates exactly what we did by hand (baseline-003→004).

**Phase 7 (CI/CD — the "ship"):** `infra/cicd_stack.py` adds a GitHub **OIDC provider + `github-actions-deploy` role** (assumes the `cdk-*` bootstrap roles; also eval-table RW for the loop) — no long-lived keys. `.github/workflows/deploy.yml`: **push→dev auto, prod behind a GH-Environment manual approval**; OIDC-assume → `cdk deploy` with QEMU/buildx for the ARM64 (t4g.micro) images. `cdk synth HealthChecker-cicd` verified.

**Phase 8 (the loop — the "improve"):**
- `evals/run_benchmark.py` — populate→check(batched)→aggregate in one call (+ `--dry-run` cost preflight; dev N=3 = 54 Opus runs).
- `evals/propose_change.py` — a **Claude proposer** (forced-tool structured output) reads the weakest stage from failure-attribution + JOURNAL history + the current prompt, and drafts **one** grounding-rule bullet targeting it (single variable, allowlisted lever).
- `evals/compare_runs.py` — **programmatic keep/revert**, codifying the baseline-004 hand-rule: keep iff the target metric beats `k·noise` **and** no other headline stage regresses beyond noise (would auto-revert a baseline-003-style thoroughness drop).
- `evals/improve_loop.py` — measure→propose→apply→re-eval (fresh subprocess so the edit takes effect)→decide→**open a PR** on a win (journal entry appended, branch pushed, `gh pr create`) / revert otherwise. `--no-pr` for cheap local smokes.
- `.github/workflows/weekly-improve.yml` — `workflow_dispatch` + a **commented weekly cron** (nothing paid runs until enabled); OIDC + GH secrets + PR permissions.

**Safety (defense in depth):** one variable per cycle from an allowlist; no-regression guard; `test` split never optimized; change reaches prod only via **human PR merge**; prod deploy a **second** manual approval.

**Verification:** `cdk synth` cicd stack OK; both workflows valid YAML; **52 eval tests** (+8: compare_runs + propose_change) + **63 backend** green; `run_benchmark --dry-run` and the `apply_rule` anchor confirmed. **Not yet live** — needs the one-time enablement (deploy cicd stack, set prod reviewers, add GH secrets) + one dispatched cycle.

**Status:** this completes the build of goal 2 (an agent that improves itself and ships) end-to-end, mirroring the manual loop. Remaining is operational enablement + a first live run.


### Phase 7 + 8 — first live runs on GitHub Actions — 2026-07-01

Enabled and exercised the pipeline live (deployed `HealthChecker-cicd`, added GH secrets):

**Phase 7 (CI/CD) — proven on both environments via OIDC (no long-lived keys):**
- `deploy-dev` ✅ (auto on push) and `deploy-prod` ✅ (manual `workflow_dispatch → prod`, the gate). New prod box at 34.194.241.128.
- Two CI-only bugs found + fixed on the first runs: (1) `cdk.json` runs `.venv/bin/python`, so CI must create `infra/.venv` (not a bare `pip install`); (2) the retained prod table vs a destroyed stack re-triggered the `AlreadyExists` conflict (cleared the orphaned table, redeployed). Dev never hits (2) — its table is `DESTROY`.

**Phase 8 (the loop) — first live cycle ran green end-to-end, and the safety guard worked:**
- Bug fixed first: the proposer used `settings.model` (Opus 4.8) through the forced-tool `judge()` helper, which sets `temperature=0` — **Opus 4.8 deprecates `temperature`** → 400. Fixed by running the proposer on the **judge model (Sonnet)**, which accepts it. (Judges were always Sonnet, so unaffected.)
- Live cycle (`weekly-improve`, dev N=2, limit=4): proposer targeted the weakest stage (**thoroughness**) and proposed a rule to enumerate every clinical dimension. Candidate re-eval → **REVERT**: thoroughness_coverage **+0.031** but faithfulness_rate **−0.023** (beyond noise). The **no-regression guard reverted it autonomously — no PR.** This is precisely the baseline-003 over-correction pattern (gain one stage, lose another), now caught by the automated comparator with no human involved.

**Takeaway:** every mechanism of goal 2 is proven live — measure → propose → apply → re-eval → **keep/revert with a no-regression guard** → PR-on-a-win → merge→deploy (merge is just a push to `main`, and push→dev-deploy is already green). A *winning* PR is win-dependent (this cycle correctly produced none); re-dispatching may yield one, since the proposer is non-deterministic. The weekly cron stays commented until explicitly enabled (cost control).
