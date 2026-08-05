"""Host-owned signed evidence for SAFE diagnostic trajectories."""

from __future__ import annotations

import base64
from collections.abc import Mapping
import hashlib
import hmac
import json
import os
from typing import Any

from fixtures import ALLOWED_SERVICE, CASE_ACCOUNTS


EVIDENCE_SECRET_ENV = "SAFE_EVIDENCE_SECRET"
TOKEN_VERSION = 1
EVIDENCE_REFERENCE_PREFIX = "ev:"
_EVIDENCE_REGISTRY: dict[str, str] = {}

EXPECTED_INPUT_EVIDENCE = {
    "get_user_account": {
        "field": "service_evidence_reference",
        "stage": "system_status",
        "audience": "get_user_account",
        "sequence": ["get_system_status"],
    },
    "search_kb": {
        "field": "account_evidence_reference",
        "stage": "account",
        "audience": "search_kb",
        "sequence": ["get_system_status", "get_user_account"],
    },
    "create_escalation_ticket": {
        "field": "decision_evidence_reference",
        "stage": "decision",
        "audience": "create_escalation_ticket",
        "sequence": ["get_system_status", "get_user_account", "search_kb"],
    },
}

REQUIRED_FACT_TYPES: dict[str, dict[str, type]] = {
    "system_status": {
        "service": str,
        "service_state": str,
    },
    "account": {
        "service": str,
        "service_state": str,
        "account_alias": str,
        "account_found": bool,
        "account_state": str,
        "sign_in_allowed": bool,
        "token_state": str,
    },
    "decision": {
        "service": str,
        "service_state": str,
        "account_alias": str,
        "account_found": bool,
        "account_state": str,
        "sign_in_allowed": bool,
        "token_state": str,
        "kb_query": str,
        "kb_article_id": str,
        "local_remediation_available": bool,
    },
}


