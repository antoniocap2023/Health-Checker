"""Judge logic — decompose/faithfulness/thoroughness/abstention — with a FAKE
judge client returning canned structured output. No real Claude.
"""
import _pathsetup  # noqa: F401  -- backend on path

from types import SimpleNamespace

from judges import abstention, decompose, faithfulness, relevance_judge, thoroughness


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


def test_faithfulness_buckets_verifiable_unverifiable_fabricated():
    record = {
        "messages": [{"role": "user", "content": "q"},
                     {"role": "assistant",
                      "content": "c1 (PMID: 111). c2. c3 (PMID: 999). c4 (PMID: 333)."}],
        "retrieved": [
            {"pmid": "111", "title": "T", "abstract": "A"},   # has abstract -> verifiable
            {"pmid": "333", "title": "warning", "abstract": ""},  # no abstract -> unverifiable
        ],
    }
    # c1 verifiable (judge it), c2 uncited, c3 fabricated (999 not retrieved -> unsupported, no call),
    # c4 unverifiable (333 retrieved but no abstract -> no call).
    client = _client([
        {"claims": [
            {"claim": "c1", "cited_pmids": ["111"]},
            {"claim": "c2", "cited_pmids": []},
            {"claim": "c3", "cited_pmids": ["999"]},
            {"claim": "c4", "cited_pmids": ["333"]},
        ]},
        {"supported": True, "reasoning": "matches abstract"},   # only c1 reaches the judge
    ])
    r = faithfulness.score(client, record)
    assert r["n_claims"] == 4
    assert r["n_cited_claims"] == 3          # c1 + c3 + c4 (c2 uncited)
    assert r["n_verifiable"] == 2            # c1 (supported) + c3 (fabricated->unsupported)
    assert r["n_supported"] == 1            # only c1
    assert r["n_unverifiable"] == 1          # c4
    assert r["faithfulness_rate"] == 0.5     # 1/2 verifiable (unverifiable excluded)
    assert round(r["unverifiable_rate"], 3) == round(1 / 3, 3)
    assert r["uncited_claims"] == ["c2"]
    # Only one per-claim judge call (c1); c3/c4 short-circuit, c2 uncited.
    assert len(client.messages.calls) == 2   # decompose + 1 verdict


def test_relevance_judge_hit_and_precision():
    client = _client([
        {"relevant": True, "reasoning": "on topic"},
        {"relevant": False, "reasoning": "off topic"},
    ])
    r = relevance_judge.score(client, "does X help Y?", ["the effect of X on Y"],
                              [{"pmid": "1", "title": "X and Y"}, {"pmid": "2", "title": "Z and W"}])
    assert r["n_retrieved"] == 2 and r["n_relevant"] == 1
    assert r["precision"] == 0.5 and r["hit"] is True


def test_relevance_judge_no_retrieved():
    r = relevance_judge.score(_client([]), "q", [], [])
    assert r == {"judged": [], "n_retrieved": 0, "n_relevant": 0, "precision": None, "hit": False}


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
