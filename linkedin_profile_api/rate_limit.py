"""Best-effort per-process rate limiting, backed by API Gateway throttling in AWS."""

from __future__ import annotations

import asyncio
import time
from collections import deque


class InMemoryRateLimiter:
    def __init__(self, requests_per_minute: int, *, max_clients: int = 2048) -> None:
        self._limit = requests_per_minute
        self._max_clients = max_clients
        self._requests: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def allow(self, client_key: str) -> bool:
        now = time.monotonic()
        cutoff = now - 60
        async with self._lock:
            self._prune(cutoff)
            timestamps = self._requests.get(client_key)
            if timestamps is None:
                if len(self._requests) >= self._max_clients:
                    return False
                timestamps = deque()
                self._requests[client_key] = timestamps
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()
            if len(timestamps) >= self._limit:
                return False
            timestamps.append(now)
            return True

    def _prune(self, cutoff: float) -> None:
        stale = [
            key for key, values in self._requests.items() if not values or values[-1] <= cutoff
        ]
        for key in stale:
            self._requests.pop(key, None)
