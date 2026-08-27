"""Runtime configuration and credential loading."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from functools import lru_cache
from time import monotonic
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from .errors import CredentialsUnavailableError
from .session_cookies import SessionCookieError, import_cookie_header, validate_cookie_map


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
    linkedin_secret_cache_ttl_seconds: int = Field(default=300, ge=30, le=3600)
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
    session_cookies: tuple[tuple[str, str], ...] = ()

    @property
    def csrf_token(self) -> str:
        return self.jsessionid.strip('"')

    @property
    def cookies(self) -> dict[str, str]:
        if self.session_cookies:
            return dict(self.session_cookies)
        quoted_jsessionid = self.jsessionid
        if not quoted_jsessionid.startswith('"'):
            quoted_jsessionid = f'"{quoted_jsessionid}"'
        return {"li_at": self.li_at, "JSESSIONID": quoted_jsessionid}


class CredentialProvider:
    """Loads secrets without logging or exposing their values."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cached: LinkedInCredentials | None = None
        self._cache_expires_at = 0.0
        self._lock = asyncio.Lock()

    async def get(self) -> LinkedInCredentials:
        if self._cached is not None and monotonic() < self._cache_expires_at:
            return self._cached

        async with self._lock:
            if self._cached is not None and monotonic() < self._cache_expires_at:
                return self._cached
            credentials = self._from_environment()
            if credentials is not None:
                cache_expires_at = float("inf")
            elif self._settings.linkedin_secret_arn:
                credentials = await asyncio.to_thread(self._from_secrets_manager)
                cache_expires_at = monotonic() + self._settings.linkedin_secret_cache_ttl_seconds
            else:
                cache_expires_at = 0.0
            if credentials is None:
                raise CredentialsUnavailableError("LinkedIn credentials have not been configured")
            self._cached = credentials
            self._cache_expires_at = cache_expires_at
            return credentials

    def clear(self) -> None:
        self._cached = None
        self._cache_expires_at = 0.0

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
        return self._validate(
            li_at_value,
            jsessionid_value,
            cookie_header=cookie_header_value,
        )

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
            cookies=secret.get("cookies"),
            cookie_header=secret.get("cookie_header"),
        )

    @staticmethod
    def _validate(
        li_at: Any,
        jsessionid: Any,
        *,
        cookies: Any = None,
        cookie_header: Any = None,
    ) -> LinkedInCredentials:
        if not isinstance(li_at, str) or len(li_at.strip()) < 20:
            raise CredentialsUnavailableError("The LinkedIn li_at value is invalid")
        if not isinstance(jsessionid, str) or not jsessionid.strip('"').startswith("ajax:"):
            raise CredentialsUnavailableError("The LinkedIn JSESSIONID value is invalid")
        normalized_li_at = li_at.strip()
        normalized_jsessionid = jsessionid.strip()
        try:
            if cookies is not None:
                if cookie_header is not None:
                    raise SessionCookieError(
                        "The LinkedIn secret contains conflicting cookie formats"
                    )
                normalized_cookies = validate_cookie_map(
                    cookies,
                    li_at=normalized_li_at,
                    jsessionid=normalized_jsessionid,
                )
            elif cookie_header is not None:
                if not isinstance(cookie_header, str):
                    raise SessionCookieError("The LinkedIn Cookie header is invalid")
                normalized_cookies = import_cookie_header(
                    cookie_header,
                    li_at=normalized_li_at,
                    jsessionid=normalized_jsessionid,
                )
            else:
                normalized_cookies = {}
        except SessionCookieError as exc:
            raise CredentialsUnavailableError(str(exc)) from exc
        return LinkedInCredentials(
            li_at=normalized_li_at,
            jsessionid=normalized_jsessionid,
            session_cookies=tuple(normalized_cookies.items()),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
