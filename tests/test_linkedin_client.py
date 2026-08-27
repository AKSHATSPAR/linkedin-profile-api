from __future__ import annotations

from typing import Any

import httpx
import pytest

from linkedin_profile_api.config import CredentialProvider, Settings
from linkedin_profile_api.errors import AuthenticationError, UpstreamRateLimitedError
from linkedin_profile_api.linkedin import LinkedInClient


def _client(
    handler: Any,
    *,
    section_fallbacks: bool = False,
    retries: int = 0,
) -> LinkedInClient:
    settings = Settings(
        _env_file=None,
        linkedin_li_at="a" * 64,
        linkedin_jsessionid='"ajax:session-id"',
        linkedin_fetch_section_fallbacks=section_fallbacks,
        linkedin_max_retries=retries,
    )
    return LinkedInClient(
        settings,
        CredentialProvider(settings),
        transport=httpx.MockTransport(handler),
    )


async def test_tries_a_second_decoration_version(dash_profile: dict[str, Any]) -> None:
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["csrf-token"] == "ajax:session-id"
        assert 'JSESSIONID="ajax:session-id"' in request.headers["cookie"]
        if len(calls) == 1:
            return httpx.Response(400, headers={"content-type": "application/json"})
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=dash_profile,
        )

    result = await _client(handler).fetch_profile("ada-lovelace")

    assert result.documents == [dash_profile]
    assert len(calls) == 2
    assert "FullProfileWithEntities-101" in str(calls[0].url)
    assert "FullProfileWithEntities-91" in str(calls[1].url)


async def test_optional_sections_degrade_to_warnings(
    dash_profile: dict[str, Any],
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/dash/profiles"):
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=dash_profile,
            )
        return httpx.Response(410, headers={"content-type": "application/json"})

    result = await _client(handler, section_fallbacks=True).fetch_profile("ada-lovelace")

    assert result.documents == [dash_profile]
    assert len(result.warnings) == 8
    assert "skills" in result.warnings[0]


@pytest.mark.parametrize("status", [302, 401, 403])
async def test_rejects_login_redirects_and_auth_failures(status: int) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(status, headers={"location": "https://linkedin.com/login"})

    with pytest.raises(AuthenticationError):
        await _client(handler).fetch_profile("ada-lovelace")


async def test_surfaces_linkedin_rate_limiting() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(429, headers={"content-type": "application/json"})

    with pytest.raises(UpstreamRateLimitedError):
        await _client(handler).fetch_profile("ada-lovelace")


async def test_retries_transient_server_errors(dash_profile: dict[str, Any]) -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        del request
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, headers={"content-type": "application/json"})
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=dash_profile,
        )

    result = await _client(handler, retries=1).fetch_profile("ada-lovelace")

    assert result.documents == [dash_profile]
    assert attempts == 2
