from __future__ import annotations

import pytest

from linkedin_profile_api.url_utils import parse_linkedin_profile_url


@pytest.mark.parametrize(
    ("value", "identifier"),
    [
        ("https://www.linkedin.com/in/ada-lovelace/", "ada-lovelace"),
        ("linkedin.com/in/ada_lovelace", "ada_lovelace"),
        ("https://in.linkedin.com/in/ada-lovelace?trk=public", "ada-lovelace"),
    ],
)
def test_accepts_member_profile_urls(value: str, identifier: str) -> None:
    parsed = parse_linkedin_profile_url(value)
    assert parsed.public_identifier == identifier
    assert parsed.canonical_url == f"https://www.linkedin.com/in/{identifier}/"


@pytest.mark.parametrize(
    "value",
    [
        "http://www.linkedin.com/in/ada-lovelace",
        "https://evil.example/in/ada-lovelace",
        "https://www.linkedin.com:443/in/ada-lovelace",
        "https://user:password@www.linkedin.com/in/ada-lovelace",
        "https://www.linkedin.com/company/babbage",
        "https://www.linkedin.com/in/a",
        "https://www.linkedin.com/in/ada%2Flovelace",
    ],
)
def test_rejects_non_profile_or_unsafe_urls(value: str) -> None:
    with pytest.raises(ValueError):
        parse_linkedin_profile_url(value)
