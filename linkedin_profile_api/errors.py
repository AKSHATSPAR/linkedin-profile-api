"""Domain errors that can be mapped to stable public API responses."""


class ProfileApiError(Exception):
    """Base class for expected service failures."""

    code = "profile_api_error"


class CredentialsUnavailableError(ProfileApiError):
    code = "credentials_unavailable"


class ContactInfoDisabledError(ProfileApiError):
    code = "contact_info_disabled"


class AuthenticationError(ProfileApiError):
    code = "linkedin_authentication_failed"


class ProfileNotFoundError(ProfileApiError):
    code = "profile_not_found"


class UpstreamRateLimitedError(ProfileApiError):
    code = "linkedin_rate_limited"


class UpstreamResponseError(ProfileApiError):
    code = "linkedin_upstream_error"


class ParseError(ProfileApiError):
    code = "linkedin_response_parse_error"
