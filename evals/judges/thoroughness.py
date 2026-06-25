"""Thoroughness — does the answer cover the gold sub-points?

Makes "thorough" deterministic by reducing it to a fixed checklist: for each gold
sub-point, did the answer address it? Coverage = fraction covered.
"""
from judges.client import judge

_SYSTEM = """You check whether a medical ANSWER addresses each item on a CHECKLIST \
of sub-points a thorough answer should cover.

- For each numbered sub-point, decide if the answer meaningfully addresses it \
(covered) or not. Partial-but-real coverage counts as covered; a passing mention \
that conveys the point is enough. Do not require exact wording.
- Judge only what the answer says."""

SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "1-based sub-point number"},
                    "covered": {"type": "boolean"},
                },
                "required": ["index", "covered"],
            },
        }
    },
    "required": ["results"],
}


def build(answer, subpoints):
    """The (system, user) prompt pair for the coverage check. Callers skip the call
    entirely when subpoints is empty (see parse / score)."""
    numbered = "\n".join(f"{i}. {sp}" for i, sp in enumerate(subpoints, 1))
    return _SYSTEM, f"ANSWER:\n{answer}\n\nCHECKLIST:\n{numbered}"


def parse(inp, subpoints):
    """Turn a thoroughness tool-result into {covered:[{subpoint,covered}], coverage}.

    Empty subpoints (or a None input) → {covered:[], coverage:None}; no call needed.
    """
    if not subpoints:
        return {"covered": [], "coverage": None}
    by_index = {r["index"]: bool(r["covered"]) for r in (inp or {}).get("results", [])}
    covered = [{"subpoint": sp, "covered": by_index.get(i, False)}
               for i, sp in enumerate(subpoints, 1)]
    n_cov = sum(1 for c in covered if c["covered"])
    return {"covered": covered, "coverage": n_cov / len(subpoints)}


def score(client, answer, subpoints):
    """Return {covered:[{subpoint,covered}], coverage} for the answer vs subpoints."""
    if not subpoints:
        return parse(None, subpoints)
    system, user = build(answer, subpoints)
    return parse(judge(client, system, user, SCHEMA), subpoints)
