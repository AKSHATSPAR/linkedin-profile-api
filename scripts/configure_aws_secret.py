"""Interactively create or rotate the deployment secret without echoing values."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from typing import Any

import boto3
from botocore.exceptions import ClientError


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


def main() -> int:
    args = parse_args()
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    identity: dict[str, Any] = session.client("sts").get_caller_identity()
    if identity.get("Account") != args.expected_account:
        print("Refusing to write: the authenticated AWS account does not match.", file=sys.stderr)
        return 2

    li_at = require_value("LinkedIn li_at (hidden): ")
    jsessionid = require_value("LinkedIn JSESSIONID (hidden): ")
    normalized_jsessionid = jsessionid.strip('"')
    if len(li_at) < 20 or not normalized_jsessionid.startswith("ajax:"):
        print("The supplied session values do not have the expected shape.", file=sys.stderr)
        return 2

    secret_string = json.dumps({"li_at": li_at, "jsessionid": normalized_jsessionid})
    client = session.client("secretsmanager")
    try:
        current = client.describe_secret(SecretId=args.name)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            raise
        response = client.create_secret(
            Name=args.name,
            Description="Temporary, revocable LinkedIn session for the Tross challenge",
            SecretString=secret_string,
            Tags=[{"Key": "Project", "Value": "tross-engineering-challenge"}],
        )
        arn = response["ARN"]
        action = "created"
    else:
        arn = current["ARN"]
        client.put_secret_value(SecretId=arn, SecretString=secret_string)
        action = "rotated"

    print(f"Secret {action}: {arn}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
