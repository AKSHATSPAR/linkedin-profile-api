"""FastAPI application and stable public HTTP contract."""

from __future__ import annotations

import ipaddress
import re
from typing import Annotated, Any
from uuid import uuid4

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .cache import ProfileCache
from .config import CredentialProvider, Settings, get_settings
from .errors import (
    AuthenticationError,
    ContactInfoDisabledError,
    CredentialsUnavailableError,
    InvalidProfileUrlError,
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
from .url_utils import MAX_PROFILE_URL_LENGTH

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
    if request.client:
        try:
            return ipaddress.ip_address(request.client.host).compressed
        except ValueError:
            pass
    return "unknown"


class ProfileBodyLimitMiddleware:
    """Bound the one JSON request body before FastAPI parses it."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not (
            scope["type"] == "http"
            and scope.get("method") == "POST"
            and scope.get("path") == "/v1/profiles"
        ):
            await self._app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                if int(declared) > self._max_bytes:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                pass

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self._max_bytes:
                await self._reject(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return {"type": "http.request", "body": b"", "more_body": False}

        await self._app(scope, replay, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        state = scope.get("state", {})
        request_id = state.get("request_id") if isinstance(state, dict) else None
        response = _error_response(
            status_code=413,
            code="request_too_large",
            message="The request body is too large",
            request_id=request_id if isinstance(request_id, str) else None,
        )
        await response(scope, receive, send)


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
    limiter = InMemoryRateLimiter(
        runtime_settings.rate_limit_per_minute,
        max_clients=runtime_settings.rate_limit_max_clients,
    )

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
    app.add_middleware(
        ProfileBodyLimitMiddleware,
        max_bytes=runtime_settings.max_request_body_bytes,
    )

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
            InvalidProfileUrlError: 422,
            ProfileNotFoundError: 404,
            UpstreamRateLimitedError: 429,
            CredentialsUnavailableError: 503,
            AuthenticationError: 503,
            ParseError: 502,
            UpstreamResponseError: 502,
        }
        status_code = next(
            (
                mapped_status
                for error_type, mapped_status in status_codes.items()
                if isinstance(exc, error_type)
            ),
            500,
        )
        response = _error_response(
            status_code=status_code,
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
            413: {"model": ErrorResponse},
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
    async def get_profile(
        url: Annotated[
            str,
            Query(
                max_length=MAX_PROFILE_URL_LENGTH,
                description="LinkedIn member profile URL",
            ),
        ],
        include_contact_info: Annotated[
            bool,
            Query(description="Include contact fields when explicitly enabled by the operator"),
        ] = False,
    ) -> ProfileResponse:
        return await service.get_profile(url, include_contact_info=include_contact_info)

    return app


app = create_app()
