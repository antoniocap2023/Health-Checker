"""Abstention — did the answer decline for lack of evidence, or make claims?

Headline check for thin/adversarial questions (which SHOULD be refused); also a
guard on answerable questions (which should NOT be refused).
"""
from judges.client import judge

_SYSTEM = """You classify a medical ANSWER as either an abstention or a substantive \
answer.

- abstained = true: the answer declines to answer because it could not find \
sufficient/credible evidence (e.g. "I couldn't find evidence on that in PubMed"), \
and does NOT assert a substantive medical conclusion.
- abstained = false: the answer makes substantive medical claims that answer the \
question (even if hedged or caveated).
Give a one-sentence reason."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "abstained": {"type": "boolean"},
        "reasoning": {"type": "string"},
    },
    "required": ["abstained", "reasoning"],
}


def score(client, answer):
    """Return {abstained:bool, reasoning:str} for the answer."""
    result = judge(client, _SYSTEM, f"ANSWER:\n{answer}", _SCHEMA)
    return {"abstained": bool(result.get("abstained")), "reasoning": result.get("reasoning", "")}
