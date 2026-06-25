"""Tests for ConversationStore (storage.py).

THE BIG IDEA: exercise the REAL DynamoDB code path without touching real AWS. `moto`
intercepts every boto3 AWS call inside `mock_aws()` and serves an in-memory fake
DynamoDB, so these tests are fast, free, and safe to run anywhere (including CI).
"""
import boto3
import pytest
from moto import mock_aws

import storage

TABLE = "test-conversations"


@pytest.fixture
def store(monkeypatch):
    # Make the environment hermetic: drop any AWS_PROFILE inherited from .env, and
    # set dummy credentials so botocore signs requests without reading ~/.aws or
    # ever reaching real AWS. moto handles the rest.
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
        # profile=None forces the boto3 default chain (our dummy env creds), ignoring
        # whatever settings.aws_profile is — so the test never depends on a real profile.
        yield storage.ConversationStore(table_name=TABLE, region="us-east-1", profile=None)


def test_save_then_get_roundtrips(store):
    msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    store.save("c1", msgs)
    assert store.get("c1") == msgs


def test_get_missing_returns_none(store):
    assert store.get("does-not-exist") is None


def test_save_overwrites_messages(store):
    store.save("c1", [{"role": "user", "content": "first"}])
    store.save("c1", [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
    ])
    got = store.get("c1")
    assert len(got) == 2
    assert got[-1] == {"role": "assistant", "content": "answer"}


def test_created_at_preserved_across_saves(store):
    """created_at is stamped once and preserved; updated_at moves on each save."""
    store.save("c1", [{"role": "user", "content": "a"}])
    raw = boto3.resource("dynamodb", region_name="us-east-1").Table(TABLE)
    first = raw.get_item(Key={"conversation_id": "c1"})["Item"]
    store.save("c1", [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}])
    second = raw.get_item(Key={"conversation_id": "c1"})["Item"]
    assert first["created_at"] == second["created_at"]
    assert second["updated_at"] >= first["updated_at"]
