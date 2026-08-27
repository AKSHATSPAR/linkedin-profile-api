from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any


def test_api_gateway_v2_health_event() -> None:
    lambda_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(lambda_loop)
    from linkedin_profile_api.lambda_handler import handler

    event: dict[str, Any] = {
        "version": "2.0",
        "routeKey": "GET /health",
        "rawPath": "/health",
        "rawQueryString": "",
        "headers": {},
        "requestContext": {
            "accountId": "123456789012",
            "apiId": "test",
            "domainName": "test.execute-api.ap-south-1.amazonaws.com",
            "domainPrefix": "test",
            "http": {
                "method": "GET",
                "path": "/health",
                "protocol": "HTTP/1.1",
                "sourceIp": "203.0.113.9",
                "userAgent": "pytest",
            },
            "requestId": "request-id",
            "routeKey": "GET /health",
            "stage": "$default",
            "time": "27/Aug/2026:00:00:00 +0000",
            "timeEpoch": 0,
        },
        "isBase64Encoded": False,
    }

    try:
        response = handler(event, SimpleNamespace(aws_request_id="lambda-request-id"))
    finally:
        lambda_loop.close()
        asyncio.set_event_loop(None)

    assert response["statusCode"] == 200
    assert json.loads(response["body"])["status"] == "ok"
