"""Identity helpers shared by the Voyager client and response normalizer."""

from __future__ import annotations

from typing import Any


def profile_public_identifiers(value: Any) -> tuple[str, ...]:
    """Return every non-empty public identifier represented by a profile shape."""
    if not isinstance(value, dict):
        return ()

    identifiers: list[str] = []
    direct = value.get("publicIdentifier")
    if isinstance(direct, str) and direct:
        identifiers.append(direct)

    mini = value.get("miniProfile")
    if isinstance(mini, dict):
        nested = mini.get("publicIdentifier")
        if isinstance(nested, str) and nested:
            identifiers.append(nested)
    return tuple(identifiers)


def has_malformed_public_identifier(value: Any) -> bool:
    """Return whether a present publicIdentifier has a non-string shape."""
    if not isinstance(value, dict):
        return False

    direct = value.get("publicIdentifier")
    if direct is not None and not isinstance(direct, str):
        return True

    mini = value.get("miniProfile")
    if isinstance(mini, dict):
        nested = mini.get("publicIdentifier")
        return nested is not None and not isinstance(nested, str)
    return False


def profile_member_ids(value: Any) -> tuple[str, ...]:
    """Return parseable member IDs from outer and nested legacy profile URNs."""
    if not isinstance(value, dict):
        return ()

    urns: list[Any] = [value.get("entityUrn")]
    mini = value.get("miniProfile")
    if isinstance(mini, dict):
        urns.append(mini.get("entityUrn"))

    member_ids: list[str] = []
    for urn in urns:
        if not isinstance(urn, str):
            continue
        member_id = urn.rsplit(":", 1)[-1]
        if member_id and not member_id.startswith("("):
            member_ids.append(member_id)
    return tuple(member_ids)
