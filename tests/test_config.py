from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from botocore.exceptions import ClientError

from linkedin_profile_api import config
from linkedin_profile_api.config import CredentialProvider, Settings
from linkedin_profile_api.errors import CredentialsUnavailableError


async def test_environment_credentials_are_cached_and_can_be_cleared() -> None:
    settings = Settings(
        _env_file=None,
        linkedin_li_at="a" * 64,
        linkedin_jsessionid="ajax:session",
    )
    provider = CredentialProvider(settings)

    first = await provider.get()
    second = await provider.get()
    provider.clear()
    third = await provider.get()

    assert first is second
    assert third is not first
    assert first.csrf_token == "ajax:session"
    assert 'JSESSIONID="ajax:session"' in first.cookie_header


async def test_complete_cookie_header_is_validated_and_used() -> None:
    li_at = "a" * 64
    cookie_header = (
        f'bcookie=v=2&example; li_at={li_at}; JSESSIONID="ajax:session"; lang=v=2&lang=en-us'
    )
    settings = Settings(
        _env_file=None,
        linkedin_li_at=li_at,
        linkedin_jsessionid="ajax:session",
        linkedin_cookie_header=cookie_header,
    )

    credentials = await CredentialProvider(settings).get()

    assert credentials.cookie_header == cookie_header
    assert credentials.csrf_token == "ajax:session"


async def test_complete_cookie_header_preserves_repeated_optional_cookies() -> None:
    li_at = "a" * 64
    cookie_header = f"optional=one; li_at={li_at}; optional=two; JSESSIONID=ajax:session"

    credentials = await CredentialProvider(
        Settings(
            _env_file=None,
            linkedin_li_at=li_at,
            linkedin_jsessionid="ajax:session",
            linkedin_cookie_header=cookie_header,
        )
    ).get()

    assert credentials.cookie_header == cookie_header


async def test_secrets_manager_load_is_coalesced_and_cached(monkeypatch: Any) -> None:
    calls = 0

    class FakeSecretsManager:
        def get_secret_value(self, *, SecretId: str) -> dict[str, str]:
            nonlocal calls
            assert SecretId == "arn:aws:secretsmanager:ap-south-1:123:secret:test"
            calls += 1
            return {
                "SecretString": json.dumps(
                    {
                        "li_at": "b" * 64,
                        "jsessionid": '"ajax:from-secret"',
                        "cookie_header": (
                            f'li_at={"b" * 64}; JSESSIONID="ajax:from-secret"; lang=en-us'
                        ),
                    }
                )
            }

    monkeypatch.setattr(
        config.boto3,
        "client",
        lambda service, region_name: FakeSecretsManager(),
    )
    settings = Settings(
        _env_file=None,
        linkedin_secret_arn="arn:aws:secretsmanager:ap-south-1:123:secret:test",
    )
    provider = CredentialProvider(settings)

    credentials = await asyncio.gather(*(provider.get() for _ in range(8)))

    assert calls == 1
    assert all(item.li_at == "b" * 64 for item in credentials)
    assert all("lang=en-us" in item.cookie_header for item in credentials)


async def test_blank_environment_values_fall_back_to_secrets_manager(monkeypatch: Any) -> None:
    class FakeSecretsManager:
        def get_secret_value(self, *, SecretId: str) -> dict[str, str]:
            del SecretId
            return {
                "SecretString": json.dumps({"li_at": "b" * 64, "jsessionid": "ajax:from-secret"})
            }

    monkeypatch.setattr(
        config.boto3,
        "client",
        lambda service, region_name: FakeSecretsManager(),
    )
    provider = CredentialProvider(
        Settings(
            _env_file=None,
            linkedin_li_at="",
            linkedin_jsessionid="",
            linkedin_secret_arn="arn:aws:secretsmanager:ap-south-1:123:secret:test",
        )
    )

    credentials = await provider.get()

    assert credentials.li_at == "b" * 64
    assert credentials.csrf_token == "ajax:from-secret"


@pytest.mark.parametrize(
    "secret_string",
    [
        None,
        "not-json",
        "{}",
        "[]",
        json.dumps({"li_at": "short", "jsessionid": "ajax:x"}),
    ],
)
def test_rejects_empty_or_malformed_secret_values(
    monkeypatch: Any,
    secret_string: str | None,
) -> None:
    class FakeSecretsManager:
        def get_secret_value(self, *, SecretId: str) -> dict[str, str | None]:
            del SecretId
            return {"SecretString": secret_string}

    monkeypatch.setattr(
        config.boto3,
        "client",
        lambda service, region_name: FakeSecretsManager(),
    )
    provider = CredentialProvider(
        Settings(
            _env_file=None,
            linkedin_secret_arn="arn:aws:secretsmanager:ap-south-1:123:secret:test",
        )
    )

    with pytest.raises(CredentialsUnavailableError):
        provider._from_secrets_manager()


def test_secret_loader_requires_an_arn() -> None:
    provider = CredentialProvider(Settings(_env_file=None))

    with pytest.raises(CredentialsUnavailableError, match="No LinkedIn secret ARN"):
        provider._from_secrets_manager()


@pytest.mark.parametrize(
    "cookie_header",
    [
        "li_at=wrong; JSESSIONID=ajax:session",
        f"li_at={'a' * 64}; JSESSIONID=ajax:wrong",
        f"li_at={'a' * 64}; JSESSIONID=ajax:session\r\nX-Test: value",
        f"li_at={'a' * 64}; JSESSIONID=ajax:session\tbad",
        f"li_at={'a' * 64}; li_at={'a' * 64}; JSESSIONID=ajax:session",
        "x=" + ("a" * 32_768),
    ],
)
def test_rejects_invalid_complete_cookie_headers(cookie_header: str) -> None:
    with pytest.raises(CredentialsUnavailableError, match="Cookie header"):
        CredentialProvider._validate("a" * 64, "ajax:session", cookie_header)


def test_secrets_manager_sdk_failures_are_sanitized(monkeypatch: Any) -> None:
    class FakeSecretsManager:
        def get_secret_value(self, *, SecretId: str) -> dict[str, str]:
            del SecretId
            raise ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "provider detail"}},
                "GetSecretValue",
            )

    monkeypatch.setattr(
        config.boto3,
        "client",
        lambda service, region_name: FakeSecretsManager(),
    )
    provider = CredentialProvider(
        Settings(
            _env_file=None,
            linkedin_secret_arn="arn:aws:secretsmanager:ap-south-1:123:secret:test",
        )
    )

    with pytest.raises(CredentialsUnavailableError, match="could not be loaded") as captured:
        provider._from_secrets_manager()

    assert "provider detail" not in str(captured.value)
