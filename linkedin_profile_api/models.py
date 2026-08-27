"""Versioned public response models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .url_utils import MAX_PROFILE_URL_LENGTH, parse_linkedin_profile_url


class ProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(
        max_length=MAX_PROFILE_URL_LENGTH,
        description="Canonical or regional LinkedIn member profile URL",
        examples=["https://www.linkedin.com/in/akshat-sparsh-b648a039a/"],
    )
    include_contact_info: bool = Field(
        default=False,
        description=(
            "Request contact fields visible to the configured LinkedIn account. "
            "Disabled by default to minimize personal-data exposure."
        ),
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return parse_linkedin_profile_url(value).canonical_url


class DateValue(BaseModel):
    year: int | None = None
    month: int | None = Field(default=None, ge=1, le=12)
    day: int | None = Field(default=None, ge=1, le=31)


class DateRange(BaseModel):
    start: DateValue | None = None
    end: DateValue | None = None
    present: bool = False


class ImageAsset(BaseModel):
    url: str
    width: int | None = None
    height: int | None = None


class ProfileImages(BaseModel):
    profile: ImageAsset | None = None
    background: ImageAsset | None = None


class Location(BaseModel):
    display_name: str | None = None
    country_code: str | None = None


class Experience(BaseModel):
    id: str | None = None
    title: str | None = None
    company_name: str | None = None
    company_url: str | None = None
    company_logo: ImageAsset | None = None
    employment_type: str | None = None
    location: str | None = None
    date_range: DateRange | None = None
    description: str | None = None


class Education(BaseModel):
    id: str | None = None
    school_name: str | None = None
    school_url: str | None = None
    school_logo: ImageAsset | None = None
    degree_name: str | None = None
    field_of_study: str | None = None
    grade: str | None = None
    activities: str | None = None
    date_range: DateRange | None = None
    description: str | None = None


class Skill(BaseModel):
    id: str | None = None
    name: str
    endorsement_count: int | None = None


class Certification(BaseModel):
    id: str | None = None
    name: str | None = None
    issuing_organization: str | None = None
    issue_date: DateValue | None = None
    expiration_date: DateValue | None = None
    credential_id: str | None = None
    credential_url: str | None = None


class Language(BaseModel):
    id: str | None = None
    name: str
    proficiency: str | None = None


class Project(BaseModel):
    id: str | None = None
    name: str | None = None
    description: str | None = None
    date_range: DateRange | None = None
    url: str | None = None


class Publication(BaseModel):
    id: str | None = None
    name: str | None = None
    publisher: str | None = None
    description: str | None = None
    published_on: DateValue | None = None
    url: str | None = None


class Course(BaseModel):
    id: str | None = None
    name: str | None = None
    number: str | None = None


class Honor(BaseModel):
    id: str | None = None
    title: str | None = None
    issuer: str | None = None
    description: str | None = None
    issued_on: DateValue | None = None


class VolunteerExperience(BaseModel):
    id: str | None = None
    role: str | None = None
    organization: str | None = None
    cause: str | None = None
    description: str | None = None
    date_range: DateRange | None = None


class ContactInfo(BaseModel):
    email: str | None = None
    phone_numbers: list[dict[str, Any]] = Field(default_factory=list)
    websites: list[dict[str, Any]] = Field(default_factory=list)
    twitter_handles: list[dict[str, Any]] = Field(default_factory=list)


class Profile(BaseModel):
    linkedin_id: str | None = None
    public_identifier: str
    profile_url: str
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    headline: str | None = None
    about: str | None = None
    location: Location = Field(default_factory=Location)
    industry: str | None = None
    connection_degree: int | None = Field(default=None, ge=1, le=3)
    follower_count: int | None = Field(default=None, ge=0)
    connection_count: int | None = Field(default=None, ge=0)
    images: ProfileImages = Field(default_factory=ProfileImages)
    experience: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    languages: list[Language] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    publications: list[Publication] = Field(default_factory=list)
    courses: list[Course] = Field(default_factory=list)
    honors: list[Honor] = Field(default_factory=list)
    volunteer_experience: list[VolunteerExperience] = Field(default_factory=list)
    contact_info: ContactInfo | None = None


class ResponseMeta(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: Literal["linkedin"] = "linkedin"
    cached: bool = False
    partial: bool = False
    warnings: list[str] = Field(default_factory=list)


class ProfileResponse(BaseModel):
    meta: ResponseMeta = Field(default_factory=ResponseMeta)
    profile: Profile


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
