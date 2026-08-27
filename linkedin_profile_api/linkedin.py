"""Minimal read-only client for LinkedIn's internal Voyager profile surface."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import CredentialProvider, Settings
from .errors import (
    AuthenticationError,
    ProfileNotFoundError,
    UpstreamRateLimitedError,
    UpstreamResponseError,
)
from .linkedin_identity import (
    has_malformed_public_identifier,
    profile_member_ids,
    profile_public_identifiers,
)

logger = logging.getLogger(__name__)


def _safe_media_type(value: str | None) -> str:
    """Return a bounded media type without logging provider-controlled parameters."""

    if value is None:
        return "missing"
    media_type = value.split(";", 1)[0].strip().casefold()
    allowed = frozenset("abcdefghijklmnopqrstuvwxyz0123456789!#$&^_.+-/")
    if not media_type or len(media_type) > 100 or any(char not in allowed for char in media_type):
        return "invalid"
    return media_type


def _log_rejection(event: str, response: httpx.Response) -> None:
    """Log enough to diagnose a rejection without URLs, bodies, or credentials."""

    logger.warning(
        "event=%s status=%d redirect=%s content_type=%s",
        event,
        response.status_code,
        "present" if response.headers.get("location") else "absent",
        _safe_media_type(response.headers.get("content-type")),
    )


@dataclass(slots=True)
class FetchResult:
    documents: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class _RequestBudgetExhausted(UpstreamResponseError):
    pass


class _AttemptBudget:
    """Concurrency-safe ceiling on physical authenticated upstream requests."""

    def __init__(self, maximum: int) -> None:
        self._remaining = maximum
        self._lock = asyncio.Lock()

    async def charge(self) -> None:
        async with self._lock:
            if self._remaining <= 0:
                raise _RequestBudgetExhausted("LinkedIn request budget was exhausted")
            self._remaining -= 1


def _primary_profile_candidates(document: dict[str, Any]) -> list[dict[str, Any]]:
    included = document.get("included")
    if not isinstance(included, list):
        return []
    profiles = [
        entity
        for entity in included
        if isinstance(entity, dict)
        and entity.get("$type") == "com.linkedin.voyager.dash.identity.profile.Profile"
    ]
    data = document.get("data")
    roots = data.get("*elements") if isinstance(data, dict) else None
    if not isinstance(roots, list):
        return profiles
    root_urns = {value for value in roots if isinstance(value, str)}
    return [entity for entity in profiles if entity.get("entityUrn") in root_urns]


def _matches_primary_identity(document: dict[str, Any], public_identifier: str) -> bool:
    expected = public_identifier.casefold()
    candidates = _primary_profile_candidates(document)
    legacy = document.get("profile")
    legacy_identifiers = profile_public_identifiers(legacy)
    if has_malformed_public_identifier(legacy):
        return False

    if candidates:
        for entity in candidates:
            if has_malformed_public_identifier(entity):
                return False
            identities = profile_public_identifiers(entity)
            if not identities or any(actual.casefold() != expected for actual in identities):
                return False
        if any(actual.casefold() != expected for actual in legacy_identifiers):
            return False

        member_ids = {
            member_id for entity in candidates for member_id in profile_member_ids(entity)
        }
        member_ids.update(profile_member_ids(legacy))
        return len(member_ids) <= 1

    return (
        bool(legacy_identifiers)
        and all(actual.casefold() == expected for actual in legacy_identifiers)
        and len(set(profile_member_ids(legacy))) <= 1
    )


class LinkedInClient:
    BASE_URL = "https://www.linkedin.com/voyager/api"
    PROFILE_DECORATIONS = (
        "com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-101",
        "com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-91",
    )
    SECTION_PATHS = (
        "skills",
        "certifications",
        "languages",
        "projects",
        "publications",
        "courses",
        "honors",
        "volunteerExperiences",
    )

    def __init__(
        self,
        settings: Settings,
        credential_provider: CredentialProvider,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._credential_provider = credential_provider
        self._transport = transport

    async def fetch_profile(
        self, public_identifier: str, *, include_contact_info: bool = False
    ) -> FetchResult:
        credentials = await self._credential_provider.get()
        headers = {
            "accept": "application/vnd.linkedin.normalized+json+2.1",
            "accept-language": "en-US,en;q=0.9",
            "csrf-token": credentials.csrf_token,
            "referer": f"https://www.linkedin.com/in/{public_identifier}/",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": self._settings.linkedin_user_agent,
            "x-li-lang": "en_US",
            "x-restli-protocol-version": "2.0.0",
        }
        timeout = httpx.Timeout(self._settings.linkedin_request_timeout_seconds)
        budget = _AttemptBudget(self._settings.linkedin_max_upstream_requests)
        try:
            async with asyncio.timeout(self._settings.linkedin_total_timeout_seconds):
                async with httpx.AsyncClient(
                    base_url=self.BASE_URL,
                    headers=headers,
                    cookies=credentials.cookies,
                    timeout=timeout,
                    follow_redirects=False,
                    transport=self._transport,
                ) as client:
                    result = FetchResult()
                    primary = await self._fetch_primary(
                        client,
                        public_identifier,
                        budget=budget,
                    )
                    primary["__source"] = "primary"
                    primary["__profile_identifier"] = public_identifier
                    result.documents.append(primary)

                    if self._settings.linkedin_fetch_section_fallbacks:
                        documents, warnings = await self._fetch_sections(
                            client,
                            public_identifier,
                            budget=budget,
                        )
                        result.documents.extend(documents)
                        result.warnings.extend(warnings)

                    if include_contact_info:
                        contact = await self._optional_get(
                            client,
                            f"/identity/profiles/{public_identifier}/profileContactInfo",
                            label="contact information",
                            budget=budget,
                        )
                        if contact.document:
                            contact.document["__source"] = "contact"
                            contact.document["__profile_identifier"] = public_identifier
                            result.documents.append(contact.document)
                        if contact.warning:
                            result.warnings.append(contact.warning)
                    return result
        except TimeoutError as exc:
            raise UpstreamResponseError("LinkedIn exceeded the request deadline") from exc

    async def _fetch_primary(
        self,
        client: httpx.AsyncClient,
        public_identifier: str,
        *,
        budget: _AttemptBudget,
    ) -> dict[str, Any]:
        last_error: UpstreamResponseError | None = None
        for decoration in self.PROFILE_DECORATIONS:
            try:
                document = await self._get_json(
                    client,
                    "/identity/dash/profiles",
                    params={
                        "decorationId": decoration,
                        "memberIdentity": public_identifier,
                        "q": "memberIdentity",
                    },
                    profile_lookup=True,
                    budget=budget,
                )
                if not _matches_primary_identity(document, public_identifier):
                    raise UpstreamResponseError(
                        "LinkedIn returned a profile with an unexpected identity"
                    )
                return document
            except _RequestBudgetExhausted:
                raise
            except UpstreamResponseError as exc:
                last_error = exc

        try:
            document = await self._get_json(
                client,
                f"/identity/profiles/{public_identifier}/profileView",
                profile_lookup=True,
                budget=budget,
            )
            if not _matches_primary_identity(document, public_identifier):
                raise UpstreamResponseError(
                    "LinkedIn returned a profile with an unexpected identity"
                )
            return document
        except _RequestBudgetExhausted:
            raise
        except UpstreamResponseError as exc:
            if last_error is not None:
                raise last_error from exc
            raise

    @dataclass(slots=True)
    class _OptionalResult:
        document: dict[str, Any] | None = None
        warning: str | None = None

    async def _fetch_sections(
        self,
        client: httpx.AsyncClient,
        public_identifier: str,
        *,
        budget: _AttemptBudget,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        semaphore = asyncio.Semaphore(3)

        async def fetch(section: str) -> LinkedInClient._OptionalResult:
            async with semaphore:
                return await self._optional_get(
                    client,
                    f"/identity/profiles/{public_identifier}/{section}",
                    params={"count": 100, "start": 0},
                    label=section,
                    budget=budget,
                )

        results = await asyncio.gather(*(fetch(section) for section in self.SECTION_PATHS))
        documents: list[dict[str, Any]] = []
        for section, item in zip(self.SECTION_PATHS, results, strict=True):
            if item.document is not None:
                item.document["__source"] = "section"
                item.document["__section"] = section
                item.document["__profile_identifier"] = public_identifier
                documents.append(item.document)
        warnings = [item.warning for item in results if item.warning is not None]
        return documents, warnings

    async def _optional_get(
        self,
        client: httpx.AsyncClient,
        path: str,
        *,
        label: str,
        budget: _AttemptBudget,
        params: dict[str, Any] | None = None,
    ) -> _OptionalResult:
        try:
            return self._OptionalResult(
                document=await self._get_json(
                    client,
                    path,
                    params=params,
                    budget=budget,
                )
            )
        except (ProfileNotFoundError, UpstreamResponseError):
            return self._OptionalResult(
                warning=f"LinkedIn did not expose {label} through this session"
            )

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        path: str,
        *,
        budget: _AttemptBudget,
        params: dict[str, Any] | None = None,
        profile_lookup: bool = False,
    ) -> dict[str, Any]:
        attempts = self._settings.linkedin_max_retries + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            await budget.charge()
            try:
                async with client.stream("GET", path, params=params) as response:
                    if response.status_code in {301, 302, 303, 307, 308, 401, 403}:
                        _log_rejection("linkedin_authentication_rejected", response)
                        self._credential_provider.clear()
                        raise AuthenticationError("The LinkedIn session is invalid or expired")
                    if response.status_code == 404 and profile_lookup:
                        raise ProfileNotFoundError(
                            "The LinkedIn profile was not found or is inaccessible"
                        )
                    if response.status_code in {404, 410}:
                        raise ProfileNotFoundError("This LinkedIn profile section is unavailable")
                    if response.status_code == 429:
                        raise UpstreamRateLimitedError(
                            "LinkedIn rate-limited the configured session"
                        )
                    if response.status_code >= 500 and attempt + 1 < attempts:
                        await asyncio.sleep(min(0.4 * (2**attempt), 2.0))
                        continue
                    if response.status_code >= 400:
                        raise UpstreamResponseError(
                            f"LinkedIn returned HTTP {response.status_code}"
                        )

                    if "json" not in response.headers.get("content-type", "").lower():
                        _log_rejection("linkedin_unexpected_content_type", response)
                        self._credential_provider.clear()
                        raise AuthenticationError("LinkedIn returned an unexpected login response")

                    declared = response.headers.get("content-length")
                    if declared is not None:
                        try:
                            if int(declared) > self._settings.linkedin_max_response_bytes:
                                raise UpstreamResponseError(
                                    "LinkedIn returned an oversized response"
                                )
                        except ValueError:
                            pass

                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(raw) + len(chunk) > self._settings.linkedin_max_response_bytes:
                            raise UpstreamResponseError("LinkedIn returned an oversized response")
                        raw.extend(chunk)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    await asyncio.sleep(min(0.4 * (2**attempt), 2.0))
                    continue
                raise UpstreamResponseError("LinkedIn could not be reached") from exc
            except (httpx.RequestError, httpx.StreamError) as exc:
                raise UpstreamResponseError("LinkedIn could not be reached") from exc

            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
                raise UpstreamResponseError("LinkedIn returned invalid JSON") from exc
            if not isinstance(payload, dict):
                raise UpstreamResponseError("LinkedIn returned an unexpected response shape")
            return payload

        raise UpstreamResponseError("LinkedIn request failed") from last_error
