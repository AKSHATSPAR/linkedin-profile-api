"""Import and validate the minimal LinkedIn session cookie set."""

from __future__ import annotations

from typing import Any

MAX_COOKIE_HEADER_LENGTH = 32_768
MAX_COOKIE_VALUE_LENGTH = 8_192

# Keep authentication, session continuity, locale, and routing state. Advertising,
# analytics, experimentation, and short-lived bot-management cookies are discarded.
SESSION_COOKIE_NAMES = (
    "bcookie",
    "bscookie",
    "li_rm",
    "li_sugr",
    "timezone",
    "JSESSIONID",
    "lang",
    "li_at",
    "liap",
    "lidc",
)
_SESSION_COOKIE_NAME_SET = frozenset(SESSION_COOKIE_NAMES)


class SessionCookieError(ValueError):
    """A session cookie input is unsafe, malformed, or internally inconsistent."""


def _validate_text(value: str, *, label: str, maximum: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise SessionCookieError(f"The {label} is empty")
    if len(normalized) > maximum:
        raise SessionCookieError(f"The {label} is too long")
    if any(ord(character) < 0x20 or ord(character) > 0x7E for character in normalized):
        raise SessionCookieError(f"The {label} contains unsafe characters")
    return normalized


def _validate_required_values(
    cookies: dict[str, str],
    *,
    li_at: str | None,
    jsessionid: str | None,
) -> None:
    cookie_li_at = cookies.get("li_at")
    if cookie_li_at is None or len(cookie_li_at) < 20:
        raise SessionCookieError("The imported cookies do not contain a valid li_at")
    cookie_jsessionid = cookies.get("JSESSIONID")
    if cookie_jsessionid is None or not cookie_jsessionid.strip('"').startswith("ajax:"):
        raise SessionCookieError("The imported cookies do not contain a valid JSESSIONID")
    if li_at is not None and cookie_li_at != li_at:
        raise SessionCookieError("The imported cookies do not match li_at")
    if jsessionid is not None and cookie_jsessionid.strip('"') != jsessionid.strip('"'):
        raise SessionCookieError("The imported cookies do not match JSESSIONID")


def import_cookie_header(
    value: str,
    *,
    li_at: str | None = None,
    jsessionid: str | None = None,
) -> dict[str, str]:
    """Extract an allowlisted cookie map while ignoring unrelated browser state."""

    header = value.strip()
    if header.casefold().startswith("cookie:"):
        header = header.split(":", 1)[1].strip()
    header = _validate_text(
        header,
        label="Cookie header",
        maximum=MAX_COOKIE_HEADER_LENGTH,
    )

    cookies: dict[str, str] = {}
    for segment in header.split(";"):
        pair = segment.strip()
        if "=" not in pair:
            continue
        name, cookie_value = pair.split("=", 1)
        name = name.strip()
        if name not in _SESSION_COOKIE_NAME_SET:
            continue
        if name in cookies:
            raise SessionCookieError("The Cookie header repeats a session cookie")
        cookies[name] = _validate_text(
            cookie_value,
            label="session cookie value",
            maximum=MAX_COOKIE_VALUE_LENGTH,
        )

    _validate_required_values(cookies, li_at=li_at, jsessionid=jsessionid)
    return cookies


def validate_cookie_map(
    value: Any,
    *,
    li_at: str,
    jsessionid: str,
) -> dict[str, str]:
    """Validate the structured cookie map loaded from a secret store."""

    if not isinstance(value, dict):
        raise SessionCookieError("The LinkedIn session cookies have an invalid shape")
    cookies: dict[str, str] = {}
    for name, cookie_value in value.items():
        if name not in _SESSION_COOKIE_NAME_SET or not isinstance(cookie_value, str):
            raise SessionCookieError("The LinkedIn session cookies contain an invalid entry")
        cookies[name] = _validate_text(
            cookie_value,
            label="session cookie value",
            maximum=MAX_COOKIE_VALUE_LENGTH,
        )
    _validate_required_values(cookies, li_at=li_at, jsessionid=jsessionid)
    return cookies
