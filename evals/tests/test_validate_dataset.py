"""Tests for the dataset validator (validate_dataset.validate_rows).

Offline only — exercises the schema/integrity rules on in-memory rows, never
touching PubMed (that's the separate, opt-in --check-pmids path).
"""
import validate_dataset as vd


def _row(**overrides):
    """A well-formed answerable row; override fields to make it invalid."""
    row = {
        "question_id": "q001",
        "question": "Does X help Y?",
        "type": "factual",
        "expected_behavior": "answer",
        "gold_pmids": ["12345678"],
        "subpoints": ["effect size", "safety"],
        "split": "dev",
        "notes": "meta-analysis is the strongest evidence here",
    }
    row.update(overrides)
    return row


def test_valid_answer_row_passes():
    assert vd.validate_rows([_row()]) == []


def test_valid_abstain_row_passes():
    row = _row(question_id="q002", type="adversarial", expected_behavior="abstain",
               gold_pmids=[], subpoints=[])
    assert vd.validate_rows([row]) == []


def test_missing_field_flagged():
    row = _row()
    del row["gold_pmids"]
    errors = vd.validate_rows([row])
    assert any("missing field 'gold_pmids'" in e for e in errors)


def test_bad_enum_flagged():
    errors = vd.validate_rows([_row(type="opinion", split="prod")])
    assert any("type must be one of" in e for e in errors)
    assert any("split must be one of" in e for e in errors)


def test_answer_row_needs_gold_and_subpoints():
    errors = vd.validate_rows([_row(gold_pmids=[], subpoints=[])])
    assert any("need at least one gold PMID" in e for e in errors)
    assert any("need at least one subpoint" in e for e in errors)


def test_abstain_row_must_have_empty_gold():
    row = _row(expected_behavior="abstain", gold_pmids=["12345678"])
    errors = vd.validate_rows([row])
    assert any("abstain rows must have empty gold_pmids" in e for e in errors)


def test_duplicate_question_id_flagged():
    errors = vd.validate_rows([_row(), _row()])
    assert any("duplicate question_id" in e for e in errors)


def test_malformed_pmid_flagged():
    errors = vd.validate_rows([_row(gold_pmids=["PMC123", "12a"])])
    assert sum("must be a digit string" in e for e in errors) == 2


def test_bad_question_id_pattern_flagged():
    errors = vd.validate_rows([_row(question_id="001")])
    assert any("question_id must match" in e for e in errors)
