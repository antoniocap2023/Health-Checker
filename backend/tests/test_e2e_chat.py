"""End-to-end test: the WHOLE stack, for real.

This exercises everything together with NO mocks: a real HTTP request to the
FastAPI app -> the real agent loop -> the real Claude API (which decides to call
the tool) -> the real PubMed search -> a grounded, cited answer streamed back.

It is the most realistic test we have, but also the slowest and the only one that
COSTS MONEY (it calls the Claude model configured in main.py). So it's gated:
    pytest --run-e2e
By default it's skipped. It additionally skips itself if no real Anthropic key is
available (e.g. CI without secrets), so it never fails for the wrong reason.

Because real model output is non-deterministic, we assert on STRUCTURE the strict
system prompt guarantees — that a search happened and the answer carries a PMID
citation — rather than on exact wording.
"""
import json
import os

import pytest
from fastapi.testclient import TestClient

import main

pytestmark = pytest.mark.e2e


@pytest.fixture
def real_anthropic_key():
    """Skip (don't fail) if only the dummy key is present."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key or key == "test-dummy-key-not-real":
        pytest.skip("no real ANTHROPIC_API_KEY available; set it to run the e2e test")


def _events(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_real_chat_searches_pubmed_and_cites_a_pmid(real_anthropic_key):
    client = TestClient(main.app)

    response = client.post(
        "/api/chat",
        json={"messages": [
            {"role": "user", "content": "Does taking aspirin help prevent heart attacks?"}
        ]},
    )

    assert response.status_code == 200
    events = _events(response)
    types = [e["type"] for e in events]

    # The model should have grounded its answer by searching PubMed at least once.
    assert "search" in types, f"expected a PubMed search to run; got event types {types}"

    # And every claim is supposed to be cited, so a PMID should appear in the text.
    answer = "".join(e["text"] for e in events if e["type"] == "text")
    assert "PMID" in answer, f"expected a PMID citation in the answer; got: {answer[:400]!r}"
