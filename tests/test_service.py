from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

import pytest

from linkedin_profile_api.cache import ProfileCache
from linkedin_profile_api.errors import UpstreamResponseError
from linkedin_profile_api.linkedin import FetchResult
from linkedin_profile_api.service import ProfileService


class _FakeClient:
    def __init__(self, document: dict[str, Any], *, failures: int = 0) -> None:
        self.document = document
        self.failures = failures
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def fetch_profile(
        self, public_identifier: str, *, include_contact_info: bool = False
    ) -> FetchResult:
        del public_identifier, include_contact_info
        self.calls += 1
        self.started.set()
        await self.release.wait()
        if self.failures:
            self.failures -= 1
            raise UpstreamResponseError("temporary failure")
        return FetchResult(documents=[deepcopy(self.document)])


def _service(client: Any, *, ttl: int = 60) -> ProfileService:
    return ProfileService(client, ProfileCache(ttl_seconds=ttl, max_entries=8))


async def test_concurrent_same_key_misses_share_one_fetch(
    dash_profile: dict[str, Any],
) -> None:
    client = _FakeClient(dash_profile)
    service = _service(client)
    tasks = [
        asyncio.create_task(service.get_profile("https://linkedin.com/in/ada-lovelace"))
        for _ in range(12)
    ]
    await client.started.wait()
    client.release.set()

    responses = await asyncio.gather(*tasks)
    cached = await service.get_profile("https://linkedin.com/in/ADA-LOVELACE")

    assert client.calls == 1
    assert all(response.meta.cached is False for response in responses)
    assert len({id(response) for response in responses}) == len(responses)
    assert cached.meta.cached is True


async def test_failed_flight_is_removed_and_can_be_retried(
    dash_profile: dict[str, Any],
) -> None:
    client = _FakeClient(dash_profile, failures=1)
    client.release.set()
    service = _service(client, ttl=0)

    with pytest.raises(UpstreamResponseError, match="temporary"):
        await service.get_profile("https://linkedin.com/in/ada-lovelace")
    response = await service.get_profile("https://linkedin.com/in/ada-lovelace")

    assert response.profile.full_name == "Ada Lovelace"
    assert client.calls == 2


async def test_waiter_cancellation_does_not_cancel_shared_fetch(
    dash_profile: dict[str, Any],
) -> None:
    client = _FakeClient(dash_profile)
    service = _service(client, ttl=0)
    cancelled = asyncio.create_task(service.get_profile("https://linkedin.com/in/ada-lovelace"))
    await client.started.wait()
    survivor = asyncio.create_task(service.get_profile("https://linkedin.com/in/ada-lovelace"))
    await asyncio.sleep(0)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    client.release.set()

    response = await survivor

    assert response.profile.full_name == "Ada Lovelace"
    assert client.calls == 1
