"""Judge logic — decompose/faithfulness/thoroughness/abstention — with a FAKE
judge client returning canned structured output. No real Claude.
"""
import _pathsetup  # noqa: F401  -- backend on path

from types import SimpleNamespace

from judges import abstention, decompose, faithfulness, thoroughness


class _FakeMsgs:
    """Pops canned tool inputs in call order and wraps them like the SDK response."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        inp = self.responses.pop(0)
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", name="record", input=inp)])


def _client(responses):
    return SimpleNamespace(messages=_FakeMsgs(responses))


def test_decompose_normalizes_pmids():
    client = _client([{"claims": [
        {"claim": "X reduces Y", "cited_pmids": ["111", 222]},
        {"claim": "meta note", "cited_pmids": []},
    ]}])
    claims = decompose.decompose(client, "answer text")
    assert claims[0]["cited_pmids"] == ["111", "222"]
    assert claims[1]["cited_pmids"] == []


def test_faithfulness_aggregates():
    record = {
        "messages": [{"role": "user", "content": "q"},
                     {"role": "assistant", "content": "c1 (PMID: 111). c2. c3 (PMID: 999)."}],
        "retrieved": [{"pmid": "111", "title": "T", "abstract": "A"}],
    }
    # decompose returns 3 claims (c1 cited+retrieved, c2 uncited, c3 cited+fabricated);
    # only c1 reaches the per-claim judge (c3's PMID isn't retrieved → short-circuit).
    client = _client([
        {"claims": [
            {"claim": "c1", "cited_pmids": ["111"]},
            {"claim": "c2", "cited_pmids": []},
            {"claim": "c3", "cited_pmids": ["999"]},
        ]},
        {"supported": True, "reasoning": "matches abstract"},
    ])
    r = faithfulness.score(client, record)
    assert r["n_claims"] == 3
    assert r["n_cited_claims"] == 2          # c1 + c3
    assert r["n_supported"] == 1             # c1 true; c3 fabricated → false
    assert r["faithfulness_rate"] == 0.5
    assert r["uncited_claims"] == ["c2"]
    assert round(r["uncited_rate"], 3) == 0.333
    # Only one per-claim judge call happened (c3 short-circuited).
    assert len(client.messages.calls) == 2   # decompose + 1 verdict


def test_thoroughness_coverage():
    client = _client([{"results": [
        {"index": 1, "covered": True}, {"index": 2, "covered": False}, {"index": 3, "covered": True},
    ]}])
    r = thoroughness.score(client, "answer", ["a", "b", "c"])
    assert r["coverage"] == 2 / 3
    assert [c["covered"] for c in r["covered"]] == [True, False, True]


def test_thoroughness_no_subpoints():
    r = thoroughness.score(_client([]), "answer", [])
    assert r == {"covered": [], "coverage": None}


def test_abstention_parses():
    assert abstention.score(_client([{"abstained": True, "reasoning": "no evidence"}]), "a")["abstained"] is True
    assert abstention.score(_client([{"abstained": False, "reasoning": "claims"}]), "a")["abstained"] is False
