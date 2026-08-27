from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

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


async def test_secrets_manager_load_is_coalesced_and_cached(monkeypatch: Any) -> None:
    calls = 0

    class FakeSecretsManager:
        def get_secret_value(self, *, SecretId: str) -> dict[str, str]:
            nonlocal calls
            assert SecretId == "arn:aws:secretsmanager:ap-south-1:123:secret:test"
            calls += 1
            return {
                "SecretString": json.dumps({"li_at": "b" * 64, "jsessionid": '"ajax:from-secret"'})
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


@pytest.mark.parametrize(
    "secret_string",
    [None, "not-json", "{}", json.dumps({"li_at": "short", "jsessionid": "ajax:x"})],
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
