from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from linkedin_profile_api.errors import ParseError
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
        [
            dash_profile,
            {
                "__source": "contact",
                "__profile_identifier": "ada-lovelace",
                "emailAddress": "ada@example.test",
                "websites": [],
            },
        ]
    ).parse("ada-lovelace", include_contact_info=True)

    assert profile.contact_info is not None
    assert profile.contact_info.email == "ada@example.test"


def test_contact_document_requires_provenance_and_explicit_opt_in(
    dash_profile: dict[str, Any],
) -> None:
    untrusted = {"emailAddress": "wrong@example.test"}
    annotated = {
        "__source": "contact",
        "__profile_identifier": "ada-lovelace",
        "emailAddress": "ada@example.test",
    }

    assert VoyagerParser([dash_profile, untrusted]).parse("ada-lovelace").contact_info is None
    assert VoyagerParser([dash_profile, annotated]).parse("ada-lovelace").contact_info is None


def test_rejects_wrong_or_ambiguous_primary_identity(
    dash_profile: dict[str, Any],
) -> None:
    wrong = deepcopy(dash_profile)
    for entity in wrong["included"]:
        if entity.get("$type") == "com.linkedin.voyager.dash.identity.profile.Profile":
            entity["publicIdentifier"] = "another-person"

    with pytest.raises(ParseError, match="conflicting profile identities"):
        VoyagerParser([wrong]).parse("ada-lovelace")

    with pytest.raises(ParseError, match="no recognizable profile"):
        VoyagerParser([{}, dash_profile]).parse("ada-lovelace")

    mixed = deepcopy(dash_profile)
    mixed["included"].append(
        {
            "$type": "com.linkedin.voyager.dash.identity.profile.Position",
            "entityUrn": "urn:li:fsd_profilePosition:(other,role)",
            "title": "Wrong Member Secret Role",
        }
    )
    with pytest.raises(ParseError, match="owned by another member"):
        VoyagerParser([mixed]).parse("ada-lovelace")

    extra_profile_only = deepcopy(dash_profile)
    extra_profile_only["included"].append(
        {
            "$type": "com.linkedin.voyager.dash.identity.profile.Profile",
            "entityUrn": "urn:li:fsd_profile:other",
            "publicIdentifier": "another-person",
        }
    )
    assert (
        VoyagerParser([extra_profile_only]).parse("ada-lovelace").public_identifier
        == "ada-lovelace"
    )


def test_section_entities_require_matching_fetch_provenance(
    dash_profile: dict[str, Any],
) -> None:
    project = {
        "$type": "com.linkedin.voyager.dash.identity.profile.Project",
        "entityUrn": "urn:li:fsd_profileProject:(member-123,engine)",
        "title": "Analytical Engine",
    }
    wrong_section = {
        "__source": "section",
        "__section": "projects",
        "__profile_identifier": "another-person",
        "included": [project],
    }
    right_section = {
        **wrong_section,
        "__profile_identifier": "ada-lovelace",
    }

    wrong_profile = VoyagerParser([dash_profile, wrong_section]).parse("ada-lovelace")
    right_profile = VoyagerParser([dash_profile, right_section]).parse("ada-lovelace")

    assert wrong_profile.projects == []
    assert right_profile.projects[0].name == "Analytical Engine"


def test_loose_section_elements_require_target_member_ownership(
    dash_profile: dict[str, Any],
) -> None:
    section = {
        "__source": "section",
        "__section": "experience",
        "__profile_identifier": "ada-lovelace",
        "elements": [
            {
                "entityUrn": "urn:li:fsd_profilePosition:(other,role)",
                "title": "Wrong Member Secret Role",
            }
        ],
    }

    with pytest.raises(ParseError, match="owned by another member"):
        VoyagerParser([dash_profile, section]).parse("ada-lovelace")

    section["elements"][0]["entityUrn"] = "urn:li:fsd_profilePosition:(member-123,verified-role)"
    profile = VoyagerParser([dash_profile, section]).parse("ada-lovelace")

    assert any(item.title == "Wrong Member Secret Role" for item in profile.experience)


def test_malformed_dates_and_image_dimensions_are_ignored(
    dash_profile: dict[str, Any],
) -> None:
    malformed = deepcopy(dash_profile)
    profile_entity = next(
        item
        for item in malformed["included"]
        if item.get("$type") == "com.linkedin.voyager.dash.identity.profile.Profile"
    )
    artifacts = profile_entity["profilePicture"]["displayImageReference"]["vectorImage"][
        "artifacts"
    ]
    artifacts[0]["width"] = "not-a-number"
    position = next(
        item for item in malformed["included"] if str(item.get("$type", "")).endswith("Position")
    )
    position["dateRange"]["start"]["month"] = 99

    profile = VoyagerParser([malformed]).parse("ada-lovelace")

    assert profile.images.profile is not None
    assert profile.experience[0].date_range is not None
    assert profile.experience[0].date_range.start is not None
    assert profile.experience[0].date_range.start.month is None


