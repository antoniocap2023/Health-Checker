"""Proposer — weakest-stage selection + parsing (no network; the Claude call is not made)."""
import _pathsetup  # noqa: F401

import propose_change


def _report(attribution, metrics=None):
    metrics = metrics or {"faithfulness_rate": 0.9, "relevance_hit_rate": 0.95,
                          "thoroughness_coverage": 0.97}
    return {"by_split": {"dev": {"attribution": attribution, "metrics": metrics}}}


def test_weakest_stage_picks_largest_targetable_bucket():
    r = _report({"ok": 6, "faithfulness": 5, "relevance": 1, "abstention": 3})
    # abstention isn't a prompt-addressable target metric → faithfulness (largest that is)
    assert propose_change.weakest_stage(r) == "faithfulness"


def test_weakest_stage_falls_back_to_lowest_metric_when_no_fails():
    r = _report({"ok": 10}, metrics={"faithfulness_rate": 0.99, "relevance_hit_rate": 0.80,
                                      "thoroughness_coverage": 0.97})
    assert propose_change.weakest_stage(r) == "relevance"


def test_parse_normalizes_and_maps_metric():
    p = propose_change.parse({"target_stage": "relevance", "rule_text": "  do X  ",
                              "hypothesis": " lifts hit@k "})
    assert p == {"target_stage": "relevance", "target_metric": "relevance_hit_rate",
                 "rule_text": "do X", "hypothesis": "lifts hit@k"}


def test_parse_defaults_unknown_stage():
    p = propose_change.parse({"target_stage": "bogus", "rule_text": "y"})
    assert p["target_stage"] == "faithfulness" and p["target_metric"] == "faithfulness_rate"