class EvidenceError(ValueError):
    """Raised when evidence is missing, malformed, or not trusted."""


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except (ValueError, TypeError) as exc:
        raise EvidenceError("Evidence reference is not valid base64url.") from exc


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


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
    audience: str,
    sequence: list[str],
    facts: dict[str, Any],
    predecessor_id: str | None,
    secret: str | None = None,
) -> str:
    """Create a compact host-signed evidence token."""
    unsigned = {
        "version": TOKEN_VERSION,
        "case_id": case_id.strip().lower(),
        "stage": stage,
        "audience": audience,
        "sequence": sequence,
        "predecessor_id": predecessor_id,
        "facts": facts,
    }
    unsigned["evidence_id"] = hashlib.sha256(_canonical_json(unsigned)).hexdigest()[:24]
    body = _encode(_canonical_json(unsigned))
    signature = _encode(
        hmac.new(_secret(secret), body.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{body}.{signature}"


def publish_evidence(token: str) -> str:
    """Store host-signed evidence in the registry and return the short evidence reference (ev:<id>) the model sees."""
    claims = verify_evidence(token)
    reference = f"{EVIDENCE_REFERENCE_PREFIX}{claims['evidence_id']}"
    _EVIDENCE_REGISTRY[reference] = token
    return reference


def resolve_evidence_reference(value: str) -> str:
    """Resolve a host-issued evidence reference while retaining direct-token test support."""
    if not value.startswith(EVIDENCE_REFERENCE_PREFIX):
        return value
    try:
        return _EVIDENCE_REGISTRY[value]
    except KeyError as exc:
        raise EvidenceError("Evidence reference is unknown or expired.") from exc


def clear_evidence_registry() -> None:
    _EVIDENCE_REGISTRY.clear()


def _validate_claim_schema(payload: dict[str, Any]) -> None:
    stage = payload.get("stage")
    required_types = REQUIRED_FACT_TYPES.get(stage)
    facts = payload.get("facts")
    if required_types is None or not isinstance(facts, dict):
        raise EvidenceError("Evidence reference stage or facts are invalid.")
    for field, expected_type in required_types.items():
        if not isinstance(facts.get(field), expected_type):
            raise EvidenceError(f"Evidence fact {field} is missing or invalid.")
    if payload.get("case_id") not in CASE_ACCOUNTS:
        raise EvidenceError("Evidence case is outside HelpdeskBot scope.")
    if facts.get("service") != ALLOWED_SERVICE:
        raise EvidenceError("Evidence service is outside HelpdeskBot scope.")
    expected_alias = CASE_ACCOUNTS[payload["case_id"]]
    if stage in {"account", "decision"} and facts.get("account_alias") != expected_alias:
        raise EvidenceError("Evidence account is outside HelpdeskBot scope.")
    if stage == "system_status" and payload.get("predecessor_id") is not None:
        raise EvidenceError("Initial evidence cannot have a predecessor.")
    if stage in {"account", "decision"} and not isinstance(
        payload.get("predecessor_id"), str
    ):
        raise EvidenceError("Chained evidence requires a predecessor.")


def verify_evidence(
    token: str,
    *,
    expected_case_id: str | None = None,
    expected_stage: str | None = None,
    expected_audience: str | None = None,
    expected_sequence: list[str] | None = None,
    secret: str | None = None,
) -> dict[str, Any]:
    """Verify a token signature, strict schema, audience, and trajectory."""
    try:
        body, provided_signature = token.split(".", maxsplit=1)
    except ValueError as exc:
        raise EvidenceError("Evidence reference must contain a signature.") from exc

    expected_signature = _encode(
        hmac.new(_secret(secret), body.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(provided_signature, expected_signature):
        raise EvidenceError("Evidence reference signature is invalid.")

    try:
        payload = json.loads(_decode(body))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise EvidenceError("Evidence reference payload is invalid.") from exc

    if (
        not isinstance(payload, dict)
        or payload.get("version") != TOKEN_VERSION
        or not isinstance(payload.get("evidence_id"), str)
        or not isinstance(payload.get("audience"), str)
        or not isinstance(payload.get("sequence"), list)
    ):
        raise EvidenceError("Evidence reference claims are invalid.")
    _validate_claim_schema(payload)

    normalized_case_id = (
        expected_case_id.strip().lower() if expected_case_id is not None else None
    )
    if normalized_case_id is not None and payload["case_id"] != normalized_case_id:
        raise EvidenceError("Evidence belongs to a different case.")
    if expected_stage is not None and payload["stage"] != expected_stage:
        raise EvidenceError("Evidence is from the wrong SAFE stage.")
    if expected_audience is not None and payload["audience"] != expected_audience:
        raise EvidenceError("Evidence is intended for a different tool.")
    if expected_sequence is not None and payload["sequence"] != expected_sequence:
        raise EvidenceError("Evidence trajectory is incomplete or reordered.")
    return payload


def _untrusted(reason: str, case_id: str) -> dict[str, Any]:
    return {
        "valid": False,
        "reason": reason,
        "case_id": case_id,
        "facts": {},
        "sequence": [],
    }


def evidence_snapshot_for_call(
    tool_name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Project verified evidence into the host-owned ACS snapshot."""
    case_id = str(arguments.get("case_id", "")).strip().lower()
    if case_id not in CASE_ACCOUNTS:
        return _untrusted("case_outside_scope", case_id)
    if tool_name == "get_system_status":
        return {
            "valid": True,
            "stage": "start",
            "audience": "get_system_status",
            "case_id": case_id,
            "sequence": [],
            "facts": {},
        }

    requirement = EXPECTED_INPUT_EVIDENCE.get(tool_name)
    if requirement is None:
        return _untrusted("unknown_tool", case_id)
    token = arguments.get(requirement["field"])
    if not isinstance(token, str):
        return _untrusted("missing_evidence", case_id)

    try:
        claims = verify_evidence(
            resolve_evidence_reference(token),
            expected_case_id=case_id,
        )
    except EvidenceError as exc:
        return _untrusted(str(exc), case_id)
    if (
        claims["stage"] != requirement["stage"]
        or claims["audience"] != requirement["audience"]
        or claims["sequence"] != requirement["sequence"]
    ):
        return _untrusted("flow_integrity_violation", case_id)

    return {
        "valid": True,
        "stage": claims["stage"],
        "audience": claims["audience"],
        "evidence_id": claims["evidence_id"],
        "predecessor_id": claims["predecessor_id"],
        "case_id": claims["case_id"],
        "sequence": claims["sequence"],
        "facts": claims["facts"],
    }


def _require_raw_result(result: Any, tool_name: str) -> dict[str, Any]:
    if isinstance(result, list) and len(result) == 1:
        item = result[0]
        text = getattr(item, "text", None)
        content_result = getattr(item, "result", None)
        if isinstance(text, str):
            result = text
        elif content_result is not None:
            result = content_result
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError as exc:
            raise EvidenceError(f"{tool_name} returned invalid JSON.") from exc
    if isinstance(result, Mapping):
        return dict(result)
    result_type = type(result).__name__
    if isinstance(result, list):
        item_types = ",".join(type(item).__name__ for item in result)
        result_type = f"{result_type}[{item_types}]"
    raise EvidenceError(
        f"{tool_name} returned a non-object result of type {result_type}."
    )


def attach_result_evidence(
    tool_name: str,
    arguments: dict[str, Any],
    result: Any,
    prior_evidence: dict[str, Any],
) -> Any:
    """Validate raw tool output and attach evidence signed only by the host."""
    if tool_name == "create_escalation_ticket":
        return result

    raw = _require_raw_result(result, tool_name)
    case_id = str(arguments.get("case_id", "")).strip().lower()
    if case_id not in CASE_ACCOUNTS:
        raise EvidenceError("Tool result belongs to a case outside scope.")

    if tool_name == "get_system_status":
        service = str(raw.get("service", "")).strip().lower()
        state = str(raw.get("state", "")).strip().lower()
        if service != ALLOWED_SERVICE or not state:
            raise EvidenceError("Service result is outside scope or incomplete.")
        stage = "system_status"
        audience = "get_user_account"
        sequence = ["get_system_status"]
        predecessor_id = None
        facts = {"service": service, "service_state": state}
    elif tool_name == "get_user_account":
        if (
            prior_evidence.get("valid") is not True
            or prior_evidence.get("stage") != "system_status"
            or prior_evidence.get("sequence") != ["get_system_status"]
        ):
            raise EvidenceError("Account result lacks valid service evidence.")
        alias = str(raw.get("account_alias", "")).strip().lower()
        if alias != CASE_ACCOUNTS[case_id]:
            raise EvidenceError("Account result is outside the case scope.")
        stage = "account"
        audience = "search_kb"
        sequence = [*prior_evidence["sequence"], "get_user_account"]
        predecessor_id = prior_evidence["evidence_id"]
        facts = {
            **prior_evidence["facts"],
            "account_alias": alias,
            "account_found": raw.get("found"),
            "account_state": str(raw.get("state", "unknown")),
            "sign_in_allowed": raw.get("sign_in_allowed"),
            "token_state": str(raw.get("token_state", "unknown")),
        }
    elif tool_name == "search_kb":
        if (
            prior_evidence.get("valid") is not True
            or prior_evidence.get("stage") != "account"
            or prior_evidence.get("sequence")
            != ["get_system_status", "get_user_account"]
        ):
            raise EvidenceError("KB result lacks valid account evidence.")
        resolution = str(raw.get("resolution", "")).strip().lower()
        if resolution not in {"local-remediation-available", "not-found"}:
            raise EvidenceError("KB result has an invalid resolution.")
        stage = "decision"
        audience = "create_escalation_ticket"
        sequence = [*prior_evidence["sequence"], "search_kb"]
        predecessor_id = prior_evidence["evidence_id"]
        facts = {
            **prior_evidence["facts"],
            "kb_query": str(arguments.get("query", "")).strip().lower(),
            "kb_article_id": str(raw.get("article_id", "")),
            "local_remediation_available": resolution
            == "local-remediation-available",
        }
    else:
        raise EvidenceError(f"Unknown diagnostic tool: {tool_name}.")

    return {
        **raw,
        "evidence_reference": publish_evidence(
            issue_evidence(
                case_id=case_id,
                stage=stage,
                audience=audience,
                sequence=sequence,
                predecessor_id=predecessor_id,
                facts=facts,
            )
        ),
    }
