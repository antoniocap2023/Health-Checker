"""Tests for EvalStore (evals/store.py) — write tagged records, read a run back.

Uses moto's in-memory DynamoDB (like backend/tests/test_storage.py); no real AWS.
"""
import _pathsetup  # noqa: F401  -- backend on path + backend/.env loaded

import boto3
import pytest
from moto import mock_aws

from store import EvalStore

TABLE = "test-eval-conversations"


@pytest.fixture
def estore(monkeypatch):
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName=TABLE,
            AttributeDefinitions=[{"AttributeName": "conversation_id", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "conversation_id", "KeyType": "HASH"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield EvalStore(table_name=TABLE, region="us-east-1", profile=None)


def _evidence(pmid="111"):
    return {
        "queries": ["q"],
        "retrieved": [{"pmid": pmid, "title": "T", "abstract": "A", "year": 2020, "pub_types": []}],
        "cited_pmids": [pmid],
    }


def test_save_record_and_read_run(estore):
    msgs = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a (PMID: 111)"}]
    estore.save_record("runA", "q001", 0, "c1", msgs, _evidence())
    estore.save_record("runA", "q001", 1, "c2", msgs, _evidence())
    estore.save_record("runB", "q002", 0, "c3", msgs, _evidence("222"))

    recs = estore.read_run("runA")
    assert len(recs) == 2
    assert {r["question_id"] for r in recs} == {"q001"}
    assert {int(r["repeat"]) for r in recs} == {0, 1}
    assert all(r["run_id"] == "runA" for r in recs)
    assert recs[0]["retrieved"][0]["abstract"] == "A"
    assert recs[0]["cited_pmids"] == ["111"]


def test_read_run_unknown_is_empty(estore):
    assert estore.read_run("does-not-exist") == []
