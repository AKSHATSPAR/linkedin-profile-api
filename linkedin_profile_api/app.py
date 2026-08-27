"""FastAPI application and stable public HTTP contract."""

from __future__ import annotations

import re
from typing import Annotated, Any
from uuid import uuid4

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .cache import ProfileCache
from .config import CredentialProvider, Settings, get_settings
from .errors import (
    AuthenticationError,
    ContactInfoDisabledError,
    CredentialsUnavailableError,
    ParseError,
    ProfileApiError,
    ProfileNotFoundError,
    UpstreamRateLimitedError,
    UpstreamResponseError,
)
from .linkedin import LinkedInClient
from .models import ErrorDetail, ErrorResponse, ProfileRequest, ProfileResponse
from .rate_limit import InMemoryRateLimiter
from .service import ProfileService

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")


def _error_response(
    *, status_code: int, code: str, message: str, request_id: str | None
) -> JSONResponse:
    payload = ErrorResponse(error=ErrorDetail(code=code, message=message, request_id=request_id))
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    if forwarded and len(forwarded) <= 64:
        return forwarded
    if request.client:
        return request.client.host
    return "unknown"


def create_app(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    runtime_settings = settings or get_settings()
    credential_provider = CredentialProvider(runtime_settings)
    linkedin_client = LinkedInClient(
        runtime_settings,
        credential_provider,
        transport=transport,
    )
    service = ProfileService(
        linkedin_client,
        ProfileCache(
            ttl_seconds=runtime_settings.cache_ttl_seconds,
            max_entries=runtime_settings.cache_max_entries,
        ),
        allow_contact_info=runtime_settings.allow_contact_info,
    )
    limiter = InMemoryRateLimiter(runtime_settings.rate_limit_per_minute)

    app = FastAPI(
        title=runtime_settings.app_name,
        version=runtime_settings.app_version,
        summary="Normalized LinkedIn profile data from an authenticated session",
        description=(
            "Submit a public LinkedIn member URL and receive a stable, versioned JSON "
            "representation of profile data visible to the configured LinkedIn session."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    app.state.service = service
    app.state.credential_provider = credential_provider

    @app.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Any:
        supplied = request.headers.get("x-request-id", "")
        request.state.request_id = supplied if _SAFE_REQUEST_ID.fullmatch(supplied) else uuid4().hex

        if request.url.path == "/v1/profiles" and not await limiter.allow(_client_key(request)):
            response = _error_response(
                status_code=429,
                code="client_rate_limited",
                message="Too many requests; try again in one minute",
                request_id=request.state.request_id,
            )
            response.headers["Retry-After"] = "60"
        else:
            response = await call_next(request)

        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(ProfileApiError)
    async def domain_error(request: Request, exc: ProfileApiError) -> JSONResponse:
        status_codes: dict[type[ProfileApiError], int] = {
            ContactInfoDisabledError: 403,
            ProfileNotFoundError: 404,
            UpstreamRateLimitedError: 429,
            CredentialsUnavailableError: 503,
            AuthenticationError: 503,
            ParseError: 502,
            UpstreamResponseError: 502,
        }
        response = _error_response(
            status_code=status_codes.get(type(exc), 500),
            code=exc.code,
            message=str(exc),
            request_id=_request_id(request),
        )
        if isinstance(exc, UpstreamRateLimitedError):
            response.headers["Retry-After"] = "60"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        del exc
        return _error_response(
            status_code=422,
            code="invalid_request",
            message="Supply a valid LinkedIn member profile URL",
            request_id=_request_id(request),
        )

    @app.exception_handler(ValueError)
    async def value_error(request: Request, exc: ValueError) -> JSONResponse:
        del exc
        return _error_response(
            status_code=422,
            code="invalid_request",
            message="Supply a valid LinkedIn member profile URL",
            request_id=_request_id(request),
        )

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "name": runtime_settings.app_name,
            "version": runtime_settings.app_version,
            "health": "/health",
            "documentation": "/docs",
            "endpoint": "/v1/profiles",
        }

    @app.get("/health", tags=["Operations"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": runtime_settings.app_version}

    @app.post(
        "/v1/profiles",
        response_model=ProfileResponse,
        response_model_exclude_none=True,
        responses={
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            429: {"model": ErrorResponse},
            502: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["Profiles"],
    )
    async def create_profile(payload: ProfileRequest) -> ProfileResponse:
        return await service.get_profile(
            payload.url,
            include_contact_info=payload.include_contact_info,
        )

    @app.get(
        "/v1/profiles",
        response_model=ProfileResponse,
        response_model_exclude_none=True,
        tags=["Profiles"],
    )
    async def get_profile(
        url: Annotated[str, Query(description="LinkedIn member profile URL")],
        include_contact_info: Annotated[
            bool,
            Query(description="Include contact fields when explicitly enabled by the operator"),
        ] = False,
    ) -> ProfileResponse:
        return await service.get_profile(url, include_contact_info=include_contact_info)

    return app


app = create_app()
