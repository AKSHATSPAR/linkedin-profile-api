from __future__ import annotations

from typing import Any

import pytest

from linkedin_profile_api.session_cookies import (
    SessionCookieError,
    import_cookie_header,
    validate_cookie_map,
)


def test_import_keeps_only_session_relevant_cookies() -> None:
    li_at = "a" * 64
    header = (
        f'Cookie: analytics=discard; bcookie="browser"; legacy flag; '
        f'li_at={li_at}; JSESSIONID="ajax:session"; '
        "g_state={nonstandard}; lang=v=2&lang=en-us; __cf_bm=discard"
    )

    cookies = import_cookie_header(header, li_at=li_at, jsessionid="ajax:session")

    assert cookies == {
        "bcookie": '"browser"',
        "li_at": li_at,
        "JSESSIONID": '"ajax:session"',
        "lang": "v=2&lang=en-us",
    }


def test_import_can_derive_required_credentials_from_header() -> None:
    li_at = "a" * 64

    cookies = import_cookie_header(f'li_at={li_at}; JSESSIONID="ajax:session"; analytics=discard')

    assert cookies == {"li_at": li_at, "JSESSIONID": '"ajax:session"'}


@pytest.mark.parametrize(
    ("header", "message"),
    [
        ("", "empty"),
        ("x=" + ("a" * 32_768), "too long"),
        ("x=one\ny=two", "unsafe characters"),
        (f"li_at={'a' * 64}; JSESSIONID=ajax:wrong", "JSESSIONID"),
        ("li_at=wrong; JSESSIONID=ajax:session", "li_at"),
        (
            f"li_at={'a' * 64}; li_at={'a' * 64}; JSESSIONID=ajax:session",
            "repeats",
        ),
    ],
)
def test_import_rejects_unsafe_or_inconsistent_inputs(header: str, message: str) -> None:
    with pytest.raises(SessionCookieError, match=message) as captured:
        import_cookie_header(
            header,
            li_at="a" * 64,
            jsessionid="ajax:session",
        )

    assert "ajax:wrong" not in str(captured.value)


@pytest.mark.parametrize(
    "cookies",
    [
        None,
        [],
        {"unknown": "value", "li_at": "a" * 64, "JSESSIONID": "ajax:session"},
        {"li_at": "a" * 64, "JSESSIONID": 1},
        {"li_at": "a" * 64, "JSESSIONID": "ajax:session\nunsafe"},
    ],
)
def test_structured_cookie_map_rejects_invalid_entries(cookies: Any) -> None:
    with pytest.raises(SessionCookieError):
        validate_cookie_map(
            cookies,
            li_at="a" * 64,
            jsessionid="ajax:session",
        )


def test_structured_cookie_map_returns_a_copy() -> None:
    source = {"li_at": "a" * 64, "JSESSIONID": "ajax:session", "liap": "true"}

    result = validate_cookie_map(
        source,
        li_at="a" * 64,
        jsessionid="ajax:session",
    )

    assert result == source
    assert result is not source
