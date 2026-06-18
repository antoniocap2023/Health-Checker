"""Tests for _request_with_retry (pubmed.py) — the exponential-backoff layer.

THE BIG IDEA: this function's job is to retry transient network failures with a
growing, jittered delay. To test it without a real network (or real waiting) we
swap out three things inside pubmed:
  - `urlopen`        -> a fake driven by a scripted list of "behaviours"
                        (each one either raises a chosen error or returns a payload)
  - `time.sleep`     -> records the delays instead of sleeping
  - `random.uniform` -> records its (low, high) bounds and returns a fixed value,
                        so the otherwise-random backoff is deterministic
We also no-op the rate limiter's acquire(), since the backoff logic is what's
under test here, not the pacing.
"""
import urllib.error

import pytest

import pubmed


class _FakeResponse:
    """Stand-in for the object urlopen returns; used as a context manager.

    The real code does `with urlopen(...) as resp: return parse(resp)`. So our
    fake just needs to support `with`, handing back whatever payload we loaded.
    """

    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self._payload

    def __exit__(self, *exc):
        return False


def _make_fake_urlopen(behaviours):
    """Return a urlopen replacement that plays through `behaviours` in order.

    Each item is either an Exception instance (raised, simulating a failure) or
    any other value (returned wrapped in a _FakeResponse, simulating success).
    """
    sequence = list(behaviours)

    def fake_urlopen(req, timeout=None):
        behaviour = sequence.pop(0)
        if isinstance(behaviour, Exception):
            raise behaviour
        return _FakeResponse(behaviour)

    return fake_urlopen


def _http_error(code):
    """Build an HTTPError carrying a status code (e.g. 503, 400)."""
    return urllib.error.HTTPError(
        url="http://example/test", code=code, msg="simulated", hdrs=None, fp=None
    )


@pytest.fixture
def captured(monkeypatch):
    """Common setup: no-op the rate limiter, capture sleeps and backoff bounds.

    Returns a dict the test can inspect:
      - "sleeps": list of delays passed to time.sleep
      - "uniform_highs": list of the upper bounds passed to random.uniform
    """
    monkeypatch.setattr(pubmed._rate_limiter, "acquire", lambda rid="-": None)

    sleeps = []
    monkeypatch.setattr(pubmed.time, "sleep", lambda d: sleeps.append(d))

    uniform_highs = []

    def fake_uniform(low, high):
        uniform_highs.append(high)
        return high  # deterministic: pretend jitter picked the max each time

    monkeypatch.setattr(pubmed.random, "uniform", fake_uniform)

    return {"sleeps": sleeps, "uniform_highs": uniform_highs}


def _call(behaviours):
    """Run _request_with_retry with a scripted urlopen; parse just echoes back."""
    return pubmed._request_with_retry(
        req=object(),               # opaque; the fake urlopen ignores it
        parse=lambda resp: resp,    # return the payload unchanged
        rid="test",
        label="esearch",
    )


def test_transient_then_success(monkeypatch, captured):
    """A 503 (transient) is retried; the following success is returned."""
    monkeypatch.setattr(
        pubmed.urllib.request, "urlopen",
        _make_fake_urlopen([_http_error(503), "OK"]),
    )

    result = _call(None)

    assert result == "OK"
    assert len(captured["sleeps"]) == 1  # exactly one backoff between the 2 tries


def test_bare_urlerror_is_transient(monkeypatch, captured):
    """A non-HTTP URLError (DNS/connection-level) is always treated as transient."""
    monkeypatch.setattr(
        pubmed.urllib.request, "urlopen",
        _make_fake_urlopen([urllib.error.URLError("connection refused"), "OK"]),
    )

    assert _call(None) == "OK"
    assert len(captured["sleeps"]) == 1


def test_non_retryable_raises_immediately(monkeypatch, captured):
    """A 400 (bad request) is NOT retried — it raises at once with no backoff."""
    monkeypatch.setattr(
        pubmed.urllib.request, "urlopen",
        _make_fake_urlopen([_http_error(400)]),
    )

    with pytest.raises(urllib.error.HTTPError):
        _call(None)
    assert captured["sleeps"] == []  # never slept, never retried


def test_gives_up_after_max_retries(monkeypatch, captured):
    """Persistent 503s eventually re-raise after the retry budget is exhausted.

    Total attempts = _MAX_RETRIES + 1 (the first try plus the retries), so we
    script that many failures and expect _MAX_RETRIES sleeps (one between each
    pair of attempts) before the final failure propagates.
    """
    attempts = pubmed._MAX_RETRIES + 1
    monkeypatch.setattr(
        pubmed.urllib.request, "urlopen",
        _make_fake_urlopen([_http_error(503)] * attempts),
    )

    with pytest.raises(urllib.error.HTTPError):
        _call(None)
    assert len(captured["sleeps"]) == pubmed._MAX_RETRIES


def test_backoff_delays_double(monkeypatch, captured):
    """The backoff window doubles each attempt: base * 2**attempt.

    With _BACKOFF_BASE = 0.5 and _MAX_RETRIES = 4, four failures then a success
    means random.uniform is called with upper bounds 0.5, 1.0, 2.0, 4.0.
    """
    monkeypatch.setattr(
        pubmed.urllib.request, "urlopen",
        _make_fake_urlopen([_http_error(503)] * 4 + ["OK"]),
    )

    assert _call(None) == "OK"

    expected = [pubmed._BACKOFF_BASE * (2 ** attempt) for attempt in range(4)]
    assert captured["uniform_highs"] == expected
    # And because our fake_uniform returns the high end, the sleeps match too.
    assert captured["sleeps"] == expected
