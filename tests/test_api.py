from __future__ import annotations

from typing import Any

import httpx

from linkedin_profile_api.app import create_app
from linkedin_profile_api.config import Settings


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "linkedin_li_at": "a" * 64,
        "linkedin_jsessionid": "ajax:1234567890",
        "linkedin_fetch_section_fallbacks": False,
        "rate_limit_per_minute": 10,
        "cache_ttl_seconds": 60,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


async def test_profile_endpoint_and_cache(dash_profile: dict[str, Any]) -> None:
    requests: list[httpx.Request] = []

    async def linkedin(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=dash_profile,
        )

    app = create_app(_settings(), transport=httpx.MockTransport(linkedin))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post(
            "/v1/profiles",
            json={"url": "https://linkedin.com/in/ada-lovelace"},
        )
        second = await client.get(
            "/v1/profiles",
            params={"url": "https://linkedin.com/in/ada-lovelace"},
        )

    assert first.status_code == 200
    assert first.json()["profile"]["full_name"] == "Ada Lovelace"
    assert first.headers["cache-control"] == "no-store"
    assert second.json()["meta"]["cached"] is True
    assert len(requests) == 1
    assert requests[0].headers["csrf-token"] == "ajax:1234567890"
    assert "li_at=" in requests[0].headers["cookie"]


async def test_invalid_url_is_a_stable_validation_error() -> None:
    app = create_app(_settings())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/v1/profiles", json={"url": "https://example.com/private"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


async def test_missing_server_credentials_returns_503() -> None:
    app = create_app(Settings(_env_file=None, linkedin_fetch_section_fallbacks=False))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/profiles",
            json={"url": "https://linkedin.com/in/ada-lovelace"},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "credentials_unavailable"


async def test_contact_info_requires_operator_opt_in() -> None:
    app = create_app(_settings(allow_contact_info=False))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/profiles",
            json={
                "url": "https://linkedin.com/in/ada-lovelace",
                "include_contact_info": True,
            },
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "contact_info_disabled"
