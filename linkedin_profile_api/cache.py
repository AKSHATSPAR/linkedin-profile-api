"""Small process-local TTL cache.

Lambda reuses execution environments, so this removes duplicate LinkedIn calls without
persisting profile data to a database. The cache is deliberately bounded and ephemeral.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass

from .models import ProfileResponse


@dataclass(slots=True)
class _Entry:
    expires_at: float
    response: ProfileResponse


class ProfileCache:
    def __init__(self, *, ttl_seconds: int, max_entries: int) -> None:
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._items: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> ProfileResponse | None:
        if self._ttl == 0:
            return None
        async with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return None
            if entry.expires_at <= time.monotonic():
                self._items.pop(key, None)
                return None
            self._items.move_to_end(key)
            response = deepcopy(entry.response)
            response.meta.cached = True
            return response

    async def set(self, key: str, response: ProfileResponse) -> None:
        if self._ttl == 0:
            return
        async with self._lock:
            self._items[key] = _Entry(
                expires_at=time.monotonic() + self._ttl,
                response=deepcopy(response),
            )
            self._items.move_to_end(key)
            while len(self._items) > self._max_entries:
                self._items.popitem(last=False)
