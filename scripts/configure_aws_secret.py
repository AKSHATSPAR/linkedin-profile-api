"""Interactively create or rotate the deployment secret without echoing values."""

from __future__ import annotations

import argparse
import getpass
import json
import subprocess
import sys


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
    return parser.parse_args()


def require_value(prompt: str) -> str:
    value = getpass.getpass(prompt).strip()
    if not value:
        raise ValueError("A value is required")
    return value


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

    li_at = require_value("LinkedIn li_at (hidden): ")
    jsessionid = require_value("LinkedIn JSESSIONID (hidden): ")
    normalized_jsessionid = jsessionid.strip('"')
    if len(li_at) < 20 or not normalized_jsessionid.startswith("ajax:"):
        print("The supplied session values do not have the expected shape.", file=sys.stderr)
        return 2

    secret_string = json.dumps({"li_at": li_at, "jsessionid": normalized_jsessionid})
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
