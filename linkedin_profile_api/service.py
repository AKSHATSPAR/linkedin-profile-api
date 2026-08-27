"""Profile orchestration independent of the web framework."""

from __future__ import annotations

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

    async def get_profile(self, url: str, *, include_contact_info: bool = False) -> ProfileResponse:
        if include_contact_info and not self._allow_contact_info:
            raise ContactInfoDisabledError("Contact information is disabled on this deployment")
        parsed_url = parse_linkedin_profile_url(url)
        cache_key = f"{parsed_url.public_identifier}:{include_contact_info}"
        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached

        fetched = await self._client.fetch_profile(
            parsed_url.public_identifier,
            include_contact_info=include_contact_info,
        )
        profile = VoyagerParser(fetched.documents).parse(parsed_url.public_identifier)
        warnings = list(dict.fromkeys(fetched.warnings))
        response = ProfileResponse(
            meta=ResponseMeta(partial=bool(warnings), warnings=warnings),
            profile=profile,
        )
        await self._cache.set(cache_key, response)
        return response
