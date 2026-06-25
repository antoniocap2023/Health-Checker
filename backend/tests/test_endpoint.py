"""Tests for the POST /api/chat endpoint (main.py) — the agent loop itself.

THE BIG IDEA: we want to test OUR streaming/tool-loop logic, not Claude and not
NCBI. So we replace two things:
  - `main.client.messages` -> a fake whose `stream(...)` returns scripted turns
  - `pubmed.search_and_fetch` -> a stub returning canned articles
Then we POST to the endpoint with FastAPI's TestClient (which runs the app
in-process, no real server) and parse the NDJSON response — one JSON object per
line — back into a list of event dicts to assert on.
"""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
import pubmed


# ---- Fakes that imitate the Anthropic streaming SDK ------------------------

class _FakeStream:
    """One streamed turn, used as a context manager like the real SDK.

    `text_stream` yields the assistant's text chunks; `get_final_message()`
    returns the final message object (stop_reason, usage, content blocks).
    """

    def __init__(self, texts, final):
        self._texts = texts
        self._final = final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        return iter(self._texts)

    def get_final_message(self):
        return self._final


class _FakeMessages:
    """Replacement for client.messages: hands back scripted turns in order."""

    def __init__(self, turns):
        # each turn is (list_of_text_chunks, final_message)
        self._turns = list(turns)
        self.calls = []  # records kwargs of each stream() call, for assertions

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        texts, final = self._turns.pop(0)
        return _FakeStream(texts, final)


def _final(stop_reason, content, texts=""):
    """Build a fake 'final message' object with the attributes main.py reads."""
    return SimpleNamespace(
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=10, output_tokens=20),
        content=content,
    )


def _tool_use_block(query="aspirin", max_results=2, block_id="tool-1"):
    """A fake tool_use content block, mimicking the SDK's block object."""
    return SimpleNamespace(
        type="tool_use",
        id=block_id,
        name="search_pubmed",
        input={"query": query, "max_results": max_results},
    )


def _events_from(response):
    """Parse an NDJSON streaming response body into a list of event dicts."""
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


@pytest.fixture
def client():
    return TestClient(main.app)


class _FakeStore:
    """In-memory stand-in for the DynamoDB store, so endpoint tests never hit AWS."""

    def __init__(self):
        self.saved = {}
        self.extra = {}  # evidence record per conversation (queries/retrieved/cited_pmids)

    def save(self, conversation_id, messages, extra=None):
        self.saved[conversation_id] = messages
        self.extra[conversation_id] = extra

    def get(self, conversation_id):
        return self.saved.get(conversation_id)


@pytest.fixture(autouse=True)
def fake_store(monkeypatch):
    """Swap main.store for an in-memory fake for EVERY test here — the chat endpoint
    now persists on each request, and tests must never write to real DynamoDB."""
    fake = _FakeStore()
    monkeypatch.setattr(main, "store", fake)
    return fake


def _post(client, text="hello"):
    return client.post("/api/chat", json={"messages": [{"role": "user", "content": text}]})


# ---- Tests -----------------------------------------------------------------

def test_health_check_returns_ok(client):
    """The /health liveness endpoint responds 200 without touching Claude/NCBI."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_chat_issues_conversation_id_and_persists(monkeypatch, client, fake_store):
    """A chat returns an X-Conversation-Id header and persists the transcript —
    ending with the assistant's answer (Save #2 ran when the stream completed)."""
    fake = _FakeMessages([(["Hi ", "there!"], _final("end_turn", content=[]))])
    monkeypatch.setattr(main.client, "messages", fake)

    response = _post(client, "hello")

    cid = response.headers.get("X-Conversation-Id")
    assert cid, "expected an X-Conversation-Id response header"
    assert cid in fake_store.saved
    saved = fake_store.saved[cid]
    assert saved[0] == {"role": "user", "content": "hello"}
    assert saved[-1] == {"role": "assistant", "content": "Hi there!"}


def test_chat_reuses_supplied_conversation_id(monkeypatch, client, fake_store):
    """When the client sends a conversation_id, the server keeps it (no new id)."""
    fake = _FakeMessages([(["ok"], _final("end_turn", content=[]))])
    monkeypatch.setattr(main.client, "messages", fake)

    response = client.post("/api/chat", json={
        "messages": [{"role": "user", "content": "hi"}],
        "conversation_id": "fixed-123",
    })

    assert response.headers.get("X-Conversation-Id") == "fixed-123"
    assert "fixed-123" in fake_store.saved


