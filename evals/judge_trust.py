"""Judge trust harness — negative-control "trap" tests + gray-zone probes.

Before we trust a judge's numbers we must show it has a working FAILURE MODE: it can
say "no", not just rubber-stamp. Each judge gets:
  - TRAP cases: an input with a known-correct verdict -> PASS/FAIL.
  - GRAY-ZONE cases: a genuinely debatable input with no hard answer -> we print the
    verdict + reasoning for a human to agree/disagree with.

Self-contained (crafted sources, no DB dependency) and reproducible. Makes REAL judge
calls, so it is NOT in the default pytest suite — run it on demand:

    backend/venv/bin/python evals/judge_trust.py

This is the lightweight precursor to Phase 5's formal judge validation (agreement/κ
against a hand-labeled sample). It establishes discrimination, not gray-zone accuracy.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _pathsetup  # noqa: F401,E402  -- backend on path + .env

from anthropic import Anthropic  # noqa: E402

from judges import abstention, decompose, faithfulness, relevance_judge, thoroughness  # noqa: E402

# --- crafted, self-contained source for faithfulness ---
ABSTRACT = (
    "In a systematic review and meta-analysis of 45 randomized trials (20,909 women), "
    "low-dose aspirin initiated at or before 16 weeks of gestation significantly reduced "
    "preeclampsia (RR 0.57, 95% CI 0.43-0.75) and fetal growth restriction. Aspirin "
    "started after 16 weeks produced a smaller, non-significant reduction. A dose-response "
    "effect was observed, with higher doses (up to 150 mg/day) more effective."
)
BY_PMID = {"P1": {"pmid": "P1", "title": "Aspirin dose and preeclampsia prevention", "abstract": ABSTRACT}}

SUBPOINTS = ["effect on preeclampsia incidence", "role of aspirin dose",
             "timing of initiation (early pregnancy)", "safety in pregnancy"]
ANSWER_NO_SAFETY = (
    "Low-dose aspirin reduces the incidence of preeclampsia in high-risk pregnancies. "
    "The effect depends on dose, with higher doses (up to 150 mg/day) more effective. "
    "Timing matters: starting at or before 16 weeks of gestation produces the largest benefit."
)
ANSWER_FULL = ANSWER_NO_SAFETY + (
    " Low-dose aspirin is generally well tolerated in pregnancy, with no significant "
    "increase in maternal bleeding or adverse fetal outcomes."
)


def _hdr(title):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def main():
    client = Anthropic()
    passed = total = 0

    # ---------------- FAITHFULNESS ----------------
    _hdr("FAITHFULNESS — trap (claim vs the same real-style abstract)")
    faith_traps = [
        (True,  "TRUE      ", "Low-dose aspirin started before 16 weeks reduces the risk of preeclampsia."),
        (False, "REVERSED  ", "Low-dose aspirin increases the risk of preeclampsia."),
        (False, "OFF-TOPIC ", "Low-dose aspirin prevents gestational diabetes."),
        (False, "DISTORTED ", "Aspirin started after 16 weeks produces the largest reductions in preeclampsia."),
    ]
    for expected, label, claim in faith_traps:
        v = faithfulness._judge_claim(client, claim, [BY_PMID["P1"]])
        ok = v["supported"] == expected
        total += 1
        passed += ok
        print(f"[{'PASS' if ok else 'FAIL'}] supported={v['supported']!s:5} (want {expected!s:5}) {label} {claim}")
        print(f"        -> {v['reasoning'][:150]}")

    _hdr("FAITHFULNESS — gray zone (human review; no hard answer)")
    for label, claim in [
        ("ROUGHLY HALVES", "Low-dose aspirin roughly halves the risk of preeclampsia."),
        ("DOSE GENERALIZED", "Higher doses of aspirin are more effective at preventing preeclampsia."),
    ]:
        v = faithfulness._judge_claim(client, claim, [BY_PMID["P1"]])
        print(f"[supported={v['supported']}] {label}: {claim}")
        print(f"        -> {v['reasoning'][:200]}")

    # ---------------- RELEVANCE ----------------
    _hdr("RELEVANCE — trap (is a retrieved paper on-topic for the question?)")
    REL_Q = "Does low-dose aspirin reduce the risk of preeclampsia in high-risk pregnancies?"
    REL_SUB = ["effect on preeclampsia incidence", "timing and dose"]
    rel_traps = [
        (True,  "ON-TOPIC  ", {"pmid": "R1", "title": "Low-dose aspirin for prevention of preeclampsia: a meta-analysis",
                               "abstract": "Aspirin significantly reduced preeclampsia in high-risk women."}),
        (False, "OFF-TOPIC ", {"pmid": "R2", "title": "Statin therapy and LDL cholesterol reduction in adults",
                               "abstract": "Statins lowered LDL cholesterol across trials."}),
    ]
    for expected, label, art in rel_traps:
        v = relevance_judge._judge_paper(client, REL_Q, REL_SUB, art)
        ok = v["relevant"] == expected
        total += 1
        passed += ok
        print(f"[{'PASS' if ok else 'FAIL'}] relevant={v['relevant']!s:5} (want {expected!s:5}) {label} {art['title'][:55]}")
        print(f"        -> {v['reasoning'][:150]}")

    _hdr("RELEVANCE — gray zone (human review)")
    gray_art = {"pmid": "R3", "title": "Aspirin for primary prevention of cardiovascular disease",
                "abstract": "Low-dose aspirin and first cardiovascular events in adults."}
    v = relevance_judge._judge_paper(client, REL_Q, REL_SUB, gray_art)
    print(f"[relevant={v['relevant']}] SAME-DRUG-DIFFERENT-OUTCOME: {gray_art['title']}")
    print(f"        -> {v['reasoning'][:200]}")

    # ---------------- THOROUGHNESS ----------------
    _hdr("THOROUGHNESS — trap (answer omits 'safety'; expect only safety uncovered)")
    r = thoroughness.score(client, ANSWER_NO_SAFETY, SUBPOINTS)
    got = [c["covered"] for c in r["covered"]]
    want = [True, True, True, False]
    ok = got == want
    total += 1
    passed += ok
    print(f"[{'PASS' if ok else 'FAIL'}] covered={got} (want {want}) coverage={r['coverage']}")
    for c in r["covered"]:
        print(f"     [{'x' if c['covered'] else ' '}] {c['subpoint']}")

    _hdr("THOROUGHNESS — trap (full answer; expect all covered)")
    r = thoroughness.score(client, ANSWER_FULL, SUBPOINTS)
    got = [c["covered"] for c in r["covered"]]
    ok = got == [True, True, True, True]
    total += 1
    passed += ok
    print(f"[{'PASS' if ok else 'FAIL'}] covered={got} coverage={r['coverage']}")

    _hdr("THOROUGHNESS — gray zone (sub-point addressed only in passing)")
    answer_passing = ANSWER_NO_SAFETY + " Aspirin is widely used and generally considered acceptable."
    r = thoroughness.score(client, answer_passing, SUBPOINTS)
    print(f"[coverage={r['coverage']}] does a one-line 'generally acceptable' count as covering 'safety in pregnancy'?")
    for c in r["covered"]:
        print(f"     [{'x' if c['covered'] else ' '}] {c['subpoint']}")

    # ---------------- ABSTENTION ----------------
    _hdr("ABSTENTION — trap")
    abst_traps = [
        (True,  "REFUSAL   ", "I couldn't find evidence on that in PubMed, so I can't answer confidently."),
        (False, "CONFIDENT ", "Yes. Low-dose aspirin reduces preeclampsia risk in high-risk pregnancies (PMID: 27640943)."),
    ]
    for expected, label, ans in abst_traps:
        v = abstention.score(client, ans)
        ok = v["abstained"] == expected
        total += 1
        passed += ok
        print(f"[{'PASS' if ok else 'FAIL'}] abstained={v['abstained']!s:5} (want {expected!s:5}) {label} {ans[:70]}")
        print(f"        -> {v['reasoning'][:150]}")

    _hdr("ABSTENTION — gray zone (human review)")
    for label, ans in [
        ("HEDGED+TENTATIVE", "The evidence is limited and mixed, but a few small studies hint at a possible benefit."),
        ("REFUSE+ASIDE", "I found no high-quality trials, so I can't give a confident answer, though one small study reported a benefit."),
    ]:
        v = abstention.score(client, ans)
        print(f"[abstained={v['abstained']}] {label}: {ans}")
        print(f"        -> {v['reasoning'][:200]}")

    # ---------------- DECOMPOSE (sanity, human review) ----------------
    _hdr("DECOMPOSE — sanity (claims + PMID attachment; human review)")
    ans = ("I'll search PubMed for that. Low-dose aspirin reduces preeclampsia risk "
           "(PMID: 111). It also reduces fetal growth restriction (PMID: 222). Aspirin is cheap.")
    for c in decompose.decompose(client, ans):
        print(f"  - cited={c['cited_pmids']}  claim={c['claim']}")

    _hdr(f"TRAP TOTAL: {passed}/{total} passed")


if __name__ == "__main__":
    main()
