"""Best-effort per-process rate limiting, backed by API Gateway throttling in AWS."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque


class InMemoryRateLimiter:
    def __init__(self, requests_per_minute: int) -> None:
        self._limit = requests_per_minute
        self._requests: defaultdict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(self, client_key: str) -> bool:
        now = time.monotonic()
        cutoff = now - 60
        async with self._lock:
            timestamps = self._requests[client_key]
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()
            if len(timestamps) >= self._limit:
                return False
            timestamps.append(now)
            if len(self._requests) > 2048:
                self._prune(cutoff)
            return True

    def _prune(self, cutoff: float) -> None:
        stale = [
            key for key, values in self._requests.items() if not values or values[-1] <= cutoff
        ]
        for key in stale:
            self._requests.pop(key, None)
