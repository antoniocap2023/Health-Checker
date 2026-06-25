"""Score one evidence record against its gold row → a per-record scorecard.

Pure orchestration: runs the type-gated checks and assembles the result. No I/O,
no aggregation (that's Phase 4). Answer rows get all four quality checks plus an
abstention guard; abstain rows get abstention (headline) + validity, with the
gold-dependent checks left null.
"""
from checks.relevance import relevance
from checks.validity import validity
from judges import abstention, faithfulness, thoroughness


def _answer_text(record):
    msgs = record.get("messages", [])
    if msgs and msgs[-1].get("role") == "assistant":
        return msgs[-1].get("content", "")
    return ""


def score_record(record, gold_row, client):
    expected = gold_row.get("expected_behavior")
    answer = _answer_text(record)

    abst = abstention.score(client, answer)
    abst["expected"] = expected
    abst["correct"] = abst["abstained"] == (expected == "abstain")

    card = {
        "question_id": gold_row.get("question_id"),
        "repeat": int(record.get("repeat", 0)),
        "type": gold_row.get("type"),
        "split": gold_row.get("split"),
        "expected_behavior": expected,
        "validity": validity(record),
        "abstention": abst,
    }

    if expected == "answer":
        card["relevance"] = relevance(record, gold_row.get("gold_pmids", []))
        card["faithfulness"] = faithfulness.score(client, record)
        card["thoroughness"] = thoroughness.score(client, answer, gold_row.get("subpoints", []))
    else:
        # No gold to score these against on abstain rows.
        card["relevance"] = None
        card["faithfulness"] = None
        card["thoroughness"] = None

    return card
