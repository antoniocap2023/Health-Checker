"""Tests for the populate harness (evals/populate.py).

Drives the REAL agent loop but with Claude and PubMed faked (same approach as
backend/tests/test_endpoint.py), persisting into a moto eval table — so this
exercises the in-process run -> evidence -> tagged record path with no network.
"""
import _pathsetup  # noqa: F401  -- backend on path + backend/.env loaded

from types import SimpleNamespace

import boto3
import pytest
from moto import mock_aws

import populate
import pubmed
from store import EvalStore

TABLE = "test-eval-conversations"


# ---- Fakes imitating the Anthropic streaming SDK (per-run generic) ----------

class _FakeStream:
    def __init__(self, texts, final):
        self._texts, self._final = texts, final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        return iter(self._texts)

    def get_final_message(self):
        return self._final


def _final(stop_reason, content):
    return SimpleNamespace(
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        content=content,
    )


def _tool_use_block():
    return SimpleNamespace(type="tool_use", id="t1", name="search_pubmed",
                           input={"query": "q", "max_results": 1})


class _FakeMessages:
    """Generic across any number of runs: search on the first turn, answer once a
    tool_result has come back."""

    def __init__(self):
        self.calls = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        last = kwargs["messages"][-1]
        is_tool_result = last["role"] == "user" and isinstance(last["content"], list)
        if is_tool_result:
            return _FakeStream(["Answer (PMID: 111)."], _final("end_turn", []))
        return _FakeStream([], _final("tool_use", [_tool_use_block()]))


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


def test_run_populate_writes_tagged_evidence(estore, monkeypatch):
    monkeypatch.setattr(pubmed, "search_and_fetch", lambda **k: {
        "total_matches": 1,
        "articles": [{"pmid": "111", "title": "T", "journal": "J", "abstract": "A"}],
    })
    monkeypatch.setattr(populate.ds, "load", lambda: [
        {"question_id": "q001", "question": "Does X help?", "split": "dev"},
        {"question_id": "q002", "question": "Does Y help?", "split": "dev"},
    ])
    client = SimpleNamespace(messages=_FakeMessages())

    summary = populate.run_populate("runX", repeats=2, split="all", client=client, store=estore)

    assert summary == {"run_id": "runX", "questions": 2, "repeats": 2, "written": 4, "failures": 0}
    recs = estore.read_run("runX")
    assert len(recs) == 4
    assert {r["question_id"] for r in recs} == {"q001", "q002"}
    assert {int(r["repeat"]) for r in recs} == {0, 1}
    sample = recs[0]
    assert sample["queries"] == ["q"]
    assert sample["retrieved"][0]["abstract"] == "A"
    assert sample["cited_pmids"] == ["111"]
    assert sample["messages"][-1]["content"] == "Answer (PMID: 111)."


def test_run_populate_isolates_failures(estore, monkeypatch):
    """A failing run is counted, not fatal — the batch keeps going."""
    monkeypatch.setattr(pubmed, "search_and_fetch", lambda **k: {
        "total_matches": 1, "articles": [{"pmid": "111", "title": "T", "abstract": "A"}],
    })
    monkeypatch.setattr(populate.ds, "load", lambda: [
        {"question_id": "q001", "question": "ok?", "split": "dev"},
    ])

    class _BoomOnce:
        def __init__(self):
            self.inner = _FakeMessages()
            self.n = 0

        def stream(self, **kwargs):
            self.n += 1
            if self.n == 1:  # blow up the very first turn of the first repeat
                raise RuntimeError("boom")
            return self.inner.stream(**kwargs)

    client = SimpleNamespace(messages=_BoomOnce())
    summary = populate.run_populate("runF", repeats=2, split="all", client=client, store=estore)

    assert summary["failures"] == 1
    assert summary["written"] == 1
    assert len(estore.read_run("runF")) == 1
