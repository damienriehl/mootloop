"""A small deterministic token-bucket rate limiter (plan D5: one process-wide
60-cites/min bucket for the citation gate). Clock + sleep are injectable so tests
drive it without real time."""

from __future__ import annotations

import time
from collections.abc import Callable


class TokenBucket:
    """Classic token bucket. ``acquire`` blocks (via the injected ``sleep``) only when
    the bucket is empty, so under the rate it is free; tests inject a fake clock."""

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

    def _replenish(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._last)
        self._last = now
        self._tokens = min(float(self.capacity), self._tokens + elapsed * self.refill_per_second)

    def acquire(self, tokens: int = 1) -> None:
        self._replenish()
        if self._tokens < tokens:
            wait = (tokens - self._tokens) / self.refill_per_second
            self._sleep(wait)
            self._replenish()
        self._tokens -= tokens


def default_limiter() -> TokenBucket:
    """The process-wide 60-requests/minute bucket for CourtListener (plan D5)."""
    return TokenBucket(capacity=60, refill_per_second=60.0 / 60.0)
