from __future__ import annotations

from linkedin_profile_api import cache as cache_module
from linkedin_profile_api.cache import ProfileCache
from linkedin_profile_api.models import Profile, ProfileResponse


async def test_cache_returns_a_copy_and_marks_it_cached() -> None:
    cache = ProfileCache(ttl_seconds=60, max_entries=2)
    response = ProfileResponse(
        profile=Profile(
            public_identifier="ada-lovelace",
            profile_url="https://www.linkedin.com/in/ada-lovelace/",
        )
    )

    await cache.set("ada", response)
    cached = await cache.get("ada")

    assert cached is not None
    assert cached.meta.cached is True
    assert response.meta.cached is False


async def test_cache_expiry_and_lru_eviction(monkeypatch: object) -> None:
    clock = [100.0]
    monkeypatch.setattr(cache_module.time, "monotonic", lambda: clock[0])  # type: ignore[attr-defined]
    cache = ProfileCache(ttl_seconds=10, max_entries=1)
    ada = ProfileResponse(
        profile=Profile(
            public_identifier="ada-lovelace",
            profile_url="https://www.linkedin.com/in/ada-lovelace/",
        )
    )
    grace = ProfileResponse(
        profile=Profile(
            public_identifier="grace-hopper",
            profile_url="https://www.linkedin.com/in/grace-hopper/",
        )
    )

    await cache.set("ada", ada)
    await cache.set("grace", grace)
    assert await cache.get("ada") is None
    assert await cache.get("grace") is not None

    clock[0] += 11
    assert await cache.get("grace") is None
