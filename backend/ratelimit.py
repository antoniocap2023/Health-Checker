"""A process-global sliding-window rate limiter.

This lived inside pubmed.py, but it is general-purpose (it knows nothing about
NCBI) and it is the heart of the upcoming **pubmed-proxy** service: that service
exists precisely to own ONE of these limiters so the NCBI cap is enforced once,
globally, no matter how many backend instances call it. Keeping it in its own
module makes that future move a copy, not a disentangling.

IMPORTANT — the scope of "global" here. This limiter coordinates THREADS within a
SINGLE process via one ``threading.Lock``. That is exactly right when one process
makes all the NCBI calls. It does NOT coordinate across processes or machines: run
N copies of this process and you get N independent limiters and N times the rate.
That is the entire reason we will funnel all PubMed traffic through a single
proxy instance on AWS rather than letting each backend container call NCBI itself.
"""
import collections
import logging
import threading
import time

logger = logging.getLogger("healthchecker.ratelimit")


class SlidingWindowRateLimiter:
    """Allow at most `max_requests` within any rolling `window_seconds` interval.

    This is a "sliding window log": we keep the timestamp of every request we let
    through. Before allowing a new one we (1) drop timestamps older than the
    window — that's what makes the window *slide* with the clock — then (2) check
    how many remain, and (3) if we're at the cap, sleep until the oldest one ages
    out and re-check.

    Unlike a fixed-window counter (reset every calendar second), this can't be
    fooled by a burst that straddles a boundary, because it always looks back
    exactly `window` seconds from *now*.

    Thread-safe: our FastAPI chat endpoint is synchronous, so FastAPI runs it in
    a thread pool and several chats can call PubMed at once. They share ONE
    limiter instance and one lock, so the cap is global across all of them.
    """

    def __init__(self, max_requests, window_seconds):
        self.max_requests = max_requests
        self.window = window_seconds
        # Monotonic timestamps of allowed requests still inside the window.
        # Oldest is on the left (popleft), newest pushed on the right (append).
        self._timestamps = collections.deque()
        self._lock = threading.Lock()

    def acquire(self, rid="-"):
        """Block until one request is allowed under the window, then record it.

        We hold the lock for the whole decision — including the sleep — so the
        "is there a free slot?" check and the "claim it" are atomic. Otherwise
        two threads could both see a free slot and both fire. The actual (slow)
        HTTP request runs *after* this returns and the lock is released, so
        throughput is still paced at ~max_requests/window, not fully serialized.

        We use time.monotonic() rather than time.time() because the monotonic
        clock only moves forward — it's immune to NTP/system-clock adjustments,
        so the interval math stays correct.
        """
        with self._lock:
            while True:
                now = time.monotonic()
                # 1. SLIDE: drop entries that have left the window.
                cutoff = now - self.window
                while self._timestamps and self._timestamps[0] <= cutoff:
                    self._timestamps.popleft()

                # 2. DETECT: under the cap? claim a slot and go.
                if len(self._timestamps) < self.max_requests:
                    self._timestamps.append(now)
                    return

                # 3. MITIGATE: full. A slot frees when the OLDEST entry ages out,
                #    so wait until then (the "timeout before retrying"), then loop
                #    back to re-slide and re-check — don't assume one slot opened.
                wait = self._timestamps[0] + self.window - now
                if wait > 0:
                    logger.info("[%s] rate limit %d/%d in %.0fs window — waiting %.3fs",
                                rid, len(self._timestamps), self.max_requests, self.window, wait)
                    time.sleep(wait)
