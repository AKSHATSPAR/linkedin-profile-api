from __future__ import annotations

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