def test_document_and_entity_counts_are_bounded(dash_profile: dict[str, Any]) -> None:
    with pytest.raises(ParseError, match="too many response documents"):
        VoyagerParser([dash_profile] * 17)

    primary = deepcopy(dash_profile)
    primary["included"].extend({"entityUrn": f"urn:test:{index}"} for index in range(2000))
    with pytest.raises(ParseError, match="too many profile entities"):
        VoyagerParser([primary])


def test_identity_bound_legacy_profile_is_supported() -> None:
    profile = VoyagerParser(
        [
            {
                "profile": {
                    "publicIdentifier": "ada-lovelace",
                    "firstName": "Ada",
                    "lastName": "Lovelace",
                }
            }
        ]
    ).parse("ADA-LOVELACE")

    assert profile.full_name == "Ada Lovelace"


def test_legacy_included_entities_require_same_member_ownership() -> None:
    document = {
        "profile": {
            "entityUrn": "urn:li:fs_profile:legacy-member",
            "miniProfile": {
                "entityUrn": "urn:li:fs_miniProfile:legacy-member",
                "publicIdentifier": "ada-lovelace",
                "firstName": "Ada",
                "lastName": "Lovelace",
            },
        },
        "included": [
            {
                "$type": "com.linkedin.voyager.dash.identity.profile.Position",
                "entityUrn": "urn:li:fsd_profilePosition:(legacy-member,role)",
                "title": "Verified Role",
            }
        ],
    }

    profile = VoyagerParser([document]).parse("ada-lovelace")
    assert profile.experience[0].title == "Verified Role"

    document["included"][0]["entityUrn"] = "urn:li:fsd_profilePosition:(other,role)"
    with pytest.raises(ParseError, match="owned by another member"):
        VoyagerParser([document]).parse("ada-lovelace")


def test_ownership_gate_covers_every_normalized_entity_type_variant() -> None:
    document = {
        "profile": {
            "entityUrn": "urn:li:fs_profile:legacy-member",
            "publicIdentifier": "ada-lovelace",
        },
        "included": [
            {
                "$type": "com.linkedin.voyager.identity.ProfileCertificationDetail",
                "entityUrn": "urn:li:fsd_profileCertification:(legacy-member,1)",
                "name": "Verified Certificate",
            }
        ],
    }

    profile = VoyagerParser([document]).parse("ada-lovelace")
    assert profile.certifications[0].name == "Verified Certificate"

    document["included"][0]["entityUrn"] = "urn:li:fsd_profileCertification:(other-member,1)"
    with pytest.raises(ParseError, match="owned by another member"):
        VoyagerParser([document]).parse("ada-lovelace")


def test_legacy_scoped_entities_require_a_provable_member_id() -> None:
    document = {
        "profile": {"publicIdentifier": "ada-lovelace", "firstName": "Ada"},
        "included": [
            {
                "$type": "com.linkedin.voyager.dash.identity.profile.Skill",
                "entityUrn": "urn:li:fsd_profileSkill:(unknown-member,1)",
                "name": "Private Skill",
            }
        ],
    }

    with pytest.raises(ParseError, match="owned by another member"):
        VoyagerParser([document]).parse("ada-lovelace")


@pytest.mark.parametrize(
    "profile",
    [
        {
            "publicIdentifier": "ada-lovelace",
            "miniProfile": {"publicIdentifier": "another-person"},
        },
        {
            "publicIdentifier": "another-person",
            "miniProfile": {"publicIdentifier": "ada-lovelace"},
        },
        {
            "entityUrn": "urn:li:fs_profile:legacy-member",
            "publicIdentifier": "ada-lovelace",
            "miniProfile": {
                "entityUrn": "urn:li:fs_miniProfile:other-member",
                "publicIdentifier": "ada-lovelace",
            },
        },
        {
            "publicIdentifier": 123,
            "miniProfile": {"publicIdentifier": "ada-lovelace"},
        },
    ],
)
def test_conflicting_legacy_identity_representations_are_rejected(
    profile: dict[str, Any],
) -> None:
    with pytest.raises(ParseError, match="profile"):
        VoyagerParser([{"profile": profile}]).parse("ada-lovelace")


def test_matching_case_variant_legacy_identities_remain_supported() -> None:
    profile = VoyagerParser(
        [
            {
                "profile": {
                    "entityUrn": "urn:li:fs_profile:legacy-member",
                    "publicIdentifier": "Ada-Lovelace",
                    "miniProfile": {
                        "entityUrn": "urn:li:fs_miniProfile:legacy-member",
                        "publicIdentifier": "ADA-LOVELACE",
                        "firstName": "Ada",
                        "lastName": "Lovelace",
                    },
                }
            }
        ]
    ).parse("ada-lovelace")

    assert profile.linkedin_id == "legacy-member"
    assert profile.full_name == "Ada Lovelace"


