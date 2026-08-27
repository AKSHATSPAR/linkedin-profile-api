from __future__ import annotations

from linkedin_profile_api import rate_limit
from linkedin_profile_api.rate_limit import InMemoryRateLimiter


async def test_rate_limiter_caps_clients_and_prunes_stale_entries(monkeypatch: object) -> None:
    clock = [100.0]
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: clock[0])  # type: ignore[attr-defined]
    limiter = InMemoryRateLimiter(1, max_clients=2)

    assert await limiter.allow("198.51.100.1") is True
    assert await limiter.allow("198.51.100.2") is True
    assert await limiter.allow("203.0.113.3") is False
    assert await limiter.allow("198.51.100.1") is False

    clock[0] += 61
    assert await limiter.allow("203.0.113.3") is True
