"""Programmatic keep/revert decision — codifies the hand-rule from the JOURNAL.

Given a baseline and a candidate report (from `run_benchmark`), decide whether the
candidate's change is worth keeping. The rule mirrors what we did by hand for
baseline-004: **keep iff the targeted metric improves by more than the noise band AND
no other headline stage regresses beyond its own noise band.** All headline metrics here
are higher-is-better. Decisions use the `dev` split only — `test` is never optimized.

    backend/venv/bin/python evals/compare_runs.py \
        --baseline evals/results/base.report.json --candidate evals/results/cand.report.json \
        --target-metric faithfulness_rate
"""
import _pathsetup  # noqa: F401

import argparse
import json

# The higher-is-better headline metrics that carry a per-repeat noise floor.
NOISE_METRICS = ["validity_ok_rate", "relevance_hit_rate", "relevance_precision",
                 "faithfulness_rate", "thoroughness_coverage"]

# Map a funnel stage (from attribution) to the metric a change targets.
STAGE_METRIC = {"validity": "validity_ok_rate", "relevance": "relevance_hit_rate",
                "faithfulness": "faithfulness_rate", "thoroughness": "thoroughness_coverage"}

DEFAULT_NOISE = 0.02  # fallback band when a run has <2 repeats (no measured stdev)


def _dev(report):
    return report["by_split"]["dev"]


def _noise(report, metric, default=DEFAULT_NOISE):
    nd = (_dev(report).get("noise") or {}).get(metric) or {}
    s = nd.get("stdev")
    return s if s is not None else default


def decide(baseline, candidate, target_metric, *, k=1.0, default_noise=DEFAULT_NOISE):
    """Return the keep/revert decision dict. `baseline`/`candidate` are report dicts."""
    b, c = _dev(baseline)["metrics"], _dev(candidate)["metrics"]

    tnoise = max(_noise(baseline, target_metric, default_noise), _noise(candidate, target_metric, default_noise))
    tb, tc = b.get(target_metric), c.get(target_metric)
    tdelta = (tc - tb) if (tb is not None and tc is not None) else None
    improved = tdelta is not None and tdelta > k * tnoise

    regressions = []
    for m in NOISE_METRICS:
        if m == target_metric:
            continue
        mb, mc = b.get(m), c.get(m)
        if mb is None or mc is None:
            continue
        noise = max(_noise(baseline, m, default_noise), _noise(candidate, m, default_noise))
        if (mc - mb) < -(k * noise):
            regressions.append({"metric": m, "delta": round(mc - mb, 3), "noise": round(noise, 3)})

    keep = bool(improved and not regressions)
    if tdelta is None:
        rationale = f"REVERT: {target_metric} not measurable in both runs."
    elif not improved:
        rationale = (f"REVERT: {target_metric} moved {tdelta:+.3f}, within the noise band "
                     f"(±{tnoise:.3f}·{k}).")
    elif regressions:
        regs = ", ".join(f"{r['metric']} {r['delta']:+.3f}" for r in regressions)
        rationale = (f"REVERT: {target_metric} improved {tdelta:+.3f} (> ±{tnoise:.3f}) but a stage "
                     f"regressed beyond noise: {regs}.")
    else:
        rationale = (f"KEEP: {target_metric} improved {tdelta:+.3f}, clearing the ±{tnoise:.3f} noise "
                     f"band, with no stage regressing beyond noise.")

    return {"keep": keep, "target_metric": target_metric,
            "target_delta": round(tdelta, 3) if tdelta is not None else None,
            "target_noise": round(tnoise, 3), "improved": improved,
            "regressions": regressions, "rationale": rationale}


def main():
    ap = argparse.ArgumentParser(description="Decide keep/revert between two benchmark reports.")
    ap.add_argument("--baseline", required=True, help="path to baseline .report.json")
    ap.add_argument("--candidate", required=True, help="path to candidate .report.json")
    ap.add_argument("--target-metric", required=True, choices=NOISE_METRICS)
    ap.add_argument("--k", type=float, default=1.0, help="noise-band multiplier (default 1.0)")
    args = ap.parse_args()

    def load(p):
        d = json.load(open(p))
        return d.get("report", d)  # accept {"report": ...} or a bare report

    verdict = decide(load(args.baseline), load(args.candidate), args.target_metric, k=args.k)
    print(json.dumps(verdict, indent=2))


if __name__ == "__main__":
    main()
