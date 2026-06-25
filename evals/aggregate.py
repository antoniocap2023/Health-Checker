"""Aggregate per-record scorecards into a run report.

Pure, deterministic — no model, no network. Turns the pile of per-record cards from
check_run into: headline metrics per funnel stage, the noise floor (run-to-run spread
across the N repeats), conditional scoring (downstream metrics among records that
passed retrieval, so one failure doesn't smear across stages), and earliest-stage
failure attribution (where to aim the next fix). All computed per split (dev/test/all).
"""
from statistics import mean, pstdev

# Thoroughness is graded, not pass/fail; for ATTRIBUTION only we need a line. This is
# a reporting convention, not a deep truth — surfaced in the report so it's explicit.
THOROUGHNESS_PASS = 0.8

_NOISE_METRICS = (
    "validity_ok_rate", "relevance_hit_rate", "relevance_precision",
    "faithfulness_rate", "thoroughness_coverage",
)


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return mean(vals) if vals else None


def _answer(cards):
    return [c for c in cards if c.get("expected_behavior") == "answer"]


def _abstain(cards):
    return [c for c in cards if c.get("expected_behavior") == "abstain"]


def _metrics(cards):
    """Headline metrics over one split. Answer-row metrics + abstention over abstain rows."""
    ans = _answer(cards)
    abst = _abstain(cards)

    # Faithfulness is claim-weighted over VERIFIABLE cited claims (unverifiable —
    # cited paper has no abstract — is excluded and reported separately).
    verifiable = sum(c["faithfulness"]["n_verifiable"] for c in ans if c.get("faithfulness"))
    supported = sum(c["faithfulness"]["n_supported"] for c in ans if c.get("faithfulness"))

    return {
        "n_answer": len(ans),
        "n_abstain": len(abst),
        "validity_ok_rate": _mean([1.0 if c["validity"]["ok"] else 0.0 for c in cards]),
        "fabricated_pmid_rate": _mean([c["validity"]["fabricated_rate"] for c in cards]),
        # Relevance headline: topical (judge). Gold-overlap kept as a diagnostic.
        "relevance_hit_rate": _mean([1.0 if c["relevance"]["hit"] else 0.0 for c in ans if c.get("relevance")]),
        "relevance_precision": _mean([c["relevance"]["precision"] for c in ans if c.get("relevance")]),
        "relevance_gold_recall": _mean([c["relevance"]["gold_recall"] for c in ans
                                        if c.get("relevance") and c["relevance"].get("gold_recall") is not None]),
        "faithfulness_rate": (supported / verifiable) if verifiable else None,
        "unverifiable_citation_rate": _mean([c["faithfulness"]["unverifiable_rate"] for c in ans if c.get("faithfulness")]),
        "uncited_claim_rate": _mean([c["faithfulness"]["uncited_rate"] for c in ans if c.get("faithfulness")]),
        "thoroughness_coverage": _mean([c["thoroughness"]["coverage"] for c in ans if c.get("thoroughness")]),
        "abstention_correct_rate": _mean([1.0 if c["abstention"]["correct"] else 0.0 for c in abst]),
        "false_answer_rate": _mean([0.0 if c["abstention"]["correct"] else 1.0 for c in abst]),
    }


def _retrieval_ok(card):
    v, r = card.get("validity"), card.get("relevance")
    return bool(v and v.get("ok") and r and r.get("hit"))


def _conditional(cards):
    """Downstream metrics among answer records that passed retrieval (validity ok & hit)."""
    ok = [c for c in _answer(cards) if _retrieval_ok(c)]
    verifiable = sum(c["faithfulness"]["n_verifiable"] for c in ok if c.get("faithfulness"))
    supported = sum(c["faithfulness"]["n_supported"] for c in ok if c.get("faithfulness"))
    return {
        "n_retrieval_ok": len(ok),
        "faithfulness_rate_given_retrieval_ok": (supported / verifiable) if verifiable else None,
        "thoroughness_coverage_given_retrieval_ok": _mean(
            [c["thoroughness"]["coverage"] for c in ok if c.get("thoroughness")]
        ),
    }


def _earliest_failure(card):
    """The first funnel stage this record fails at, or 'ok'."""
    if card.get("expected_behavior") == "abstain":
        return "ok" if card["abstention"]["correct"] else "abstention"
    if not card["validity"]["ok"]:
        return "validity"
    rel = card.get("relevance")
    if rel and not rel["hit"]:
        return "relevance"
    f = card.get("faithfulness")
    if f and f["n_verifiable"] > 0 and (f["faithfulness_rate"] or 0) < 1.0:
        return "faithfulness"
    t = card.get("thoroughness")
    if t and t["coverage"] is not None and t["coverage"] < THOROUGHNESS_PASS:
        return "thoroughness"
    return "ok"


def _attribution(cards):
    buckets = {"ok": 0, "validity": 0, "relevance": 0, "faithfulness": 0,
               "thoroughness": 0, "abstention": 0}
    for c in cards:
        buckets[_earliest_failure(c)] += 1
    return buckets


def _noise(cards):
    """Run-to-run spread: aggregate each headline metric per repeat-slice, take stdev."""
    repeats = sorted({c.get("repeat", 0) for c in cards})
    noise = {}
    for m in _NOISE_METRICS:
        per_slice = []
        for r in repeats:
            slice_cards = [c for c in cards if c.get("repeat", 0) == r]
            val = _metrics(slice_cards).get(m)
            if val is not None:
                per_slice.append(val)
        # stdev needs >= 2 slices; pstdev of one value is 0 but not meaningful.
        noise[m] = {
            "mean": round(mean(per_slice), 4) if per_slice else None,
            "stdev": round(pstdev(per_slice), 4) if len(per_slice) >= 2 else None,
            "n_slices": len(per_slice),
        }
    return noise


def _split(cards, split):
    return cards if split == "all" else [c for c in cards if c.get("split") == split]


def aggregate(cards):
    """Return the full run report dict from a list of per-record scorecards."""
    report = {
        "n_records": len(cards),
        "n_questions": len({c.get("question_id") for c in cards}),
        "repeats": sorted({c.get("repeat", 0) for c in cards}),
        "thoroughness_pass_threshold": THOROUGHNESS_PASS,
        "weighting": "headline record-weighted (== question-weighted under balanced N); faithfulness claim-weighted",
        "by_split": {},
    }
    for split in ("dev", "test", "all"):
        sub = _split(cards, split)
        report["by_split"][split] = {
            "n_records": len(sub),
            "metrics": _metrics(sub),
            "conditional": _conditional(sub),
            "attribution": _attribution(sub),
            "noise": _noise(sub),
        }
    return report
