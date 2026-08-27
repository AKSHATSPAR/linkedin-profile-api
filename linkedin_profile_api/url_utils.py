"""Strict LinkedIn profile URL parsing.

The input is converted to a public identifier before any network request. This
prevents user-controlled hosts, paths, ports, or schemes from reaching the
upstream client.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from .errors import InvalidProfileUrlError

_PUBLIC_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,149}$")
_ALLOWED_HOSTS = {"linkedin.com", "www.linkedin.com", "in.linkedin.com"}
MAX_PROFILE_URL_LENGTH = 512


@dataclass(frozen=True, slots=True)
class LinkedInProfileUrl:
    public_identifier: str

    @property
    def canonical_url(self) -> str:
        return f"https://www.linkedin.com/in/{self.public_identifier}/"


def parse_linkedin_profile_url(value: str) -> LinkedInProfileUrl:
    if not isinstance(value, str) or len(value) > MAX_PROFILE_URL_LENGTH:
        raise InvalidProfileUrlError("The LinkedIn profile URL is too long")
    candidate = value.strip()
    if not candidate:
        raise InvalidProfileUrlError("A LinkedIn profile URL is required")
    if any(ord(character) < 32 or ord(character) == 127 for character in candidate):
        raise InvalidProfileUrlError("LinkedIn profile URLs must not contain control characters")

    if "://" not in candidate:
        candidate = f"https://{candidate}"

    try:
        parsed = urlsplit(candidate)
        hostname = parsed.hostname
        port = parsed.port
        username = parsed.username
        password = parsed.password
    except ValueError as exc:
        raise InvalidProfileUrlError("The LinkedIn profile URL is malformed") from exc
    if parsed.scheme != "https":
        raise InvalidProfileUrlError("LinkedIn profile URLs must use HTTPS")
    if hostname is None or hostname.lower() not in _ALLOWED_HOSTS:
        raise InvalidProfileUrlError("Only linkedin.com profile URLs are accepted")
    if port is not None:
        raise InvalidProfileUrlError("LinkedIn profile URLs must not specify a port")
    if username is not None or password is not None:
        raise InvalidProfileUrlError("LinkedIn profile URLs must not contain credentials")

    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) != 2 or parts[0].lower() != "in":
        raise InvalidProfileUrlError(
            "Expected a LinkedIn URL in the form https://linkedin.com/in/name"
        )

    identifier = parts[1]
    if not _PUBLIC_IDENTIFIER.fullmatch(identifier):
        raise InvalidProfileUrlError("The LinkedIn public identifier is invalid")

    return LinkedInProfileUrl(public_identifier=identifier)