def test_get_conversation_returns_saved_messages(client, fake_store):
    """GET /api/conversations/{id} returns stored messages, or 404 when unknown."""
    fake_store.saved["abc"] = [{"role": "user", "content": "hi"}]
    ok = client.get("/api/conversations/abc")
    assert ok.status_code == 200
    assert ok.json() == {"conversation_id": "abc", "messages": [{"role": "user", "content": "hi"}]}

    missing = client.get("/api/conversations/nope")
    assert missing.status_code == 404


def test_plain_text_turn_streams_text(monkeypatch, client):
    """A non-tool turn just streams text events and finishes."""
    fake = _FakeMessages([
        (["Hello ", "there!"], _final("end_turn", content=[])),
    ])
    monkeypatch.setattr(main.client, "messages", fake)

    events = _events_from(_post(client, "hi"))

    text_events = [e for e in events if e["type"] == "text"]
    assert "".join(e["text"] for e in text_events) == "Hello there!"
    # No tool was used, so no search/turn_end/notice events.
    assert all(e["type"] == "text" for e in events)


def test_tool_use_turn_runs_search_then_answers(monkeypatch, client, fake_store):
    """Turn 1 calls the tool; we run the search and turn 2 streams the answer."""
    # Stub the PubMed layer so no network/NCBI traffic happens.
    fake_search = lambda **kwargs: {
        "total_matches": 1,
        "articles": [{"pmid": "111", "title": "T", "journal": "J", "abstract": "A"}],
    }
    monkeypatch.setattr(pubmed, "search_and_fetch", fake_search)

    fake = _FakeMessages([
        # Turn 1: model decides to search (stop_reason == "tool_use").
        ([], _final("tool_use", content=[_tool_use_block(query="aspirin headache")])),
        # Turn 2: model answers from the tool result.
        (["Aspirin helps (PMID: 111)."], _final("end_turn", content=[])),
    ])
    monkeypatch.setattr(main.client, "messages", fake)

    response = _post(client, "does aspirin help headaches?")
    events = _events_from(response)
    types = [e["type"] for e in events]

    # A search marker was surfaced, the turn boundary was emitted, then the answer.
    assert "search" in types
    assert "turn_end" in types
    search_event = next(e for e in events if e["type"] == "search")
    assert search_event["query"] == "aspirin headache"

    answer = "".join(e["text"] for e in events if e["type"] == "text")
    assert "PMID: 111" in answer

    # The loop ran exactly two turns (two stream calls).
    assert len(fake.calls) == 2

    # The evidence record was persisted: the query issued, the article retrieved
    # (with its abstract), and the PMID the answer cited.
    cid = response.headers["X-Conversation-Id"]
    evidence = fake_store.extra[cid]
    assert evidence["queries"] == ["aspirin headache"]
    assert [r["pmid"] for r in evidence["retrieved"]] == ["111"]
    assert evidence["retrieved"][0]["abstract"] == "A"
    assert evidence["cited_pmids"] == ["111"]


def test_system_prompt_includes_todays_date(monkeypatch, client):
    """The model is handed today's date so recency filters use the real year."""
    fake = _FakeMessages([(["ok"], _final("end_turn", content=[]))])
    monkeypatch.setattr(main.client, "messages", fake)

    _post(client, "hi")

    assert "Today's date is" in fake.calls[0]["system"]


def test_search_budget_reached_emits_notice(monkeypatch, client):
    """When the tool-call budget is spent, the loop emits a 'notice' and wraps up.

    Forcing the tool-call budget to 0 means the very first tool_use trips the
    budget path: instead of running the search we send back an is_error result and
    emit a user-facing notice, then the next turn answers.
    """
    monkeypatch.setattr(main.settings, "max_tool_calls", 0)

    # search_and_fetch must NOT be called when the budget is already spent.
    def boom(**kwargs):
        raise AssertionError("search should not run once the budget is spent")

    monkeypatch.setattr(pubmed, "search_and_fetch", boom)

    fake = _FakeMessages([
        ([], _final("tool_use", content=[_tool_use_block()])),
        (["Answering from what I have."], _final("end_turn", content=[])),
    ])
    monkeypatch.setattr(main.client, "messages", fake)

    events = _events_from(_post(client, "tell me about X"))
    types = [e["type"] for e in events]

    assert "notice" in types
    notice = next(e for e in events if e["type"] == "notice")
    assert "limit reached" in notice["text"].lower()
