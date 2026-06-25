"""Score one evidence record against its gold row → a per-record scorecard.

Pure orchestration: runs the type-gated checks and assembles the result. No I/O,
no aggregation (that's Phase 4). Answer rows get all four quality checks plus an
abstention guard; abstain rows get abstention (headline) + validity, with the
gold-dependent checks left null.

Two entry points share one card shape:
  - `score_record` makes the judge calls inline (sequential path; check_run default).
  - `assemble_record` builds the same card from already-collected batch results
    (the batched path in check_run) — no model calls.
"""
from checks.relevance import relevance as gold_relevance
from checks.validity import validity
from judges import abstention, decompose, faithfulness, relevance_judge, thoroughness


def _answer_text(record):
    msgs = record.get("messages", [])
    if msgs and msgs[-1].get("role") == "assistant":
        return msgs[-1].get("content", "")
    return ""


def _relevance_card(rel, record, gold_row):
    """Combine the topical relevance block with the demoted gold-overlap diagnostic."""
    gold = gold_relevance(record, gold_row.get("gold_pmids", []))
    return {
        # headline: topical, reference-free
        "hit": rel["hit"],
        "precision": rel["precision"],
        "n_retrieved": rel["n_retrieved"],
        "n_relevant": rel["n_relevant"],
        "judged": rel["judged"],
        # demoted diagnostics: exact gold-PMID overlap
        "gold_recall": gold["recall"],
        "gold_hit": gold["hit"],
        "gold_hits": gold["hits"],
    }


def _abstention_card(abst, expected):
    abst["expected"] = expected
    abst["correct"] = abst["abstained"] == (expected == "abstain")
    return abst


def _base_card(record, gold_row):
    return {
        "question_id": gold_row.get("question_id"),
        "repeat": int(record.get("repeat", 0)),
        "type": gold_row.get("type"),
        "split": gold_row.get("split"),
        "expected_behavior": gold_row.get("expected_behavior"),
        "validity": validity(record),
    }


def score_record(record, gold_row, client):
    """Sequential: make the judge calls inline and assemble the scorecard."""
    expected = gold_row.get("expected_behavior")
    answer = _answer_text(record)

    card = _base_card(record, gold_row)
    card["abstention"] = _abstention_card(abstention.score(client, answer), expected)

    if expected == "answer":
        subpoints = gold_row.get("subpoints", [])
        rel = relevance_judge.score(client, gold_row.get("question", ""), subpoints,
                                    record.get("retrieved", []))
        card["relevance"] = _relevance_card(rel, record, gold_row)
        card["faithfulness"] = faithfulness.score(client, record)
        card["thoroughness"] = thoroughness.score(client, answer, subpoints)
    else:
        # No gold to score these against on abstain rows.
        card["relevance"] = None
        card["faithfulness"] = None
        card["thoroughness"] = None

    return card


def assemble_record(record, gold_row, parts):
    """Batched: build the same scorecard from collected batch results — no model calls.

    `parts` carries the de-keyed Batches-API results for this record:
      - "abstention": one tool-input (or None)
      - "decompose":  one tool-input (or None)        — answer rows
      - "thoroughness": one tool-input (or None)       — answer rows with subpoints
      - "relevance":  list of tool-inputs, aligned to record["retrieved"]
      - "faithfulness": list of tool-inputs, aligned to faithfulness.plan(...)["to_judge"]
    A None entry degrades gracefully (that judge is treated as unavailable).
    """
    expected = gold_row.get("expected_behavior")

    card = _base_card(record, gold_row)
    card["abstention"] = _abstention_card(abstention.parse(parts.get("abstention")), expected)

    if expected == "answer":
        retrieved = record.get("retrieved", [])
        rel_inputs = parts.get("relevance", [])
        judged = [{"pmid": str(a.get("pmid")), **relevance_judge.parse_paper(inp)}
                  for a, inp in zip(retrieved, rel_inputs)]
        card["relevance"] = _relevance_card(relevance_judge.assemble(judged), record, gold_row)

        claims = decompose.parse(parts.get("decompose"))
        plan_result = faithfulness.plan(record, claims)
        verdicts = [faithfulness.parse_claim(inp) if inp is not None else None
                    for inp in parts.get("faithfulness", [])]
        card["faithfulness"] = faithfulness.assemble(plan_result, verdicts)

        subpoints = gold_row.get("subpoints", [])
        card["thoroughness"] = thoroughness.parse(parts.get("thoroughness"), subpoints)
    else:
        card["relevance"] = None
        card["faithfulness"] = None
        card["thoroughness"] = None

    return card
