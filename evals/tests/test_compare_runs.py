"""Keep/revert comparator — pure decision logic on canned reports (no network)."""
import _pathsetup  # noqa: F401

import compare_runs


def _report(metrics, noise=None):
    noise = noise or {m: {"stdev": 0.02} for m in compare_runs.NOISE_METRICS}
    return {"by_split": {"dev": {"metrics": metrics, "noise": noise, "attribution": {}}}}


_BASE = {"validity_ok_rate": 1.0, "relevance_hit_rate": 0.96, "relevance_precision": 0.78,
         "faithfulness_rate": 0.90, "thoroughness_coverage": 0.97}


def test_keep_when_target_beats_noise_and_no_regression():
    cand = {**_BASE, "faithfulness_rate": 0.96}  # +0.06 vs ±0.02
    v = compare_runs.decide(_report(_BASE), _report(cand), "faithfulness_rate")
    assert v["keep"] is True and v["improved"] is True and not v["regressions"]
    assert v["target_delta"] == 0.06


def test_revert_when_target_move_within_noise():
    cand = {**_BASE, "faithfulness_rate": 0.905}  # +0.005, inside ±0.02
    v = compare_runs.decide(_report(_BASE), _report(cand), "faithfulness_rate")
    assert v["keep"] is False and v["improved"] is False


def test_revert_when_another_stage_regresses():
    # target improves, but thoroughness craters beyond its noise band → revert
    cand = {**_BASE, "faithfulness_rate": 0.96, "thoroughness_coverage": 0.90}
    v = compare_runs.decide(_report(_BASE), _report(cand), "faithfulness_rate")
    assert v["keep"] is False
    assert any(r["metric"] == "thoroughness_coverage" for r in v["regressions"])


def test_missing_stdev_falls_back_to_default_noise():
    noise = {m: {"stdev": None} for m in compare_runs.NOISE_METRICS}  # e.g. N=1 run
    cand = {**_BASE, "faithfulness_rate": 0.915}  # +0.015 < default 0.02 → not enough
    v = compare_runs.decide(_report(_BASE, noise), _report(cand, noise), "faithfulness_rate")
    assert v["target_noise"] == compare_runs.DEFAULT_NOISE and v["keep"] is False
