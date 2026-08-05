"""A small deterministic token-bucket rate limiter (plan D5: one process-wide
60-cites/min bucket for the citation gate). Clock + sleep are injectable so tests
drive it without real time."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

CL_CAPACITY = 60
CL_REFILL_PER_SECOND = 60.0 / 60.0


class TokenBucket:
    """Classic token bucket. ``acquire`` blocks (via the injected ``sleep``) only when
    the bucket is empty, so under the rate it is free; tests inject a fake clock.

    ``acquire`` is serialized by an internal lock, so the bucket is safe to share
    across threads — which the process-wide bucket must be, since the matter API
    runs its sync request handlers in a threadpool. The sleep happens while the lock
    is held: waiters queue behind the thread that is already paying the wait, which
    is exactly the serialization a rate limit is for, and it keeps two threads from
    each computing a wait against the same tokens and then both spending them.
    """

    def __init__(
        self,
        *,
        capacity: int,
        refill_per_second: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self._tokens = float(capacity)
        self._clock = clock
        self._sleep = sleep
        self._last = clock()
        self._lock = threading.Lock()

    @property
    def tokens(self) -> float:
        """Tokens available right now (replenished). Read-only; for tests/telemetry."""
        with self._lock:
            self._replenish()
            return self._tokens

    def _replenish(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._last)
        self._last = now
        self._tokens = min(float(self.capacity), self._tokens + elapsed * self.refill_per_second)

    def acquire(self, tokens: int = 1) -> None:
        with self._lock:
            self._replenish()
            if self._tokens < tokens:
                wait = (tokens - self._tokens) / self.refill_per_second
                self._sleep(wait)
                self._replenish()
            self._tokens -= tokens


_default_lock = threading.Lock()
_default_bucket: TokenBucket | None = None


def default_limiter() -> TokenBucket:
    """The process-wide 60-requests/minute bucket for CourtListener (plan D5).

    One shared instance, memoized. It used to build a NEW bucket per call, so the
    "process-wide" budget was really a per-caller budget: every `verify_cases` call
    that did not pass a limiter started full, and N concurrent verifications could
    issue 60N requests a minute at CourtListener — a free-tier API whose terms the
    plan pins this bucket to. Sharing it is the whole point of the control.
    """
    global _default_bucket
    with _default_lock:
        if _default_bucket is None:
            _default_bucket = TokenBucket(
                capacity=CL_CAPACITY, refill_per_second=CL_REFILL_PER_SECOND
            )
        return _default_bucket


def reset_default_limiter() -> None:
    """Drop the process-wide bucket so the next `default_limiter` builds a fresh one.

    A test seam. Production never needs it: the budget is deliberately process-lived.
    """
    global _default_bucket
    with _default_lock:
        _default_bucket = None
