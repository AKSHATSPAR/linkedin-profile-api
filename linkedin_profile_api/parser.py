"""Normalize legacy and modern Voyager profile responses into a stable schema."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .errors import ParseError
from .models import (
    Certification,
    ContactInfo,
    Course,
    DateRange,
    DateValue,
    Education,
    Experience,
    Honor,
    ImageAsset,
    Language,
    Location,
    Profile,
    ProfileImages,
    Project,
    Publication,
    Skill,
    VolunteerExperience,
)


def _urn_id(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value.rsplit(":", 1)[-1]


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, dict):
        for key in ("text", "name", "localizedName", "defaultLocalizedName"):
            result = _text(value.get(key))
            if result:
                return result
    return None


def _date(value: Any) -> DateValue | None:
    if not isinstance(value, dict):
        return None
    result = DateValue(year=value.get("year"), month=value.get("month"), day=value.get("day"))
    return result if any((result.year, result.month, result.day)) else None


def _date_range(value: Any) -> DateRange | None:
    if not isinstance(value, dict):
        return None
    start = _date(value.get("start") or value.get("startDate"))
    end = _date(value.get("end") or value.get("endDate"))
    result = DateRange(start=start, end=end, present=start is not None and end is None)
    return result if start or end else None


def _image(value: Any) -> ImageAsset | None:
    """Find the largest LinkedIn VectorImage artifact in common response shapes."""
    if not isinstance(value, dict):
        return None

    for key in (
        "vectorImage",
        "com.linkedin.common.VectorImage",
        "displayImageReference",
        "displayImage",
        "picture",
        "logo",
    ):
        nested = value.get(key)
        image = _image(nested)
        if image:
            return image

    root = value.get("rootUrl")
    artifacts = value.get("artifacts")
    if isinstance(root, str) and isinstance(artifacts, list) and artifacts:
        artifact = max(
            (item for item in artifacts if isinstance(item, dict)),
            key=lambda item: int(item.get("width", 0)) * int(item.get("height", 0)),
            default=None,
        )
        if artifact:
            segment = artifact.get("fileIdentifyingUrlPathSegment") or artifact.get("url")
            if isinstance(segment, str):
                url = segment if segment.startswith("http") else f"{root}{segment}"
                return ImageAsset(
                    url=url, width=artifact.get("width"), height=artifact.get("height")
                )
    if isinstance(root, str):
        return ImageAsset(url=root)

    for nested in value.values():
        image = _image(nested)
        if image:
            return image
    return None


def _dedupe[T](items: Iterable[T], key: Any) -> list[T]:
    result: list[T] = []
    seen: set[Any] = set()
    for item in items:
        marker = key(item)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(item)
    return result


class VoyagerParser:
    def __init__(self, raw_documents: Iterable[dict[str, Any]]) -> None:
        self.documents = [doc for doc in raw_documents if isinstance(doc, dict)]
        self.entities: list[dict[str, Any]] = []
        for document in self.documents:
            included = document.get("included")
            if isinstance(included, list):
                self.entities.extend(item for item in included if isinstance(item, dict))
        self.urn_map = {
            entity["entityUrn"]: entity
            for entity in self.entities
            if isinstance(entity.get("entityUrn"), str)
        }

    def parse(self, public_identifier: str) -> Profile:
        legacy = self._legacy_profile_view()
        entity = self._profile_entity(public_identifier)
        if entity is None and legacy is None:
            raise ParseError("LinkedIn returned no recognizable profile entity")

        if legacy is not None:
            profile = self._parse_legacy(legacy, public_identifier)
        else:
            assert entity is not None
            profile = self._parse_dash(entity, public_identifier)

        self._merge_entity_sections(profile)
        self._merge_legacy_sections(profile)
        self._merge_contact_info(profile)
        return profile

    def _legacy_profile_view(self) -> dict[str, Any] | None:
        for document in self.documents:
            if isinstance(document.get("profile"), dict):
                return document
        return None

    def _profile_entity(self, public_identifier: str) -> dict[str, Any] | None:
        candidates = [
            entity
            for entity in self.entities
            if entity.get("$type") == "com.linkedin.voyager.dash.identity.profile.Profile"
        ]
        exact = [
            entity for entity in candidates if entity.get("publicIdentifier") == public_identifier
        ]
        pool = exact or candidates
        return max(pool, key=lambda item: len(item), default=None)

    def _resolve(self, entity: dict[str, Any], *fields: str) -> dict[str, Any] | None:
        for field in fields:
            value = entity.get(field)
            if isinstance(value, str) and value in self.urn_map:
                return self.urn_map[value]
            if isinstance(value, dict):
                return value
        return None

    def _parse_dash(self, entity: dict[str, Any], public_identifier: str) -> Profile:
        geo = self._resolve(entity, "*geo")
        if geo is None and isinstance(entity.get("geoLocation"), dict):
            geo = self._resolve(entity["geoLocation"], "*geo", "geoUrn")
        industry = self._resolve(entity, "*industry")

        first_name = _text(entity.get("firstName"))
        last_name = _text(entity.get("lastName"))
        location = _text(entity.get("locationName")) or _text(geo)
        country_code = None
        if isinstance(entity.get("location"), dict):
            country_code = _text(entity["location"].get("countryCode"))

        return Profile(
            linkedin_id=_urn_id(entity.get("entityUrn")),
            public_identifier=public_identifier,
            profile_url=f"https://www.linkedin.com/in/{public_identifier}/",
            first_name=first_name,
            last_name=last_name,
            full_name=" ".join(part for part in (first_name, last_name) if part) or None,
            headline=_text(entity.get("headline")),
            about=_text(entity.get("summary")),
            location=Location(display_name=location, country_code=country_code),
            industry=_text(industry) or _text(entity.get("industryName")),
            connection_degree=self._connection_degree(entity),
            follower_count=entity.get("followerCount"),
            connection_count=entity.get("connectionCount"),
            images=ProfileImages(
                profile=_image(entity.get("profilePicture")) or _image(entity.get("picture")),
                background=_image(entity.get("backgroundPicture")),
            ),
        )

    def _parse_legacy(self, document: dict[str, Any], public_identifier: str) -> Profile:
        entity = document["profile"]
        mini = entity.get("miniProfile") if isinstance(entity.get("miniProfile"), dict) else {}
        first_name = _text(entity.get("firstName")) or _text(mini.get("firstName"))
        last_name = _text(entity.get("lastName")) or _text(mini.get("lastName"))
        return Profile(
            linkedin_id=_urn_id(mini.get("entityUrn") or entity.get("entityUrn")),
            public_identifier=public_identifier,
            profile_url=f"https://www.linkedin.com/in/{public_identifier}/",
            first_name=first_name,
            last_name=last_name,
            full_name=" ".join(part for part in (first_name, last_name) if part) or None,
            headline=_text(entity.get("headline")) or _text(mini.get("occupation")),
            about=_text(entity.get("summary")),
            location=Location(
                display_name=_text(entity.get("locationName")),
                country_code=_text(
                    entity.get("location", {}).get("countryCode")
                    if isinstance(entity.get("location"), dict)
                    else None
                ),
            ),
            industry=_text(entity.get("industryName")),
            images=ProfileImages(profile=_image(mini) or _image(entity)),
        )

    def _connection_degree(self, profile: dict[str, Any]) -> int | None:
        relationship = self._resolve(profile, "*memberRelationship")
        candidates = [relationship] if relationship else []
        candidates.extend(
            entity
            for entity in self.entities
            if str(entity.get("$type", "")).endswith("MemberRelationship")
        )
        for item in candidates:
            if not isinstance(item, dict):
                continue
            union = item.get("memberRelationshipUnion") or item.get("memberRelationshipData")
            if not isinstance(union, dict):
                continue
            if any(key in union for key in ("connectedMember", "connected", "connection")):
                return 1
            distance = (union.get("noConnection") or {}).get("memberDistance")
            if distance in {"DISTANCE_1", "DISTANCE_2", "DISTANCE_3"}:
                return int(distance[-1])
        return None

    def _merge_entity_sections(self, profile: Profile) -> None:
        experiences: list[Experience] = []
        educations: list[Education] = []
        skills: list[Skill] = []
        certifications: list[Certification] = []
        languages: list[Language] = []
        projects: list[Project] = []
        publications: list[Publication] = []
        courses: list[Course] = []
        honors: list[Honor] = []
        volunteer: list[VolunteerExperience] = []

        for entity in self.entities:
            type_name = str(entity.get("$type", "")).lower()
            if type_name.endswith(".position"):
                company = self._resolve(entity, "*company", "company")
                company_name = _text(entity.get("companyName")) or _text(company)
                company_identifier = (
                    company.get("universalName") if isinstance(company, dict) else None
                )
                experiences.append(
                    Experience(
                        id=_urn_id(entity.get("entityUrn")),
                        title=_text(entity.get("title")),
                        company_name=company_name,
                        company_url=(
                            f"https://www.linkedin.com/company/{company_identifier}/"
                            if company_identifier
                            else None
                        ),
                        company_logo=_image(company),
                        employment_type=_text(entity.get("employmentType")),
                        location=_text(entity.get("locationName"))
                        or _text(entity.get("geoLocationName")),
                        date_range=_date_range(entity.get("dateRange") or entity.get("timePeriod")),
                        description=_text(entity.get("description")),
                    )
                )
            elif type_name.endswith(".education"):
                school = self._resolve(entity, "*school", "school")
                school_identifier = (
                    school.get("universalName") if isinstance(school, dict) else None
                )
                educations.append(
                    Education(
                        id=_urn_id(entity.get("entityUrn")),
                        school_name=_text(entity.get("schoolName")) or _text(school),
                        school_url=(
                            f"https://www.linkedin.com/school/{school_identifier}/"
                            if school_identifier
                            else None
                        ),
                        school_logo=_image(school),
                        degree_name=_text(entity.get("degreeName")),
                        field_of_study=_text(entity.get("fieldOfStudy")),
                        grade=_text(entity.get("grade")),
                        activities=_text(entity.get("activities")),
                        date_range=_date_range(entity.get("dateRange") or entity.get("timePeriod")),
                        description=_text(entity.get("description")),
                    )
                )
            elif type_name.endswith(".skill") and _text(entity.get("name")):
                skills.append(
                    Skill(
                        id=_urn_id(entity.get("entityUrn")),
                        name=_text(entity.get("name")) or "Unknown",
                        endorsement_count=entity.get("endorsementCount"),
                    )
                )
            elif "certification" in type_name and not type_name.endswith("collection"):
                issued = entity.get("timePeriod") or entity.get("dateRange") or {}
                certifications.append(
                    Certification(
                        id=_urn_id(entity.get("entityUrn")),
                        name=_text(entity.get("name")),
                        issuing_organization=_text(entity.get("authority"))
                        or _text(entity.get("issuingOrganization")),
                        issue_date=_date(issued.get("start") if isinstance(issued, dict) else None),
                        expiration_date=_date(
                            issued.get("end") if isinstance(issued, dict) else None
                        ),
                        credential_id=_text(entity.get("licenseNumber"))
                        or _text(entity.get("credentialId")),
                        credential_url=_text(entity.get("url")),
                    )
                )
            elif type_name.endswith(".language") and _text(entity.get("name")):
                languages.append(
                    Language(
                        id=_urn_id(entity.get("entityUrn")),
                        name=_text(entity.get("name")) or "Unknown",
                        proficiency=_text(entity.get("proficiency")),
                    )
                )
            elif type_name.endswith(".project"):
                projects.append(
                    Project(
                        id=_urn_id(entity.get("entityUrn")),
                        name=_text(entity.get("title")) or _text(entity.get("name")),
                        description=_text(entity.get("description")),
                        date_range=_date_range(entity.get("timePeriod") or entity.get("dateRange")),
                        url=_text(entity.get("url")),
                    )
                )
            elif type_name.endswith(".publication"):
                publications.append(
                    Publication(
                        id=_urn_id(entity.get("entityUrn")),
                        name=_text(entity.get("name")),
                        publisher=_text(entity.get("publisher")),
                        description=_text(entity.get("description")),
                        published_on=_date(entity.get("date")),
                        url=_text(entity.get("url")),
                    )
                )
            elif type_name.endswith(".course"):
                courses.append(
                    Course(
                        id=_urn_id(entity.get("entityUrn")),
                        name=_text(entity.get("name")),
                        number=_text(entity.get("number")),
                    )
                )
            elif type_name.endswith(".honor"):
                honors.append(
                    Honor(
                        id=_urn_id(entity.get("entityUrn")),
                        title=_text(entity.get("title")),
                        issuer=_text(entity.get("issuer")),
                        description=_text(entity.get("description")),
                        issued_on=_date(entity.get("issuedOn")),
                    )
                )
            elif "volunteerexperience" in type_name:
                volunteer.append(
                    VolunteerExperience(
                        id=_urn_id(entity.get("entityUrn")),
                        role=_text(entity.get("role")),
                        organization=_text(entity.get("companyName"))
                        or _text(entity.get("organization")),
                        cause=_text(entity.get("cause")),
                        description=_text(entity.get("description")),
                        date_range=_date_range(entity.get("timePeriod") or entity.get("dateRange")),
                    )
                )

        profile.experience = _dedupe(
            experiences, lambda item: item.id or (item.title, item.company_name)
        )
        profile.education = _dedupe(
            educations, lambda item: item.id or (item.school_name, item.degree_name)
        )
        profile.skills = _dedupe(skills, lambda item: item.id or item.name.casefold())
        profile.certifications = _dedupe(
            certifications, lambda item: item.id or (item.name, item.issuing_organization)
        )
        profile.languages = _dedupe(languages, lambda item: item.id or item.name.casefold())
        profile.projects = _dedupe(projects, lambda item: item.id or item.name)
        profile.publications = _dedupe(publications, lambda item: item.id or item.name)
        profile.courses = _dedupe(courses, lambda item: item.id or (item.name, item.number))
        profile.honors = _dedupe(honors, lambda item: item.id or (item.title, item.issuer))
        profile.volunteer_experience = _dedupe(
            volunteer, lambda item: item.id or (item.role, item.organization)
        )

    def _merge_legacy_sections(self, profile: Profile) -> None:
        for document in self.documents:
            if isinstance(document.get("profile"), dict):
                self._parse_legacy_view_sections(profile, document)
            elements = document.get("elements")
            if isinstance(elements, list):
                section = document.get("__section")
                self._parse_loose_elements(
                    profile,
                    elements,
                    section if isinstance(section, str) else None,
                )

    def _parse_legacy_view_sections(self, profile: Profile, document: dict[str, Any]) -> None:
        view_map = {
            "positionView": "experience",
            "educationView": "education",
            "skillView": "skills",
            "certificationView": "certifications",
            "languageView": "languages",
            "projectView": "projects",
            "publicationView": "publications",
            "courseView": "courses",
            "honorView": "honors",
            "volunteerExperienceView": "volunteer_experience",
        }
        for source, target in view_map.items():
            view = document.get(source)
            if isinstance(view, dict) and isinstance(view.get("elements"), list):
                self._parse_loose_elements(profile, view["elements"], target)

    def _parse_loose_elements(
        self, profile: Profile, elements: list[Any], target_hint: str | None = None
    ) -> None:
        valid = [item for item in elements if isinstance(item, dict)]
        if not valid:
            return
        # Wrap legacy elements with a synthetic type, then reuse the entity parser.
        type_names = {
            "experience": "Position",
            "education": "Education",
            "skills": "Skill",
            "certifications": "Certification",
            "languages": "Language",
            "projects": "Project",
            "publications": "Publication",
            "courses": "Course",
            "honors": "Honor",
            "volunteer_experience": "VolunteerExperience",
        }
        if target_hint in type_names:
            for item in valid:
                item.setdefault("$type", f"legacy.{type_names[target_hint]}")
        nested = VoyagerParser([{"included": valid}])
        shell = Profile(
            public_identifier=profile.public_identifier,
            profile_url=profile.profile_url,
        )
        nested._merge_entity_sections(shell)
        for field in type_names:
            current = getattr(profile, field)
            extra = getattr(shell, field)
            setattr(profile, field, _dedupe([*current, *extra], lambda item: item.id or repr(item)))

    def _merge_contact_info(self, profile: Profile) -> None:
        for document in self.documents:
            if not any(
                key in document
                for key in ("emailAddress", "phoneNumbers", "websites", "twitterHandles")
            ):
                continue
            profile.contact_info = ContactInfo(
                email=_text(document.get("emailAddress")),
                phone_numbers=[
                    item for item in document.get("phoneNumbers", []) if isinstance(item, dict)
                ],
                websites=[item for item in document.get("websites", []) if isinstance(item, dict)],
                twitter_handles=[
                    item for item in document.get("twitterHandles", []) if isinstance(item, dict)
                ],
            )
            break
