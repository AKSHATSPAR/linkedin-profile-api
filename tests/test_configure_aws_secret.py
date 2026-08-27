from __future__ import annotations

import subprocess
from typing import Any

import pytest

from scripts import configure_aws_secret


def test_clipboard_cookie_header_is_read_without_echoing(monkeypatch: Any) -> None:
    li_at = "a" * 64
    copied = f'Cookie: bcookie=context; li_at={li_at}; JSESSIONID="ajax:session"\n'

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert args == (["pbpaste"],)
        assert kwargs == {"text": True, "capture_output": True, "check": False}
        return subprocess.CompletedProcess(["pbpaste"], 0, stdout=copied, stderr="")

    monkeypatch.setattr(configure_aws_secret.subprocess, "run", fake_run)

    header = configure_aws_secret.clipboard_cookie_header(
        li_at=li_at,
        jsessionid="ajax:session",
    )

    assert header == copied.removeprefix("Cookie: ").strip()


@pytest.mark.parametrize(
    "result",
    [
        subprocess.CompletedProcess(["pbpaste"], 1, stdout="", stderr="unavailable"),
        subprocess.CompletedProcess(["pbpaste"], 0, stdout="", stderr=""),
    ],
)
def test_clipboard_cookie_header_rejects_unavailable_content(
    monkeypatch: Any,
    result: subprocess.CompletedProcess[str],
) -> None:
    monkeypatch.setattr(
        configure_aws_secret.subprocess,
        "run",
        lambda *args, **kwargs: result,
    )

    with pytest.raises(ValueError, match="clipboard does not contain"):
        configure_aws_secret.clipboard_cookie_header(
            li_at="a" * 64,
            jsessionid="ajax:session",
        )


def test_clipboard_cookie_header_sanitizes_os_failures(monkeypatch: Any) -> None:
    def fail(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise OSError("provider detail")

    monkeypatch.setattr(configure_aws_secret.subprocess, "run", fail)

    with pytest.raises(ValueError, match="clipboard could not be read") as captured:
        configure_aws_secret.clipboard_cookie_header(
            li_at="a" * 64,
            jsessionid="ajax:session",
        )

    assert "provider detail" not in str(captured.value)
