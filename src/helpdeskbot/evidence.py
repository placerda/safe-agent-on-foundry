"""Signed evidence passed between SAFE diagnostic steps."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from typing import Any


EVIDENCE_SECRET_ENV = "SAFE_EVIDENCE_SECRET"
TOKEN_VERSION = 1


class EvidenceError(ValueError):
    """Raised when evidence is missing, malformed, or not trusted."""


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except (ValueError, TypeError) as exc:
        raise EvidenceError("Evidence token is not valid base64url.") from exc


def _secret(secret: str | None = None) -> bytes:
    value = secret if secret is not None else os.getenv(EVIDENCE_SECRET_ENV, "")
    normalized = value.strip()
    if len(normalized) < 32:
        raise EvidenceError(
            f"{EVIDENCE_SECRET_ENV} must contain at least 32 characters."
        )
    return normalized.encode("utf-8")


def issue_evidence(
    *,
    case_id: str,
    stage: str,
    sequence: list[str],
    facts: dict[str, Any],
    secret: str | None = None,
) -> str:
    """Create a compact HMAC-signed evidence token."""
    payload = {
        "version": TOKEN_VERSION,
        "case_id": case_id.strip().lower(),
        "stage": stage,
        "sequence": sequence,
        "facts": facts,
    }
    body = _encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    signature = _encode(
        hmac.new(_secret(secret), body.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{body}.{signature}"


def verify_evidence(
    token: str,
    *,
    expected_case_id: str | None = None,
    expected_stage: str | None = None,
    secret: str | None = None,
) -> dict[str, Any]:
    """Verify a token and return its trusted claims."""
    try:
        body, provided_signature = token.split(".", maxsplit=1)
    except ValueError as exc:
        raise EvidenceError("Evidence token must contain a signature.") from exc

    expected_signature = _encode(
        hmac.new(_secret(secret), body.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(provided_signature, expected_signature):
        raise EvidenceError("Evidence token signature is invalid.")

    try:
        payload = json.loads(_decode(body))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise EvidenceError("Evidence token payload is invalid.") from exc

    if not isinstance(payload, dict) or payload.get("version") != TOKEN_VERSION:
        raise EvidenceError("Evidence token version is invalid.")
    if not isinstance(payload.get("facts"), dict) or not isinstance(
        payload.get("sequence"), list
    ):
        raise EvidenceError("Evidence token claims are invalid.")

    normalized_case_id = (
        expected_case_id.strip().lower() if expected_case_id is not None else None
    )
    if normalized_case_id is not None and payload.get("case_id") != normalized_case_id:
        raise EvidenceError("Evidence belongs to a different case.")
    if expected_stage is not None and payload.get("stage") != expected_stage:
        raise EvidenceError("Evidence is from the wrong SAFE stage.")
    return payload


def evidence_snapshot_for_call(
    tool_name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Project validated evidence into the host-owned ACS snapshot."""
    if tool_name == "get_system_status":
        return {
            "valid": True,
            "stage": "start",
            "case_id": str(arguments.get("case_id", "")).strip().lower(),
            "sequence": [],
            "facts": {},
        }

    token_fields = {
        "get_user_account": ("service_evidence_token", "system_status"),
        "search_kb": ("account_evidence_token", "account"),
        "create_escalation_ticket": ("decision_evidence_token", "decision"),
    }
    token_field, expected_stage = token_fields.get(tool_name, ("", ""))
    token = arguments.get(token_field)
    case_id = str(arguments.get("case_id", "")).strip().lower()
    if not token_field or not isinstance(token, str):
        return {
            "valid": False,
            "reason": "missing_evidence",
            "case_id": case_id,
            "facts": {},
            "sequence": [],
        }

    try:
        claims = verify_evidence(
            token,
            expected_case_id=case_id,
            expected_stage=expected_stage,
        )
    except EvidenceError as exc:
        return {
            "valid": False,
            "reason": str(exc),
            "case_id": case_id,
            "facts": {},
            "sequence": [],
        }

    return {
        "valid": True,
        "stage": claims["stage"],
        "case_id": claims["case_id"],
        "sequence": claims["sequence"],
        "facts": claims["facts"],
    }


def validate_result_evidence(tool_name: str, result: Any) -> None:
    """Fail if a diagnostic tool returns evidence the host cannot verify."""
    expected_stages = {
        "get_system_status": "system_status",
        "get_user_account": "account",
        "search_kb": "decision",
    }
    expected_stage = expected_stages.get(tool_name)
    if expected_stage is None:
        return
    if not isinstance(result, dict) or not isinstance(
        result.get("evidence_token"), str
    ):
        raise EvidenceError(f"{tool_name} did not return signed evidence.")
    verify_evidence(result["evidence_token"], expected_stage=expected_stage)

