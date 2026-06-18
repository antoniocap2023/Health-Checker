"""Tests for SlidingWindowRateLimiter (pubmed.py).

THE BIG IDEA: the limiter's behaviour is all about *time* — "how many requests
in the last N seconds". A test must not depend on real wall-clock time, or it
would be slow (real sleeps) and flaky (timing varies machine to machine). So for
the deterministic tests we install a FAKE CLOCK: we replace `time.monotonic` and
`time.sleep` inside the pubmed module with our own functions, where "sleeping"
simply jumps a counter forward. That makes every test instant and exact.

The one exception is the concurrency test at the bottom, which uses the REAL
clock with real threads, because its whole point is to prove the lock works
under genuine parallelism.
"""
import threading
import time

import pubmed


class FakeClock:
    """A clock we fully control.

    `monotonic()` returns our current fake time, and `sleep(dt)` doesn't really
    sleep — it just advances that fake time by `dt`. So when the limiter decides
    "I need to wait 1.0s" and calls sleep(1.0), the clock jumps forward 1.0s
    instantly, and the next `monotonic()` reflects it. This lets us assert
    exactly how long the limiter *would* have slept, with zero real waiting.
    """

    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


def _install_fake_clock(monkeypatch):
    """Point pubmed's time.monotonic/time.sleep at a FakeClock; return the clock.

    `monkeypatch` is a built-in pytest fixture that undoes every change it makes
    automatically when the test finishes, so we never leak the fake clock into
    other tests.
    """
    clock = FakeClock()
    monkeypatch.setattr(pubmed.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(pubmed.time, "sleep", clock.sleep)
    return clock


def test_under_cap_returns_immediately(monkeypatch):
    """The first `max_requests` acquires should all pass without any waiting."""
    clock = _install_fake_clock(monkeypatch)
    limiter = pubmed.SlidingWindowRateLimiter(max_requests=3, window_seconds=1.0)

    limiter.acquire()
    limiter.acquire()
    limiter.acquire()

    # No sleep ever happened, so the clock never moved.
    assert clock.t == 0.0


def test_at_cap_waits_until_oldest_ages_out(monkeypatch):
    """The (cap+1)-th acquire must wait exactly until the oldest entry expires.

    With cap=2 and a 1.0s window, two acquires fill the window at t=0. The third
    can't proceed until the oldest (t=0) is older than the window, i.e. at
    t=1.0 — so the limiter should sleep exactly 1.0s.
    """
    clock = _install_fake_clock(monkeypatch)
    limiter = pubmed.SlidingWindowRateLimiter(max_requests=2, window_seconds=1.0)

    limiter.acquire()  # records t=0
    limiter.acquire()  # records t=0 (window now full: [0, 0])
    limiter.acquire()  # full -> must wait for the oldest (t=0) to age out

    assert clock.t == 1.0


def test_window_slides(monkeypatch):
    """Old timestamps drop out as time passes, freeing slots without waiting.

    Fill the window at t=0, then manually advance the clock past the window. The
    next acquire should find the window empty (entries slid out) and return with
    no sleep.
    """
    clock = _install_fake_clock(monkeypatch)
    limiter = pubmed.SlidingWindowRateLimiter(max_requests=2, window_seconds=1.0)

    limiter.acquire()
    limiter.acquire()  # window full at t=0

    clock.t = 1.5  # 1.5s later — both entries are now older than the 1.0s window
    limiter.acquire()  # should slide them out and pass instantly

    # The acquire at t=1.5 did not need to sleep, so the clock didn't advance.
    assert clock.t == 1.5


def test_second_full_cycle_waits_for_the_right_entry(monkeypatch):
    """A more realistic sequence: confirm the wait targets the OLDEST entry.

    cap=2, window=1.0. Acquire at t=0 and t=0 (full). Third acquire waits to
    t=1.0 and records there. Now the window holds [t=0(expired on slide), t=1.0]
    -> after sliding, just [1.0], so a fourth acquire records immediately at 1.0.
    A fifth is then full again ([1.0, 1.0]) and must wait until t=2.0.
    """
    clock = _install_fake_clock(monkeypatch)
    limiter = pubmed.SlidingWindowRateLimiter(max_requests=2, window_seconds=1.0)

    limiter.acquire()          # t=0
    limiter.acquire()          # t=0  -> full
    limiter.acquire()          # waits to t=1.0, records 1.0
    assert clock.t == 1.0

    limiter.acquire()          # window is [1.0]; room -> records at 1.0, no wait
    assert clock.t == 1.0

    limiter.acquire()          # window [1.0, 1.0] full -> waits to t=2.0
    assert clock.t == 2.0


def test_production_limiter_stays_under_ncbi_cap():
    """Guardrail: the SHARED PubMed limiter must never allow >10 req/sec.

    NCBI's limit is ~10/sec with an API key (~3 without). Every real NCBI call —
    including the ones in the integration and e2e tests — flows through this one
    `pubmed._rate_limiter`, so this single check is what keeps the whole suite
    (and production) under NCBI's cap. If someone raises `_RATE_LIMIT` or shrinks
    the window, this fails here instead of getting us blocked by NCBI.
    """
    limiter = pubmed._rate_limiter
    rate_per_sec = limiter.max_requests / limiter.window
    assert rate_per_sec <= 10, (
        f"configured PubMed rate is {rate_per_sec}/s, which exceeds NCBI's 10/s cap"
    )


def test_concurrent_threads_never_exceed_cap():
    """Prove the LOCK works: under real parallelism the cap is never breached.

    This one deliberately uses the REAL clock and REAL threads. We push 20
    threads through a small limiter (5 per 0.2s) and record the real time each
    one actually fired. If the lock is correct, then within ANY window of 0.2s
    at most 5 requests fired — equivalently, for any 6 consecutive fires, the
    6th happened at least one window after the 1st.

    Total real time is small: 20 requests / 5 per 0.2s ~= 0.8s.
    """
    cap, window = 5, 0.2
    limiter = pubmed.SlidingWindowRateLimiter(max_requests=cap, window_seconds=window)

    fire_times = []
    record_lock = threading.Lock()  # protects our list, unrelated to the limiter

    def worker():
        limiter.acquire()
        with record_lock:
            fire_times.append(time.monotonic())

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    fire_times.sort()
    # We measure the time slightly AFTER the limiter let each thread through, so
    # real spacing is >= the limiter's intended spacing. A small tolerance covers
    # OS scheduling jitter.
    tolerance = 0.03
    for i in range(cap, len(fire_times)):
        gap = fire_times[i] - fire_times[i - cap]
        assert gap >= window - tolerance, (
            f"requests {i - cap}..{i} fell within {gap:.3f}s "
            f"(< window {window}s) — cap was exceeded"
        )
