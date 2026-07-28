"""Four harmless, deterministic local tools used by HelpdeskBot."""

from __future__ import annotations

from typing import Any, Literal

from agent_framework import tool
from pydantic import Field
from typing_extensions import Annotated

from evidence import EvidenceError, issue_evidence, verify_evidence


TOOL_NAMES = (
    "get_system_status",
    "get_user_account",
    "search_kb",
    "create_escalation_ticket",
)

_TICKETS: list[dict[str, str]] = []


def _get_system_status(case_id: str, service: str) -> dict[str, str]:
    normalized = service.strip().lower()
    status = {
        "identity": ("operational", "No active identity-service incident."),
        "email": ("degraded", "Synthetic mail queue delay: 4 minutes."),
        "network": ("operational", "No active network incident."),
    }
    state, detail = status.get(
        normalized, ("unknown", "Service is not in the mock catalog.")
    )
    result = {
        "service": normalized,
        "state": state,
        "detail": detail,
        "source": "in-memory-mock",
    }
    result["evidence_token"] = issue_evidence(
        case_id=case_id,
        stage="system_status",
        sequence=["get_system_status"],
        facts={"service": normalized, "service_state": state},
    )
    return result


def _get_user_account(
    case_id: str, account_alias: str, service_evidence_token: str
) -> dict[str, str | bool]:
    previous = verify_evidence(
        service_evidence_token,
        expected_case_id=case_id,
        expected_stage="system_status",
    )
    if previous["facts"].get("service") != "identity":
        raise EvidenceError("Account lookup requires identity-service evidence.")

    normalized = account_alias.strip().lower()
    accounts: dict[str, dict[str, str | bool]] = {
        "demo-user": {
            "account_alias": "demo-user",
            "found": True,
            "state": "active",
            "sign_in_allowed": True,
            "token_state": "expired",
            "source": "in-memory-mock",
        },
        "locked-user": {
            "account_alias": "locked-user",
            "found": True,
            "state": "locked",
            "sign_in_allowed": False,
            "token_state": "valid",
            "source": "in-memory-mock",
        },
    }
    result = accounts.get(
        normalized,
        {
            "account_alias": normalized,
            "found": False,
            "source": "in-memory-mock",
        },
    )
    facts = {
        **previous["facts"],
        "account_alias": normalized,
        "account_found": result["found"],
        "account_state": result.get("state", "unknown"),
        "sign_in_allowed": result.get("sign_in_allowed", False),
        "token_state": result.get("token_state", "unknown"),
    }
    return {
        **result,
        "evidence_token": issue_evidence(
            case_id=case_id,
            stage="account",
            sequence=[*previous["sequence"], "get_user_account"],
            facts=facts,
        ),
    }


def _search_kb(
    case_id: str, query: str, account_evidence_token: str
) -> dict[str, str | list[str]]:
    previous = verify_evidence(
        account_evidence_token,
        expected_case_id=case_id,
        expected_stage="account",
    )
    normalized = query.strip().lower()
    if previous["facts"].get("token_state") == "expired":
        result: dict[str, Any] = {
            "article_id": "KB-1001",
            "title": "Refresh an expired sign-in token",
            "steps": [
                "Sign out of the demo client.",
                "Sign in again.",
                "Retry the request.",
            ],
            "resolution": "local-remediation-available",
            "source": "in-memory-mock",
        }
    else:
        result = {
            "article_id": "KB-0000",
            "title": "No matching mock article",
            "steps": [],
            "resolution": "not-found",
            "source": "in-memory-mock",
        }
    facts = {
        **previous["facts"],
        "kb_query": normalized,
        "kb_article_id": result["article_id"],
        "local_remediation_available": result["resolution"]
        == "local-remediation-available",
    }
    return {
        **result,
        "evidence_token": issue_evidence(
            case_id=case_id,
            stage="decision",
            sequence=[*previous["sequence"], "search_kb"],
            facts=facts,
        ),
    }


def _create_escalation_ticket(
    case_id: str,
    category: str,
    summary: str,
    severity: str,
    account_alias: str,
    decision_evidence_token: str,
) -> dict[str, str]:
    evidence = verify_evidence(
        decision_evidence_token,
        expected_case_id=case_id,
        expected_stage="decision",
    )
    normalized_alias = account_alias.strip().lower()
    if evidence["facts"].get("account_alias") != normalized_alias:
        raise EvidenceError("Escalation evidence belongs to a different account.")
    if evidence["facts"].get("local_remediation_available") is not False:
        raise EvidenceError("Escalation evidence does not require a handoff.")

    record = {
        "ticket_id": f"MOCK-{len(_TICKETS) + 1:04d}",
        "case_id": case_id.strip().lower(),
        "category": category.strip().lower(),
        "summary": summary.strip(),
        "severity": severity.strip().lower(),
        "account_alias": normalized_alias,
        "evidence_sequence": ",".join(evidence["sequence"]),
        "state": "mock-created",
        "destination": "in-memory-only",
    }
    _TICKETS.append(record.copy())
    return record


def reset_mock_tickets() -> None:
    _TICKETS.clear()


def mock_tickets() -> tuple[dict[str, str], ...]:
    return tuple(record.copy() for record in _TICKETS)


@tool(approval_mode="never_require")
def get_system_status(
    case_id: Annotated[
        str, Field(description="Stable fictional case ID shared by every SAFE step.")
    ],
    service: Annotated[
        str, Field(description="Mock service name: identity, email, or network.")
    ],
) -> dict[str, str]:
    """Return deterministic operational status from the local mock catalog."""
    return _get_system_status(case_id, service)


@tool(approval_mode="never_require")
def get_user_account(
    case_id: Annotated[
        str, Field(description="The same fictional case ID used for service status.")
    ],
    account_alias: Annotated[
        str,
        Field(description="Fictional alias: demo-user or locked-user."),
    ],
    service_evidence_token: Annotated[
        str,
        Field(description="Signed evidence returned by get_system_status."),
    ],
) -> dict[str, str | bool]:
    """Return non-PII account state from the local mock catalog."""
    return _get_user_account(case_id, account_alias, service_evidence_token)


@tool(approval_mode="never_require")
def search_kb(
    case_id: Annotated[
        str, Field(description="The same fictional case ID used by prior steps.")
    ],
    query: Annotated[str, Field(description="Helpdesk terms to search in the mock KB.")],
    account_evidence_token: Annotated[
        str,
        Field(description="Signed evidence returned by get_user_account."),
    ],
) -> dict[str, str | list[str]]:
    """Search the deterministic in-memory knowledge base."""
    return _search_kb(case_id, query, account_evidence_token)


@tool(approval_mode="never_require")
def create_escalation_ticket(
    case_id: Annotated[
        str, Field(description="The same fictional case ID used by prior steps.")
    ],
    category: Annotated[str, Field(description="Non-PII issue category.")],
    summary: Annotated[str, Field(description="Brief non-PII issue summary.")],
    severity: Annotated[
        Literal["low", "medium", "high"], Field(description="Mock ticket severity.")
    ],
    account_alias: Annotated[str, Field(description="Fictional account alias.")],
    decision_evidence_token: Annotated[
        str,
        Field(description="Signed decision evidence returned by search_kb."),
    ],
) -> dict[str, str]:
    """Create a harmless deterministic ticket in process memory only."""
    return _create_escalation_ticket(
        case_id,
        category,
        summary,
        severity,
        account_alias,
        decision_evidence_token,
    )


TOOLS = [
    get_system_status,
    get_user_account,
    search_kb,
    create_escalation_ticket,
]
