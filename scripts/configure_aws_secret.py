"""Interactively create or rotate the deployment secret without echoing values."""

from __future__ import annotations

import argparse
import getpass
import json
import subprocess
import sys
from pathlib import Path

# Support the documented ``python3 scripts/configure_aws_secret.py`` invocation
# without requiring the project to be installed into the active interpreter.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from linkedin_profile_api.session_cookies import (  # noqa: E402
    SessionCookieError,
    import_cookie_header,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="tross", help="AWS CLI profile name")
    parser.add_argument("--region", default="ap-south-1")
    parser.add_argument("--name", default="tross/linkedin-session")
    parser.add_argument(
        "--expected-account",
        required=True,
        help="Refuse to write unless STS returns this AWS account ID",
    )
    parser.add_argument(
        "--cookie-header-from-clipboard",
        action="store_true",
        help="Read the complete Cookie header from the macOS clipboard without echoing it",
    )
    return parser.parse_args()


def require_value(prompt: str) -> str:
    value = getpass.getpass(prompt).strip()
    if not value:
        raise ValueError("A value is required")
    return value


def optional_session_cookies(*, li_at: str, jsessionid: str) -> dict[str, str] | None:
    header = getpass.getpass(
        "LinkedIn full Cookie header (hidden, optional; Enter to skip): "
    ).strip()
    if not header:
        return None
    return import_cookie_header(header, li_at=li_at, jsessionid=jsessionid)


def clipboard_session_cookies() -> dict[str, str]:
    try:
        result = subprocess.run(
            ["pbpaste"],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise SessionCookieError("The macOS clipboard could not be read") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise SessionCookieError("The macOS clipboard does not contain a Cookie header")
    return import_cookie_header(result.stdout)


def aws(
    *args: str, input_text: str | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["aws", *args],
        input=input_text,
        text=True,
        capture_output=True,
        check=check,
    )


def main() -> int:
    args = parse_args()
    common = ("--profile", args.profile, "--region", args.region)
    identity = json.loads(aws("sts", "get-caller-identity", *common, "--output", "json").stdout)
    if identity.get("Account") != args.expected_account:
        print(
            "Refusing to write: the authenticated AWS account does not match.",
            file=sys.stderr,
        )
        return 2

    try:
        if args.cookie_header_from_clipboard:
            session_cookies = clipboard_session_cookies()
            li_at = session_cookies["li_at"]
            normalized_jsessionid = session_cookies["JSESSIONID"].strip('"')
        else:
            li_at = require_value("LinkedIn li_at (hidden): ")
            jsessionid = require_value("LinkedIn JSESSIONID (hidden): ")
            normalized_jsessionid = jsessionid.strip('"')
            if len(li_at) < 20 or not normalized_jsessionid.startswith("ajax:"):
                raise SessionCookieError(
                    "The supplied session values do not have the expected shape"
                )
            session_cookies = optional_session_cookies(
                li_at=li_at,
                jsessionid=normalized_jsessionid,
            )
    except (SessionCookieError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    secret_payload = {"li_at": li_at, "jsessionid": normalized_jsessionid}
    if session_cookies is not None:
        secret_payload["cookies"] = session_cookies
    secret_string = json.dumps(secret_payload)
    current = aws(
        "secretsmanager",
        "describe-secret",
        "--secret-id",
        args.name,
        *common,
        "--output",
        "json",
        check=False,
    )
    if current.returncode != 0:
        if "ResourceNotFoundException" not in current.stderr:
            print(current.stderr.strip(), file=sys.stderr)
            return current.returncode
        created = aws(
            "secretsmanager",
            "create-secret",
            "--name",
            args.name,
            "--description",
            "Temporary, revocable LinkedIn session for the Tross challenge",
            "--secret-string",
            "file:///dev/stdin",
            "--tags",
            "Key=Project,Value=tross-engineering-challenge",
            *common,
            "--output",
            "json",
            input_text=secret_string,
        )
        arn = json.loads(created.stdout)["ARN"]
        action = "created"
    else:
        arn = json.loads(current.stdout)["ARN"]
        aws(
            "secretsmanager",
            "put-secret-value",
            "--secret-id",
            arn,
            "--secret-string",
            "file:///dev/stdin",
            *common,
            input_text=secret_string,
        )
        action = "rotated"

    print(f"Secret {action}: {arn}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
