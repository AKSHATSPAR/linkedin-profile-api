"""Profile orchestration independent of the web framework."""

from __future__ import annotations

import asyncio

from .cache import ProfileCache
from .errors import ContactInfoDisabledError
from .linkedin import LinkedInClient
from .models import ProfileResponse, ResponseMeta
from .parser import VoyagerParser
from .url_utils import parse_linkedin_profile_url


class ProfileService:
    def __init__(
        self,
        client: LinkedInClient,
        cache: ProfileCache,
        *,
        allow_contact_info: bool = False,
    ) -> None:
        self._client = client
        self._cache = cache
        self._allow_contact_info = allow_contact_info
        self._inflight: dict[str, asyncio.Task[ProfileResponse]] = {}
        self._inflight_lock = asyncio.Lock()

    async def get_profile(self, url: str, *, include_contact_info: bool = False) -> ProfileResponse:
        if include_contact_info and not self._allow_contact_info:
            raise ContactInfoDisabledError("Contact information is disabled on this deployment")
        parsed_url = parse_linkedin_profile_url(url)
        cache_key = f"{parsed_url.public_identifier.casefold()}:{include_contact_info}"
        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached

        async with self._inflight_lock:
            task = self._inflight.get(cache_key)
            if task is None:
                cached = await self._cache.get(cache_key)
                if cached is not None:
                    return cached
                task = asyncio.create_task(
                    self._fetch_and_cache(
                        cache_key,
                        parsed_url.public_identifier,
                        include_contact_info=include_contact_info,
                    )
                )
                self._inflight[cache_key] = task

        response = await asyncio.shield(task)
        return response.model_copy(deep=True)

    async def _fetch_and_cache(
        self,
        cache_key: str,
        public_identifier: str,
        *,
        include_contact_info: bool,
    ) -> ProfileResponse:
        current = asyncio.current_task()
        assert current is not None
        try:
            return await self._fetch_response(
                cache_key,
                public_identifier,
                include_contact_info=include_contact_info,
            )
        finally:
            async with self._inflight_lock:
                if self._inflight.get(cache_key) is current:
                    self._inflight.pop(cache_key, None)

    async def _fetch_response(
        self,
        cache_key: str,
        public_identifier: str,
        *,
        include_contact_info: bool,
    ) -> ProfileResponse:
        fetched = await self._client.fetch_profile(
            public_identifier,
            include_contact_info=include_contact_info,
        )
        profile = VoyagerParser(fetched.documents).parse(
            public_identifier,
            include_contact_info=include_contact_info,
        )
        warnings = list(dict.fromkeys(fetched.warnings))
        response = ProfileResponse(
            meta=ResponseMeta(partial=bool(warnings), warnings=warnings),
            profile=profile,
        )
        await self._cache.set(cache_key, response)
        return response
