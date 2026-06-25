"""Judge logic — decompose/faithfulness/thoroughness/abstention — with a FAKE
judge client returning canned structured output. No real Claude.
"""
import _pathsetup  # noqa: F401  -- backend on path

from types import SimpleNamespace

from judges import abstention, decompose, faithfulness, relevance_judge, thoroughness
from judges import client as judge_client


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
                      "content": "c1 (PMID: 111). c2. c3 (PMID: 999). c4 (PMID: 333). c5 (PMID: 444)."}],
        "retrieved": [
            {"pmid": "111", "title": "T", "abstract": "A"},       # title+abstract -> judged
            {"pmid": "333", "title": "warning", "abstract": ""},  # title only -> judged (title fallback)
            {"pmid": "444", "title": "", "abstract": ""},         # no text at all -> unverifiable
        ],
    }
    # c1 judged, c2 uncited, c3 fabricated (999 not retrieved -> unsupported, no call),
    # c4 judged against the title (333), c5 unverifiable (444 has no title AND no abstract -> no call).
    client = _client([
        {"claims": [
            {"claim": "c1", "cited_pmids": ["111"]},
            {"claim": "c2", "cited_pmids": []},
            {"claim": "c3", "cited_pmids": ["999"]},
            {"claim": "c4", "cited_pmids": ["333"]},
            {"claim": "c5", "cited_pmids": ["444"]},
        ]},
        {"supported": True, "reasoning": "matches abstract"},   # c1
        {"supported": True, "reasoning": "matches title"},      # c4 (title-only)
    ])
    r = faithfulness.score(client, record)
    assert r["n_claims"] == 5
    assert r["n_cited_claims"] == 4          # c1 + c3 + c4 + c5 (c2 uncited)
    assert r["n_verifiable"] == 3            # c1, c4 (judged) + c3 (fabricated->unsupported)
    assert r["n_supported"] == 2            # c1 + c4
    assert r["n_unverifiable"] == 1          # c5 (no title/abstract)
    assert round(r["faithfulness_rate"], 3) == round(2 / 3, 3)  # unverifiable excluded
    assert round(r["unverifiable_rate"], 3) == round(1 / 4, 3)
    assert r["uncited_claims"] == ["c2"]
    # Two per-claim judge calls (c1, c4); c3/c5 short-circuit, c2 uncited.
    assert len(client.messages.calls) == 3   # decompose + 2 verdicts


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


# --- batch building blocks (pure; no client) ---

def test_build_request_shape():
    req = judge_client.build_request("0_abst", "SYS", "USR", abstention.SCHEMA)
    assert req["custom_id"] == "0_abst"
    p = req["params"]
    assert p["system"] == "SYS"
    assert p["messages"] == [{"role": "user", "content": "USR"}]
    assert p["temperature"] == 0
    assert p["tool_choice"] == {"type": "tool", "name": "record"}
    assert p["tools"][0]["input_schema"] is abstention.SCHEMA


def test_decompose_parse_tolerates_none():
    assert decompose.parse(None) == []
    assert decompose.parse({"claims": [{"claim": "x", "cited_pmids": [123, "abc"]}]}) == \
        [{"claim": "x", "cited_pmids": ["123"]}]


def test_faithfulness_plan_buckets():
    record = {"retrieved": [
        {"pmid": "111", "title": "T", "abstract": "A"},       # text -> to_judge
        {"pmid": "333", "title": "warning", "abstract": ""},  # title only -> to_judge
        {"pmid": "444", "title": "", "abstract": ""},         # no text -> unverifiable
    ]}
    claims = [
        {"claim": "c1", "cited_pmids": ["111"]},
        {"claim": "c2", "cited_pmids": []},
        {"claim": "c3", "cited_pmids": ["999"]},
        {"claim": "c4", "cited_pmids": ["333"]},
        {"claim": "c5", "cited_pmids": ["444"]},
    ]
    p = faithfulness.plan(record, claims)
    assert [e["claim"] for e in p["to_judge"]] == ["c1", "c4"]
    assert p["uncited"] == ["c2"]
    assert [e["claim"] for e in p["fabricated"]] == ["c3"]
    assert [e["claim"] for e in p["unverifiable"]] == ["c5"]


def test_faithfulness_assemble_none_verdict_degrades_to_unverifiable():
    record = {"retrieved": [{"pmid": "111", "title": "T", "abstract": "A"},
                            {"pmid": "222", "title": "T2", "abstract": "B"}]}
    claims = [{"claim": "c1", "cited_pmids": ["111"]}, {"claim": "c2", "cited_pmids": ["222"]}]
    p = faithfulness.plan(record, claims)
    # c1 judged supported; c2's verdict came back None (failed batch request).
    block = faithfulness.assemble(p, [{"supported": True, "reasoning": "ok"}, None])
    assert block["n_verifiable"] == 1 and block["n_supported"] == 1
    assert block["n_unverifiable"] == 1
    assert block["faithfulness_rate"] == 1.0
    assert any(u["reason"] == "judge unavailable" for u in block["unverifiable_claims"])


def test_relevance_assemble():
    judged = [{"pmid": "1", "relevant": True, "reasoning": "a"},
              {"pmid": "2", "relevant": False, "reasoning": "b"}]
    block = relevance_judge.assemble(judged)
    assert block["n_retrieved"] == 2 and block["n_relevant"] == 1
    assert block["precision"] == 0.5 and block["hit"] is True
