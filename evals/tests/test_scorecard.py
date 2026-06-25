"""End-to-end per-record assembly (scorecard.score_record) with a fake judge client."""
import _pathsetup  # noqa: F401  -- backend on path

from types import SimpleNamespace

from scorecard import assemble_record, score_record


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
    gold = {"question_id": "q1", "question": "Does X reduce Y?", "type": "factual", "split": "dev",
            "expected_behavior": "answer", "gold_pmids": ["111"], "subpoints": ["a", "b"]}
    # Call order: abstention, relevance (1 per retrieved paper), decompose, claim verdict, thoroughness.
    client = _client([
        {"abstained": False, "reasoning": "makes claims"},
        {"relevant": True, "reasoning": "on topic"},
        {"claims": [{"claim": "X reduces Y", "cited_pmids": ["111"]}]},
        {"supported": True, "reasoning": "matches"},
        {"results": [{"index": 1, "covered": True}, {"index": 2, "covered": False}]},
    ])
    card = score_record(record, gold, client)

    assert card["question_id"] == "q1"
    assert card["validity"]["ok"] is True
    assert card["relevance"]["hit"] is True and card["relevance"]["precision"] == 1.0
    assert card["relevance"]["gold_recall"] == 1.0  # diagnostic: cited paper is the gold one
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


def test_assemble_record_matches_sequential():
    """The batched assembly produces the same card as the sequential path given the
    same (canned) judge outputs."""
    record = {
        "repeat": 0,
        "messages": [{"role": "user", "content": "q"},
                     {"role": "assistant", "content": "X reduces Y (PMID: 111)."}],
        "retrieved": [{"pmid": "111", "title": "T", "abstract": "A"}],
        "cited_pmids": ["111"],
    }
    gold = {"question_id": "q1", "question": "Does X reduce Y?", "type": "factual", "split": "dev",
            "expected_behavior": "answer", "gold_pmids": ["111"], "subpoints": ["a", "b"]}

    # Same canned outputs the sequential test feeds, but as de-keyed batch results.
    seq_client = _client([
        {"abstained": False, "reasoning": "makes claims"},
        {"relevant": True, "reasoning": "on topic"},
        {"claims": [{"claim": "X reduces Y", "cited_pmids": ["111"]}]},
        {"supported": True, "reasoning": "matches"},
        {"results": [{"index": 1, "covered": True}, {"index": 2, "covered": False}]},
    ])
    seq = score_record(record, gold, seq_client)

    parts = {
        "abstention": {"abstained": False, "reasoning": "makes claims"},
        "relevance": [{"relevant": True, "reasoning": "on topic"}],
        "decompose": {"claims": [{"claim": "X reduces Y", "cited_pmids": ["111"]}]},
        "faithfulness": [{"supported": True, "reasoning": "matches"}],
        "thoroughness": {"results": [{"index": 1, "covered": True}, {"index": 2, "covered": False}]},
    }
    batched = assemble_record(record, gold, parts)

    assert batched == seq
