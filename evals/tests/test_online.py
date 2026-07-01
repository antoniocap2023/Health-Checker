"""Online eval — reference-free scoring of real conversations, with a FAKE store and
FAKE judge client (no AWS, no Claude)."""
import _pathsetup  # noqa: F401  -- backend + evals on path

import logging
from types import SimpleNamespace

import online_eval

LOG = logging.getLogger("test.online")


class _FakeMsgs:
    def __init__(self, responses):
        self.responses = list(responses)

    def create(self, **kwargs):
        inp = self.responses.pop(0)
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", name="record", input=inp)])


def _client(responses):
    return SimpleNamespace(messages=_FakeMsgs(responses))


# c1: answered with a search; cites 111 (retrieved) and 999 (NOT retrieved → fabricated).
REC_ANSWER = {
    "conversation_id": "c1", "created_at": "2026-07-01T10:00:00",
    "messages": [{"role": "user", "content": "Does X reduce Y?"},
                 {"role": "assistant", "content": "X reduces Y (PMID: 111). Z happens (PMID: 999)."}],
    "retrieved": [{"pmid": "111", "title": "T", "abstract": "A"}],
    "cited_pmids": ["111", "999"],
}
# c2: abstain-style answer, no retrieval.
REC_ABSTAIN = {
    "conversation_id": "c2", "created_at": "2026-07-01T12:00:00",
    "messages": [{"role": "user", "content": "Does amethyst cure eczema?"},
                 {"role": "assistant", "content": "No — there's no evidence for that."}],
    "retrieved": [], "cited_pmids": [],
}
# c3: messages-only (last turn is the user) → not scorable.
REC_INCOMPLETE = {
    "conversation_id": "c3", "created_at": "2026-07-01T13:00:00",
    "messages": [{"role": "user", "content": "pending?"}],
}


def test_load_recent_skips_incomplete_and_orders_by_recency(monkeypatch):
    class FakeStore:
        def __init__(self, table_name=None):
            pass

        def scan_sample(self, only_real=False, max_scan=None):
            return [REC_ABSTAIN, REC_INCOMPLETE, REC_ANSWER]  # unsorted; one incomplete

    monkeypatch.setattr(online_eval, "ConversationStore", FakeStore)
    recs, counts = online_eval.load_recent("t", limit=10, max_scan=None, log=LOG)
    assert [r["conversation_id"] for r in recs] == ["c2", "c1"]  # c3 skipped; recency desc
    assert counts == {"scanned": 3, "scorable": 2, "scored": 2}


def _answer_card():
    # call order in _parts_sequential: abstention, decompose, relevance(1 paper), faith(1 to_judge)
    client = _client([
        {"outcome": "affirmed", "reasoning": "claim"},
        {"claims": [{"claim": "X reduces Y", "cited_pmids": ["111"]},
                    {"claim": "Z happens", "cited_pmids": ["999"]}]},
        {"relevant": True, "reasoning": "on topic"},
        {"supported": True, "reasoning": "matches"},
    ])
    return online_eval.score_sequential([REC_ANSWER], client, LOG)[0]


def test_online_answer_card_is_reference_free():
    c = _answer_card()
    # validity catches the fabricated citation deterministically
    assert c["validity"]["ok"] is False and c["validity"]["fabricated_pmids"] == ["999"]
    assert c["relevance"]["hit"] is True and c["relevance"]["precision"] == 1.0
    # 999 (not retrieved) → fabricated→unsupported; 111 → judged supported
    assert c["faithfulness"]["n_verifiable"] == 2 and c["faithfulness"]["n_supported"] == 1
    assert c["abstention"]["outcome"] == "affirmed"
    # reference-free: no gold-dependent fields
    assert "thoroughness" not in c
    assert "gold_recall" not in c["relevance"]
    assert "correct" not in c["abstention"]


def test_online_abstain_card_scores_only_abstention():
    c = online_eval.score_sequential([REC_ABSTAIN], _client([{"outcome": "no_evidence"}]), LOG)[0]
    assert c["relevance"] is None and c["faithfulness"] is None  # no retrieval → not scored
    assert c["validity"]["ok"] is True
    assert c["abstention"]["outcome"] == "no_evidence"


def test_summarize_and_flag():
    ca = _answer_card()
    cb = online_eval.score_sequential([REC_ABSTAIN], _client([{"outcome": "no_evidence"}]), LOG)[0]
    cards = [ca, cb]

    s = online_eval.summarize(cards)
    assert s["n_scored"] == 2 and s["n_with_retrieval"] == 1
    assert s["validity_ok_rate"] == 0.5
    assert s["faithfulness_rate"] == 0.5  # 1 supported / 2 verifiable
    assert s["abstention_outcomes"] == {"affirmed": 1, "no_evidence": 1}

    flags = online_eval.flag(cards)
    assert len(flags) == 1 and flags[0]["conversation_id"] == "c1"
    assert any("fabricated" in i for i in flags[0]["issues"])
    assert any("unsupported claim" in i for i in flags[0]["issues"])
