from __future__ import annotations

from typing import Any

import httpx
import pytest

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


async def test_forged_forwarded_for_does_not_change_limiter_identity() -> None:
    app = create_app(_settings(rate_limit_per_minute=1))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post(
            "/v1/profiles",
            headers={"x-forwarded-for": "198.51.100.1"},
            json={"url": "https://example.com/first"},
        )
        second = await client.post(
            "/v1/profiles",
            headers={"x-forwarded-for": "203.0.113.2"},
            json={"url": "https://example.com/second"},
        )

    assert first.status_code == 422
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "client_rate_limited"


async def test_distinct_network_peers_get_distinct_limiter_buckets() -> None:
    app = create_app(_settings(rate_limit_per_minute=1))
    first_transport = httpx.ASGITransport(app=app, client=("198.51.100.10", 1000))
    second_transport = httpx.ASGITransport(app=app, client=("198.51.100.11", 1001))
    async with (
        httpx.AsyncClient(transport=first_transport, base_url="http://test") as first_client,
        httpx.AsyncClient(transport=second_transport, base_url="http://test") as second_client,
    ):
        first = await first_client.get("/v1/profiles", params={"url": "https://example.com/first"})
        second = await second_client.get(
            "/v1/profiles", params={"url": "https://example.com/second"}
        )

    assert first.status_code == 422
    assert second.status_code == 422


async def test_rejects_declared_and_streamed_oversized_bodies() -> None:
    app = create_app(_settings(max_request_body_bytes=512, rate_limit_per_minute=10))

    async def chunks() -> Any:
        yield b'{"url":"https://www.linkedin.com/in/ada-lovelace","padding":"'
        yield b"x" * 600
        yield b'"}'

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        declared = await client.post(
            "/v1/profiles",
            content=b"x" * 513,
            headers={"content-type": "application/json", "x-request-id": "declared-limit"},
        )
        streamed = await client.post(
            "/v1/profiles",
            content=chunks(),
            headers={"content-type": "application/json", "x-request-id": "streamed-limit"},
        )

    for response, request_id in (
        (declared, "declared-limit"),
        (streamed, "streamed-limit"),
    ):
        assert response.status_code == 413
        assert response.json()["error"] == {
            "code": "request_too_large",
            "message": "The request body is too large",
            "request_id": request_id,
        }
        assert response.headers["x-request-id"] == request_id
        assert response.headers["x-content-type-options"] == "nosniff"


async def test_get_and_post_enforce_same_url_length_bound() -> None:
    app = create_app(_settings())
    overlong = "https://www.linkedin.com/in/" + ("a" * 600)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        get_response = await client.get("/v1/profiles", params={"url": overlong})
        post_response = await client.post("/v1/profiles", json={"url": overlong})

    assert get_response.status_code == 422
    assert post_response.status_code == 422


async def test_wrong_upstream_identity_is_a_stable_502(
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

    async def linkedin(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, headers={"content-type": "application/json"}, json=wrong)

    app = create_app(_settings(), transport=httpx.MockTransport(linkedin))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/profiles",
            json={"url": "https://linkedin.com/in/ada-lovelace"},
        )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "linkedin_upstream_error"
    assert "another-person" not in response.text


async def test_conflicting_profile_graph_is_rejected_and_never_cached(
    dash_profile: dict[str, Any],
) -> None:
    mixed = {
        **dash_profile,
        "included": [
            *dash_profile["included"],
            {
                "$type": "com.linkedin.voyager.dash.identity.profile.Position",
                "entityUrn": "urn:li:fsd_profilePosition:(other,role)",
                "title": "Wrong Member Secret Role",
            },
        ],
    }
    requests = 0

    async def linkedin(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        del request
        requests += 1
        return httpx.Response(200, headers={"content-type": "application/json"}, json=mixed)

    app = create_app(_settings(), transport=httpx.MockTransport(linkedin))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.get(
            "/v1/profiles",
            params={"url": "https://linkedin.com/in/ada-lovelace"},
        )
        second = await client.get(
            "/v1/profiles",
            params={"url": "https://linkedin.com/in/ada-lovelace"},
        )

    assert first.status_code == 502
    assert second.status_code == 502
    assert requests == 2
    assert "Wrong Member Secret Role" not in first.text


async def test_foreign_legacy_section_is_rejected_and_never_cached() -> None:
    legacy = {
        "profile": {
            "miniProfile": {
                "entityUrn": "urn:li:fs_miniProfile:legacy-member",
                "publicIdentifier": "ada-lovelace",
                "firstName": "Ada",
                "lastName": "Lovelace",
            }
        },
        "positionView": {
            "elements": [
                {
                    "entityUrn": "urn:li:fs_position:(other,role)",
                    "title": "Foreign Legacy Secret",
                }
            ]
        },
    }
    requests = 0

    async def linkedin(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if request.url.path.endswith("/profileView"):
            return httpx.Response(200, headers={"content-type": "application/json"}, json=legacy)
        return httpx.Response(400, headers={"content-type": "application/json"})

    app = create_app(_settings(), transport=httpx.MockTransport(linkedin))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.get(
            "/v1/profiles",
            params={"url": "https://linkedin.com/in/ada-lovelace"},
        )
        second = await client.get(
            "/v1/profiles",
            params={"url": "https://linkedin.com/in/ada-lovelace"},
        )

    assert first.status_code == 502
    assert second.status_code == 502
    assert requests == 6
    assert "Foreign Legacy Secret" not in first.text


async def test_cache_key_is_case_insensitive(
    dash_profile: dict[str, Any],
) -> None:
    requests = 0

    async def linkedin(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        del request
        requests += 1
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=dash_profile,
        )

    app = create_app(_settings(), transport=httpx.MockTransport(linkedin))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.get(
            "/v1/profiles",
            params={"url": "https://linkedin.com/in/ada-lovelace"},
        )
        second = await client.get(
            "/v1/profiles",
            params={"url": "https://linkedin.com/in/ADA-LOVELACE"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["meta"]["cached"] is True
    assert requests == 1


async def test_openapi_documents_request_limits() -> None:
    app = create_app(_settings())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        schema = (await client.get("/openapi.json")).json()

    post = schema["paths"]["/v1/profiles"]["post"]
    request_url = schema["components"]["schemas"]["ProfileRequest"]["properties"]["url"]
    assert "413" in post["responses"]
    assert request_url["maxLength"] == 512


async def test_root_health_and_request_id_sanitization() -> None:
    app = create_app(_settings())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        root = await client.get("/", headers={"x-request-id": "not valid / id"})
        health = await client.get("/health", headers={"x-request-id": "health-check-1"})

    assert root.status_code == 200
    assert root.json()["endpoint"] == "/v1/profiles"
    assert len(root.headers["x-request-id"]) == 32
    assert health.json()["status"] == "ok"
    assert health.headers["x-request-id"] == "health-check-1"


@pytest.mark.parametrize(
    ("status", "expected_status", "code"),
    [
        (404, 404, "profile_not_found"),
        (429, 429, "linkedin_rate_limited"),
        (302, 503, "linkedin_authentication_failed"),
    ],
)
async def test_upstream_failures_map_to_stable_http_errors(
    status: int,
    expected_status: int,
    code: str,
) -> None:
    async def linkedin(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            status,
            headers={
                "content-type": "application/json",
                "location": "https://linkedin.com/login",
            },
        )

    app = create_app(_settings(), transport=httpx.MockTransport(linkedin))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/v1/profiles",
            params={"url": "https://linkedin.com/in/ada-lovelace"},
        )

    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == code
    if status == 429:
        assert response.headers["retry-after"] == "60"