def test_parses_remaining_supported_sections_and_relationship(
    dash_profile: dict[str, Any],
) -> None:
    enriched = deepcopy(dash_profile)
    profile_entity = next(
        item
        for item in enriched["included"]
        if item.get("$type") == "com.linkedin.voyager.dash.identity.profile.Profile"
    )
    profile_entity["*memberRelationship"] = "urn:li:fsd_relationship:1"
    enriched["included"].extend(
        [
            {
                "$type": "com.linkedin.voyager.dash.identity.profile.MemberRelationship",
                "entityUrn": "urn:li:fsd_relationship:1",
                "memberRelationshipUnion": {"noConnection": {"memberDistance": "DISTANCE_2"}},
            },
            {
                "$type": "com.linkedin.voyager.dash.identity.profile.Project",
                "entityUrn": "urn:li:fsd_profileProject:(member-123,1)",
                "title": "Analytical Engine",
                "description": "Mechanical computation",
                "timePeriod": {"start": {"year": 1842}},
                "url": "https://example.test/engine",
            },
            {
                "$type": "com.linkedin.voyager.dash.identity.profile.Publication",
                "entityUrn": "urn:li:fsd_profilePublication:(member-123,1)",
                "name": "Notes",
                "publisher": "Scientific Memoirs",
                "date": {"year": 1843},
            },
            {
                "$type": "com.linkedin.voyager.dash.identity.profile.Course",
                "entityUrn": "urn:li:fsd_profileCourse:(member-123,1)",
                "name": "Mathematics",
                "number": "MATH-1",
            },
            {
                "$type": "com.linkedin.voyager.dash.identity.profile.Honor",
                "entityUrn": "urn:li:fsd_profileHonor:(member-123,1)",
                "title": "Pioneer",
                "issuer": "Royal Society",
                "issuedOn": {"year": 1843},
            },
            {
                "$type": "com.linkedin.voyager.dash.identity.profile.VolunteerExperience",
                "entityUrn": "urn:li:fsd_profileVolunteerExperience:(member-123,1)",
                "role": "Translator",
                "organization": "Scientific Memoirs",
                "cause": "EDUCATION",
                "timePeriod": {"start": {"year": 1842}},
            },
        ]
    )

    profile = VoyagerParser([enriched]).parse("ada-lovelace")

    assert profile.connection_degree == 2
    assert profile.projects[0].name == "Analytical Engine"
    assert profile.publications[0].publisher == "Scientific Memoirs"
    assert profile.courses[0].number == "MATH-1"
    assert profile.honors[0].issuer == "Royal Society"
    assert profile.volunteer_experience[0].role == "Translator"


def test_malformed_relationship_shape_fails_closed(dash_profile: dict[str, Any]) -> None:
    malformed = deepcopy(dash_profile)
    profile_entity = next(
        item
        for item in malformed["included"]
        if item.get("$type") == "com.linkedin.voyager.dash.identity.profile.Profile"
    )
    profile_entity["*memberRelationship"] = "urn:li:fsd_relationship:malformed"
    malformed["included"].append(
        {
            "entityUrn": "urn:li:fsd_relationship:malformed",
            "memberRelationshipUnion": {"noConnection": "not-an-object"},
        }
    )

    with pytest.raises(ParseError, match="malformed profile data"):
        VoyagerParser([malformed]).parse("ada-lovelace")


def test_parses_identity_bound_legacy_view_sections() -> None:
    document = {
        "profile": {
            "miniProfile": {
                "entityUrn": "urn:li:fs_miniProfile:legacy-member",
                "publicIdentifier": "ada-lovelace",
                "firstName": "Ada",
                "lastName": "Lovelace",
            }
        },
        "projectView": {
            "elements": [
                {
                    "entityUrn": "urn:li:fs_project:(legacy-member,1)",
                    "title": "Difference Engine",
                }
            ]
        },
    }

    profile = VoyagerParser([document]).parse("ada-lovelace")

    assert profile.projects[0].name == "Difference Engine"

    foreign = deepcopy(document)
    foreign["projectView"]["elements"][0]["entityUrn"] = "urn:li:fs_project:(other,1)"
    with pytest.raises(ParseError, match="owned by another member"):
        VoyagerParser([foreign]).parse("ada-lovelace")


def test_normalizes_upstream_model_validation_failure(
    dash_profile: dict[str, Any],
) -> None:
    malformed = deepcopy(dash_profile)
    profile_entity = next(
        item
        for item in malformed["included"]
        if item.get("$type") == "com.linkedin.voyager.dash.identity.profile.Profile"
    )
    profile_entity["followerCount"] = -1

    with pytest.raises(ParseError, match="malformed profile data"):
        VoyagerParser([malformed]).parse("ada-lovelace")


def test_empty_document_collection_is_rejected() -> None:
    with pytest.raises(ParseError, match="no profile document"):
        VoyagerParser([]).parse("ada-lovelace")
