from __future__ import annotations

import argparse
import json
import subprocess
from typing import Any

import pytest

from scripts import configure_aws_secret


def test_clipboard_session_cookies_are_read_without_echoing(monkeypatch: Any) -> None:
    li_at = "a" * 64
    copied = f'Cookie: bcookie=context; li_at={li_at}; JSESSIONID="ajax:session"\n'

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert args == (["pbpaste"],)
        assert kwargs == {"text": True, "capture_output": True, "check": False}
        return subprocess.CompletedProcess(["pbpaste"], 0, stdout=copied, stderr="")

    monkeypatch.setattr(configure_aws_secret.subprocess, "run", fake_run)

    cookies = configure_aws_secret.clipboard_session_cookies()

    assert cookies == {
        "bcookie": "context",
        "li_at": li_at,
        "JSESSIONID": '"ajax:session"',
    }


@pytest.mark.parametrize(
    "result",
    [
        subprocess.CompletedProcess(["pbpaste"], 1, stdout="", stderr="unavailable"),
        subprocess.CompletedProcess(["pbpaste"], 0, stdout="", stderr=""),
    ],
)
def test_clipboard_session_cookies_reject_unavailable_content(
    monkeypatch: Any,
    result: subprocess.CompletedProcess[str],
) -> None:
    monkeypatch.setattr(
        configure_aws_secret.subprocess,
        "run",
        lambda *args, **kwargs: result,
    )

    with pytest.raises(ValueError, match="clipboard does not contain"):
        configure_aws_secret.clipboard_session_cookies()


def test_clipboard_session_cookies_sanitize_os_failures(monkeypatch: Any) -> None:
    def fail(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise OSError("provider detail")

    monkeypatch.setattr(configure_aws_secret.subprocess, "run", fail)

    with pytest.raises(ValueError, match="clipboard could not be read") as captured:
        configure_aws_secret.clipboard_session_cookies()

    assert "provider detail" not in str(captured.value)


def test_main_stores_only_structured_session_cookies(monkeypatch: Any) -> None:
    li_at = "a" * 64
    captured_secret: dict[str, Any] = {}
    monkeypatch.setattr(
        configure_aws_secret,
        "parse_args",
        lambda: argparse.Namespace(
            profile="tross",
            region="ap-south-1",
            name="tross/linkedin-session",
            expected_account="123",
            cookie_header_from_clipboard=True,
        ),
    )
    monkeypatch.setattr(
        configure_aws_secret,
        "clipboard_session_cookies",
        lambda **kwargs: {
            "li_at": li_at,
            "JSESSIONID": "ajax:session",
            "bcookie": "browser",
        },
    )

    def fake_aws(
        *args: str,
        input_text: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        del check
        if args[:2] == ("sts", "get-caller-identity"):
            return subprocess.CompletedProcess(args, 0, stdout='{"Account":"123"}', stderr="")
        if args[:2] == ("secretsmanager", "describe-secret"):
            return subprocess.CompletedProcess(
                args,
                0,
                stdout='{"ARN":"arn:aws:secretsmanager:test"}',
                stderr="",
            )
        assert args[:2] == ("secretsmanager", "put-secret-value")
        assert input_text is not None
        captured_secret.update(json.loads(input_text))
        return subprocess.CompletedProcess(args, 0, stdout="{}", stderr="")

    monkeypatch.setattr(configure_aws_secret, "aws", fake_aws)

    assert configure_aws_secret.main() == 0
    assert captured_secret == {
        "li_at": li_at,
        "jsessionid": "ajax:session",
        "cookies": {
            "li_at": li_at,
            "JSESSIONID": "ajax:session",
            "bcookie": "browser",
        },
    }
    assert "cookie_header" not in captured_secret
