from __future__ import annotations

from typing import Any

from linkedin_profile_api.parser import VoyagerParser


def test_parses_normalized_dash_entities(dash_profile: dict[str, Any]) -> None:
    profile = VoyagerParser([dash_profile]).parse("ada-lovelace")

    assert profile.full_name == "Ada Lovelace"
    assert profile.headline == "Analytical Engine Programmer"
    assert profile.about == "Exploring how machines can manipulate symbols."
    assert profile.location.display_name == "London, England"
    assert profile.images.profile is not None
    assert profile.images.profile.url == "https://media.example.test/ada-large.jpg"
    assert profile.experience[0].company_name == "Babbage Works"
    assert profile.experience[0].date_range is not None
    assert profile.experience[0].date_range.start is not None
    assert profile.experience[0].date_range.start.year == 1842
    assert profile.education[0].field_of_study == "Mathematics"
    assert profile.skills[0].name == "Mathematics"
    assert profile.certifications[0].issuing_organization == "Royal Society"
    assert profile.languages[0].proficiency == "NATIVE_OR_BILINGUAL"


def test_merges_optional_contact_document(dash_profile: dict[str, Any]) -> None:
    profile = VoyagerParser(
        [dash_profile, {"emailAddress": "ada@example.test", "websites": []}]
    ).parse("ada-lovelace")

    assert profile.contact_info is not None
    assert profile.contact_info.email == "ada@example.test"
