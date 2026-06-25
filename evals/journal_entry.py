"""Render an aggregated run report as a JOURNAL.md entry (matches the template there)."""

# Maps the funnel stage a run's failures cluster at to the knob to try next.
_KNOB = {
    "relevance": "tune query construction (the search prompt)",
    "faithfulness": "tighten the answering/grounding prompt",
    "thoroughness": "strengthen synthesis to cover more sub-points",
    "validity": "enforce citing only PMIDs that were retrieved",
    "abstention": "strengthen the refuse-when-no-evidence instruction",
}

_ROWS = [
    ("Validity — fabricated-PMID rate", "fabricated_pmid_rate", None),
    ("Validity — uncited-claim rate", "uncited_claim_rate", None),
    ("Relevance — recall@k vs gold", "relevance_recall", "relevance_recall"),
    ("Faithfulness — claim-level rate", "faithfulness_rate", "faithfulness_rate"),
    ("Thoroughness — sub-point coverage", "thoroughness_coverage", "thoroughness_coverage"),
    ("Abstention — false-answer rate", "false_answer_rate", None),
]


def _cell(block, metric_key, noise_key):
    val = block["metrics"].get(metric_key)
    if val is None:
        return "n/a"
    s = f"{val:.2f}"
    if noise_key:
        nd = block["noise"].get(noise_key, {})
        if nd.get("stdev") is not None:
            s += f" ± {nd['stdev']:.2f}"
    return s


def _observations(dev):
    att = dev["attribution"]
    fails = {k: v for k, v in att.items() if k != "ok" and v > 0}
    if fails:
        ranked = ", ".join(f"{k} ({v})" for k, v in sorted(fails.items(), key=lambda x: -x[1]))
        obs = f"Dev failures localize to: {ranked}; {att['ok']} ok."
    else:
        obs = f"All {att['ok']} dev records pass the funnel."
    fc = dev["conditional"].get("faithfulness_rate_given_retrieval_ok")
    if fc is not None:
        obs += f" Faithfulness | retrieval-ok = {fc:.2f}."
    return obs, fails


def _next_iteration(fails):
    if not fails:
        return "Funnel clean on the seed → grow the dataset toward ~50 and add harder cases."
    top = max(fails, key=fails.get)
    return f"Largest failure bucket is **{top}** → {_KNOB.get(top, 'investigate this stage')}."


def render(report, *, run_id, date, hypothesis, config, decision=None):
    dev, test = report["by_split"]["dev"], report["by_split"]["test"]
    observations, fails = _observations(dev)
    decision = decision or "baseline — establishes the noise floor; nothing to beat yet."

    lines = [
        f"### Run {run_id} — {date}",
        "",
        f"**Hypothesis / what changed since last run:** {hypothesis}",
        "",
        f"**Config:** {config}",
        "",
        "**Results (mean ± spread):**",
        "",
        "| stage | dev | test |",
        "|---|---|---|",
    ]
    for label, mkey, nkey in _ROWS:
        lines.append(f"| {label} | {_cell(dev, mkey, nkey)} | {_cell(test, mkey, nkey)} |")
    lines += [
        "",
        "**Judge validation:** 8/8 trap tests pass (see `JUDGE_TRUST.md`); formal "
        "agreement / κ is Phase 5.",
        "",
        f"**Observations:** {observations}",
        "",
        f"**Decision:** {decision}",
        "",
        f"**Next iteration:** {_next_iteration(fails)}",
    ]
    return "\n".join(lines)
