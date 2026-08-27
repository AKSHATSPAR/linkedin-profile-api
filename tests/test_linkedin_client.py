from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from linkedin_profile_api.config import CredentialProvider, Settings
from linkedin_profile_api.errors import (
    AuthenticationError,
    ProfileNotFoundError,
    UpstreamRateLimitedError,
    UpstreamResponseError,
)
from linkedin_profile_api.linkedin import LinkedInClient


def _client(
    handler: Any,
    *,
    section_fallbacks: bool = False,
    retries: int = 0,
    max_requests: int = 8,
    max_response_bytes: int = 2_000_000,
    total_timeout: float = 10,
) -> LinkedInClient:
    settings = Settings(
        _env_file=None,
        linkedin_li_at="a" * 64,
        linkedin_jsessionid='"ajax:session-id"',
        linkedin_fetch_section_fallbacks=section_fallbacks,
        linkedin_max_retries=retries,
        linkedin_max_upstream_requests=max_requests,
        linkedin_max_response_bytes=max_response_bytes,
        linkedin_total_timeout_seconds=total_timeout,
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

    assert result.documents[0]["included"] == dash_profile["included"]
    assert result.documents[0]["__source"] == "primary"
    assert result.documents[0]["__profile_identifier"] == "ada-lovelace"
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

    assert result.documents[0]["included"] == dash_profile["included"]
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

    assert result.documents[0]["included"] == dash_profile["included"]
    assert attempts == 2


async def test_empty_or_wrong_primary_falls_through_to_next_decoration(
    dash_profile: dict[str, Any],
) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        del request
        calls += 1
        payload = {} if calls == 1 else dash_profile
        return httpx.Response(200, headers={"content-type": "application/json"}, json=payload)

    result = await _client(handler).fetch_profile("ada-lovelace")

    assert result.documents[0]["__source"] == "primary"
    assert calls == 2


async def test_wrong_profile_identity_is_never_accepted(
    dash_profile: dict[str, Any],
) -> None:
    wrong = {
        **dash_profile,
        "included": [
            {**item, "publicIdentifier": "another-person"}
            if item.get("$type") == "com.linkedin.voyager.dash.identity.profile.Profile"
            else item
            for item in dash_profile["included"]
        ],
    }
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        del request
        calls += 1
        return httpx.Response(200, headers={"content-type": "application/json"}, json=wrong)

    with pytest.raises(UpstreamResponseError, match="unexpected identity"):
        await _client(handler).fetch_profile("ada-lovelace")

    assert calls == 3


async def test_non_root_profile_does_not_override_primary_identity(
    dash_profile: dict[str, Any],
) -> None:
    mixed = {
        **dash_profile,
        "included": [
            *dash_profile["included"],
            {
                "$type": "com.linkedin.voyager.dash.identity.profile.Profile",
                "entityUrn": "urn:li:fsd_profile:other",
                "publicIdentifier": "another-person",
            },
        ],
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, headers={"content-type": "application/json"}, json=mixed)

    result = await _client(handler).fetch_profile("ada-lovelace")

    assert result.documents[0]["__profile_identifier"] == "ada-lovelace"


async def test_physical_attempt_budget_caps_concurrent_section_retries(
    dash_profile: dict[str, Any],
) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if request.url.path.endswith("/dash/profiles"):
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=dash_profile,
            )
        return httpx.Response(503, headers={"content-type": "application/json"})

    result = await _client(
        handler,
        section_fallbacks=True,
        retries=2,
        max_requests=5,
    ).fetch_profile("ada-lovelace", include_contact_info=True)

    assert calls == 5
    assert len(result.warnings) == 9


async def test_rejects_oversized_upstream_responses() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        del request
        calls += 1
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"padding":"' + (b"x" * 2000) + b'"}',
        )

    with pytest.raises(UpstreamResponseError, match="oversized"):
        await _client(handler, max_response_bytes=1024).fetch_profile("ada-lovelace")

    assert calls == 3


async def test_total_deadline_bounds_slow_upstream() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        await asyncio.sleep(0.05)
        return httpx.Response(500, headers={"content-type": "application/json"})

    with pytest.raises(UpstreamResponseError, match="deadline"):
        await _client(handler, retries=1, total_timeout=0.01).fetch_profile("ada-lovelace")


async def test_accepts_identity_bound_legacy_profile() -> None:
    legacy = {
        "profile": {
            "publicIdentifier": "ada-lovelace",
            "firstName": "Ada",
            "lastName": "Lovelace",
        }
    }
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if request.url.path.endswith("/profileView"):
            return httpx.Response(200, headers={"content-type": "application/json"}, json=legacy)
        return httpx.Response(400, headers={"content-type": "application/json"})

    result = await _client(handler).fetch_profile("ada-lovelace")

    assert calls == 3
    assert result.documents[0]["profile"]["firstName"] == "Ada"


async def test_successful_sections_and_contact_are_provenance_tagged(
    dash_profile: dict[str, Any],
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/dash/profiles"):
            payload = dash_profile
        elif request.url.path.endswith("/profileContactInfo"):
            payload = {"emailAddress": "ada@example.test"}
        else:
            payload = {"elements": []}
        return httpx.Response(200, headers={"content-type": "application/json"}, json=payload)

    result = await _client(
        handler,
        section_fallbacks=True,
        max_requests=10,
    ).fetch_profile("ada-lovelace", include_contact_info=True)

    assert len(result.documents) == 10
    assert result.warnings == []
    assert {document["__source"] for document in result.documents} == {
        "primary",
        "section",
        "contact",
    }
    assert all(document["__profile_identifier"] == "ada-lovelace" for document in result.documents)


async def test_primary_request_budget_exhaustion_is_not_fallbackable() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        del request
        calls += 1
        return httpx.Response(200, headers={"content-type": "application/json"}, json={})

    with pytest.raises(UpstreamResponseError, match="request budget"):
        await _client(handler, max_requests=1).fetch_profile("ada-lovelace")

    assert calls == 1


async def test_profile_not_found_is_not_retried() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        del request
        calls += 1
        return httpx.Response(404, headers={"content-type": "application/json"})

    with pytest.raises(ProfileNotFoundError, match="not found"):
        await _client(handler, retries=2).fetch_profile("ada-lovelace")

    assert calls == 1


async def test_rejects_non_json_login_page_and_clears_credentials() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, headers={"content-type": "text/html"}, text="login")

    client = _client(handler)
    with pytest.raises(AuthenticationError, match="login response"):
        await client.fetch_profile("ada-lovelace")

    assert client._credential_provider._cached is None


@pytest.mark.parametrize(
    ("content", "message"),
    [(b"not-json", "invalid JSON"), (b"[]", "unexpected response shape")],
)
async def test_rejects_malformed_json_shapes(content: bytes, message: str) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=content,
        )

    with pytest.raises(UpstreamResponseError, match=message):
        await _client(handler).fetch_profile("ada-lovelace")


async def test_retries_network_errors_then_fails() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(UpstreamResponseError, match="could not be reached"):
        await _client(handler, retries=1).fetch_profile("ada-lovelace")

    assert attempts == 6


async def test_actual_stream_size_is_bounded_without_content_length() -> None:
    class LargeStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> Any:
            yield b"{" + (b"x" * 1024)
            yield b"x}"

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=LargeStream(),
        )

    with pytest.raises(UpstreamResponseError, match="oversized"):
        await _client(handler, max_response_bytes=1024).fetch_profile("ada-lovelace")
