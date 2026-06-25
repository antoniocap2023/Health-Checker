"""End-to-end per-record assembly (scorecard.score_record) with a fake judge client."""
import _pathsetup  # noqa: F401  -- backend on path

from types import SimpleNamespace

from scorecard import score_record


class _FakeMsgs:
    def __init__(self, responses):
        self.responses = list(responses)

    def create(self, **kwargs):
        inp = self.responses.pop(0)
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", name="record", input=inp)])


def _client(responses):
    return SimpleNamespace(messages=_FakeMsgs(responses))


def test_answer_row_full_scorecard():
    record = {
        "repeat": 0,
        "messages": [{"role": "user", "content": "q"},
                     {"role": "assistant", "content": "X reduces Y (PMID: 111)."}],
        "retrieved": [{"pmid": "111", "title": "T", "abstract": "A"}],
        "cited_pmids": ["111"],
    }
    gold = {"question_id": "q1", "type": "factual", "split": "dev",
            "expected_behavior": "answer", "gold_pmids": ["111"], "subpoints": ["a", "b"]}
    # Order: abstention, decompose, claim verdict, thoroughness.
    client = _client([
        {"abstained": False, "reasoning": "makes claims"},
        {"claims": [{"claim": "X reduces Y", "cited_pmids": ["111"]}]},
        {"supported": True, "reasoning": "matches"},
        {"results": [{"index": 1, "covered": True}, {"index": 2, "covered": False}]},
    ])
    card = score_record(record, gold, client)

    assert card["question_id"] == "q1"
    assert card["validity"]["ok"] is True
    assert card["relevance"]["recall"] == 1.0 and card["relevance"]["hit"] is True
    assert card["faithfulness"]["faithfulness_rate"] == 1.0
    assert card["thoroughness"]["coverage"] == 0.5
    assert card["abstention"]["correct"] is True  # expected answer, did not abstain


def test_abstain_row_scorecard():
    record = {
        "repeat": 0,
        "messages": [{"role": "user", "content": "q"},
                     {"role": "assistant", "content": "I couldn't find evidence on that in PubMed."}],
        "retrieved": [],
        "cited_pmids": [],
    }
    gold = {"question_id": "q9", "type": "adversarial", "split": "dev",
            "expected_behavior": "abstain", "gold_pmids": [], "subpoints": []}
    client = _client([{"abstained": True, "reasoning": "declines, no evidence"}])

    card = score_record(record, gold, client)
    assert card["abstention"]["correct"] is True
    assert card["relevance"] is None
    assert card["faithfulness"] is None
    assert card["thoroughness"] is None
    assert card["validity"]["ok"] is True
