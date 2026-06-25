"""Shared judge call — get a validated structured object back from Claude.

Every judge (decompose/faithfulness/thoroughness/abstention) needs the same thing:
hand the model a system+user prompt and force it to answer in a fixed JSON shape.
We do that with a single tool plus `tool_choice` forcing it, so the model MUST
return the schema's fields (validated at the API layer) — no brittle text parsing.
Runs at temperature 0 to keep the judge itself low-noise (the agent's randomness is
what Phase 4 measures, not the judge's).
"""
import _pathsetup  # noqa: F401  -- backend on path + backend/.env loaded

from config import settings

_TOOL_NAME = "record"


def judge(client, system, user, schema, model=None, max_tokens=None):
    """Call the judge model and return the validated structured object (a dict).

    `schema` is a JSON Schema for the object we want back. The forced tool makes the
    model emit exactly that, returned as the tool_use block's input.
    """
    msg = client.messages.create(
        model=model or settings.eval_judge_model,
        max_tokens=max_tokens or settings.eval_judge_max_tokens,
        temperature=0,
        system=system,
        tools=[{"name": _TOOL_NAME, "description": "Return the result.", "input_schema": schema}],
        tool_choice={"type": "tool", "name": _TOOL_NAME},
        messages=[{"role": "user", "content": user}],
    )
    for block in msg.content:
        if getattr(block, "type", None) == "tool_use" and block.name == _TOOL_NAME:
            return block.input
    raise RuntimeError("judge did not return the forced tool call")
