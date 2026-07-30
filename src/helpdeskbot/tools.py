"""Four harmless, deterministic local tools used by HelpdeskBot."""

from __future__ import annotations

from typing import Literal

from agent_framework import SKIP_PARSING, tool
from pydantic import Field
from typing_extensions import Annotated

from fixtures import (
    ALLOWED_SERVICE,
    ALLOWED_TICKET_CATEGORY,
    ALLOWED_TICKET_SEVERITY,
    CASE_ACCOUNTS,
)


TOOL_NAMES = (
    "get_system_status",
    "get_user_account",
    "search_kb",
    "create_escalation_ticket",
)

_TICKETS: list[dict[str, str]] = []


def _get_system_status(case_id: str, service: str) -> dict[str, str]:
    normalized_case = case_id.strip().lower()
    normalized_service = service.strip().lower()
    if normalized_case not in CASE_ACCOUNTS or normalized_service != ALLOWED_SERVICE:
        raise ValueError("Case or service is outside HelpdeskBot scope.")
    return {
        "service": normalized_service,
        "state": "operational",
        "detail": "No active identity-service incident.",
        "source": "in-memory-mock",
    }


def _get_user_account(
    case_id: str, account_alias: str, service_evidence_token: str
) -> dict[str, str | bool]:
    normalized_case = case_id.strip().lower()
    normalized_alias = account_alias.strip().lower()
    if (
        normalized_case not in CASE_ACCOUNTS
        or CASE_ACCOUNTS[normalized_case] != normalized_alias
        or not service_evidence_token
    ):
        raise ValueError("Case, account, or prerequisite is outside scope.")

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
    return accounts[normalized_alias]


def _search_kb(
    case_id: str, query: str, account_evidence_token: str
) -> dict[str, str | list[str]]:
    normalized_case = case_id.strip().lower()
    if normalized_case not in CASE_ACCOUNTS or not account_evidence_token:
        raise ValueError("Case or prerequisite is outside scope.")
    if not query.strip():
        raise ValueError("KB query is required.")

    if normalized_case == "urgent-signin":
        return {
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
    return {
        "article_id": "KB-0000",
        "title": "No matching mock article",
        "steps": [],
        "resolution": "not-found",
        "source": "in-memory-mock",
    }


def _create_escalation_ticket(
    case_id: str,
    category: str,
    summary: str,
    severity: str,
    account_alias: str,
    decision_evidence_token: str,
) -> dict[str, str]:
    normalized_case = case_id.strip().lower()
    normalized_alias = account_alias.strip().lower()
    if (
        normalized_case != "locked-signin"
        or normalized_alias != CASE_ACCOUNTS["locked-signin"]
        or category.strip().lower() != ALLOWED_TICKET_CATEGORY
        or severity.strip().lower() != ALLOWED_TICKET_SEVERITY
        or not decision_evidence_token
        or "@" in summary
    ):
        raise ValueError("Ticket request is outside HelpdeskBot scope.")

    existing = next(
        (ticket for ticket in _TICKETS if ticket["case_id"] == normalized_case),
        None,
    )
    if existing is not None:
        return existing.copy()

    record = {
        "ticket_id": f"MOCK-{len(_TICKETS) + 1:04d}",
        "case_id": normalized_case,
        "category": ALLOWED_TICKET_CATEGORY,
        "summary": summary.strip(),
        "severity": ALLOWED_TICKET_SEVERITY,
        "account_alias": normalized_alias,
        "idempotency_scope": normalized_case,
        "state": "mock-created",
        "destination": "in-memory-only",
    }
    _TICKETS.append(record.copy())
    return record


def reset_mock_tickets() -> None:
    _TICKETS.clear()


def mock_tickets() -> tuple[dict[str, str], ...]:
    return tuple(record.copy() for record in _TICKETS)


@tool(approval_mode="never_require", result_parser=SKIP_PARSING)
def get_system_status(
    case_id: Annotated[
        str, Field(description="Fictional case ID: urgent-signin or locked-signin.")
    ],
    service: Annotated[
        str,
        Field(description='Use the exact literal "identity".'),
    ],
) -> dict[str, str]:
    """Return deterministic identity status from the local mock catalog."""
    return _get_system_status(case_id, service)


@tool(approval_mode="never_require", result_parser=SKIP_PARSING)
def get_user_account(
    case_id: Annotated[
        str, Field(description="The same fictional case ID used for service status.")
    ],
    account_alias: Annotated[
        str,
        Field(
            description=(
                'Use exactly "demo-user" for urgent-signin or "locked-user" '
                "for locked-signin."
            )
        ),
    ],
    service_evidence_token: Annotated[
        str,
        Field(
            description=(
                "Copy evidence_token exactly from the immediately preceding "
                "get_system_status result."
            )
        ),
    ],
) -> dict[str, str | bool]:
    """Return non-PII account state from the local mock catalog."""
    return _get_user_account(case_id, account_alias, service_evidence_token)


@tool(approval_mode="never_require", result_parser=SKIP_PARSING)
def search_kb(
    case_id: Annotated[
        str, Field(description="The same fictional case ID used by prior steps.")
    ],
    query: Annotated[str, Field(description="Helpdesk terms to search in the mock KB.")],
    account_evidence_token: Annotated[
        str,
        Field(
            description=(
                "Copy evidence_token exactly from the immediately preceding "
                "get_user_account result."
            )
        ),
    ],
) -> dict[str, str | list[str]]:
    """Search the deterministic in-memory knowledge base."""
    return _search_kb(case_id, query, account_evidence_token)


@tool(approval_mode="never_require", result_parser=SKIP_PARSING)
def create_escalation_ticket(
    case_id: Annotated[
        str, Field(description="The locked-signin case requiring human handoff.")
    ],
    category: Annotated[
        str, Field(description="Must be access for this bounded sample.")
    ],
    summary: Annotated[str, Field(description="Brief non-PII issue summary.")],
    severity: Annotated[
        Literal["low", "medium", "high"],
        Field(description="Must be medium for this bounded sample."),
    ],
    account_alias: Annotated[
        str, Field(description="Must be the fictional locked-user alias.")
    ],
    decision_evidence_token: Annotated[
        str,
        Field(
            description=(
                "Copy evidence_token exactly from the immediately preceding "
                "search_kb result."
            )
        ),
    ],
) -> dict[str, str]:
    """Idempotently create one harmless in-memory handoff ticket per case."""
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
