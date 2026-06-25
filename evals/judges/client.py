"""Shared judge call — get a validated structured object back from Claude.

Every judge (decompose/faithfulness/thoroughness/abstention) needs the same thing:
hand the model a system+user prompt and force it to answer in a fixed JSON shape.
We do that with a single tool plus `tool_choice` forcing it, so the model MUST
return the schema's fields (validated at the API layer) — no brittle text parsing.
Runs at temperature 0 to keep the judge itself low-noise (the agent's randomness is
what Phase 4 measures, not the judge's).
"""
import _pathsetup  # noqa: F401  -- backend on path + backend/.env loaded

import time

from config import settings

_TOOL_NAME = "record"


def _params(system, user, schema, model=None, max_tokens=None):
    """The shared Messages-API params for one forced-tool judge call.

    Used by both the sync path (`judge`) and the batch path (`build_request`), so the
    two never diverge on model / tool / temperature.
    """
    return {
        "model": model or settings.eval_judge_model,
        "max_tokens": max_tokens or settings.eval_judge_max_tokens,
        "temperature": 0,
        "system": system,
        "tools": [{"name": _TOOL_NAME, "description": "Return the result.", "input_schema": schema}],
        "tool_choice": {"type": "tool", "name": _TOOL_NAME},
        "messages": [{"role": "user", "content": user}],
    }


def _extract(content):
    """Pull the forced tool_use input out of a response's content blocks."""
    for block in content:
        if getattr(block, "type", None) == "tool_use" and block.name == _TOOL_NAME:
            return block.input
    return None


def judge(client, system, user, schema, model=None, max_tokens=None):
    """Sync: call the judge model and return the validated structured object (a dict)."""
    msg = client.messages.create(**_params(system, user, schema, model, max_tokens))
    inp = _extract(msg.content)
    if inp is None:
        raise RuntimeError("judge did not return the forced tool call")
    return inp


def build_request(custom_id, system, user, schema, model=None, max_tokens=None):
    """A single Batches-API request (same params as `judge`), tagged with custom_id."""
    return {"custom_id": custom_id, "params": _params(system, user, schema, model, max_tokens)}


def run_batch(client, requests, log=None, poll_seconds=20):
    """Submit one batch of judge requests, wait for it, return {custom_id: input|None}.

    Half-price vs. sync calls. Async — a batch can take up to ~1h, so callers should
    run this in the background. A failed/expired request maps to None (assembly then
    treats that judge as unavailable).
    """
    if not requests:
        return {}
    batch = client.messages.batches.create(requests=requests)
    while True:
        b = client.messages.batches.retrieve(batch.id)
        if b.processing_status == "ended":
            break
        if log:
            log.info("batch %s: %s", batch.id, getattr(b, "request_counts", ""))
        time.sleep(poll_seconds)
    out = {}
    for res in client.messages.batches.results(batch.id):
        out[res.custom_id] = _extract(res.result.message.content) if res.result.type == "succeeded" else None
    return out
