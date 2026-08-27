"""Runtime configuration and credential loading."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from .errors import CredentialsUnavailableError

_COOKIE_NAME_PATTERN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_MAX_COOKIE_HEADER_LENGTH = 32_768


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "LinkedIn Profile API"
    app_version: str = "1.0.0"
    log_level: str = "INFO"

    linkedin_li_at: SecretStr | None = None
    linkedin_jsessionid: SecretStr | None = None
    linkedin_cookie_header: SecretStr | None = None
    linkedin_secret_arn: str | None = None
    linkedin_user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    )
    linkedin_request_timeout_seconds: float = Field(default=6.0, gt=0, le=30)
    linkedin_total_timeout_seconds: float = Field(default=10.0, gt=0, le=45)
    linkedin_max_retries: int = Field(default=1, ge=0, le=2)
    linkedin_max_upstream_requests: int = Field(default=8, ge=1, le=16)
    linkedin_max_response_bytes: int = Field(default=2_000_000, ge=1024, le=10_000_000)
    linkedin_fetch_section_fallbacks: bool = False
    allow_contact_info: bool = False

    aws_region: str = "ap-south-1"
    cache_ttl_seconds: int = Field(default=300, ge=0, le=3600)
    cache_max_entries: int = Field(default=128, ge=1, le=1024)
    rate_limit_per_minute: int = Field(default=10, ge=1, le=120)
    rate_limit_max_clients: int = Field(default=2048, ge=16, le=16384)
    max_request_body_bytes: int = Field(default=4096, ge=512, le=65536)


@dataclass(frozen=True, slots=True)
class LinkedInCredentials:
    li_at: str
    jsessionid: str
    full_cookie_header: str | None = None

    @property
    def csrf_token(self) -> str:
        return self.jsessionid.strip('"')

    @property
    def cookie_header(self) -> str:
        if self.full_cookie_header is not None:
            return self.full_cookie_header
        quoted_jsessionid = self.jsessionid
        if not quoted_jsessionid.startswith('"'):
            quoted_jsessionid = f'"{quoted_jsessionid}"'
        return f"li_at={self.li_at}; JSESSIONID={quoted_jsessionid}"


class CredentialProvider:
    """Loads secrets without logging or exposing their values."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cached: LinkedInCredentials | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> LinkedInCredentials:
        if self._cached is not None:
            return self._cached

        async with self._lock:
            if self._cached is not None:
                return self._cached
            credentials = self._from_environment()
            if credentials is None and self._settings.linkedin_secret_arn:
                credentials = await asyncio.to_thread(self._from_secrets_manager)
            if credentials is None:
                raise CredentialsUnavailableError("LinkedIn credentials have not been configured")
            self._cached = credentials
            return credentials

    def clear(self) -> None:
        self._cached = None

    def _from_environment(self) -> LinkedInCredentials | None:
        li_at = self._settings.linkedin_li_at
        jsessionid = self._settings.linkedin_jsessionid
        if li_at is None or jsessionid is None:
            return None
        li_at_value = li_at.get_secret_value()
        jsessionid_value = jsessionid.get_secret_value()
        if not li_at_value.strip() and not jsessionid_value.strip():
            return None
        cookie_header = self._settings.linkedin_cookie_header
        cookie_header_value = cookie_header.get_secret_value() if cookie_header else None
        return self._validate(li_at_value, jsessionid_value, cookie_header_value)

    def _from_secrets_manager(self) -> LinkedInCredentials:
        secret_arn = self._settings.linkedin_secret_arn
        if secret_arn is None:
            raise CredentialsUnavailableError("No LinkedIn secret ARN is configured")
        try:
            client = boto3.client("secretsmanager", region_name=self._settings.aws_region)
            response = client.get_secret_value(SecretId=secret_arn)
        except (BotoCoreError, ClientError) as exc:
            raise CredentialsUnavailableError(
                "The configured LinkedIn secret could not be loaded"
            ) from exc
        if not isinstance(response, dict):
            raise CredentialsUnavailableError(
                "The configured LinkedIn secret returned an invalid response"
            )
        raw = response.get("SecretString")
        if not raw:
            raise CredentialsUnavailableError("The configured LinkedIn secret is empty")
        try:
            secret = json.loads(raw)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise CredentialsUnavailableError(
                "The configured LinkedIn secret is not valid JSON"
            ) from exc
        if not isinstance(secret, dict):
            raise CredentialsUnavailableError("The configured LinkedIn secret has an invalid shape")
        return self._validate(
            secret.get("li_at"),
            secret.get("jsessionid"),
            secret.get("cookie_header"),
        )

    @staticmethod
    def _validate(
        li_at: Any,
        jsessionid: Any,
        cookie_header: Any = None,
    ) -> LinkedInCredentials:
        if not isinstance(li_at, str) or len(li_at.strip()) < 20:
            raise CredentialsUnavailableError("The LinkedIn li_at value is invalid")
        if not isinstance(jsessionid, str) or not jsessionid.strip('"').startswith("ajax:"):
            raise CredentialsUnavailableError("The LinkedIn JSESSIONID value is invalid")
        normalized_li_at = li_at.strip()
        normalized_jsessionid = jsessionid.strip()
        normalized_cookie_header = CredentialProvider._validate_cookie_header(
            cookie_header,
            li_at=normalized_li_at,
            jsessionid=normalized_jsessionid,
        )
        return LinkedInCredentials(
            li_at=normalized_li_at,
            jsessionid=normalized_jsessionid,
            full_cookie_header=normalized_cookie_header,
        )

    @staticmethod
    def _validate_cookie_header(
        value: Any,
        *,
        li_at: str,
        jsessionid: str,
    ) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise CredentialsUnavailableError("The LinkedIn Cookie header is invalid")
        header = value.strip()
        if not header:
            return None
        if len(header) > _MAX_COOKIE_HEADER_LENGTH or any(
            ord(character) < 0x20 or ord(character) > 0x7E for character in header
        ):
            raise CredentialsUnavailableError("The LinkedIn Cookie header is invalid")

        cookies: dict[str, list[str]] = {}
        for segment in header.split(";"):
            pair = segment.strip()
            if not pair:
                continue
            if "=" not in pair:
                raise CredentialsUnavailableError("The LinkedIn Cookie header is invalid")
            name, cookie_value = pair.split("=", 1)
            name = name.strip()
            cookie_value = cookie_value.strip()
            if not _COOKIE_NAME_PATTERN.fullmatch(name):
                raise CredentialsUnavailableError("The LinkedIn Cookie header is invalid")
            cookies.setdefault(name, []).append(cookie_value)

        if cookies.get("li_at") != [li_at]:
            raise CredentialsUnavailableError(
                "The LinkedIn Cookie header does not match the li_at value"
            )
        header_jsessionids = cookies.get("JSESSIONID", [])
        if len(header_jsessionids) != 1 or header_jsessionids[0].strip('"') != jsessionid.strip(
            '"'
        ):
            raise CredentialsUnavailableError(
                "The LinkedIn Cookie header does not match the JSESSIONID value"
            )
        return header


@lru_cache
def get_settings() -> Settings:
    return Settings()
