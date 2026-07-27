"""Four harmless, deterministic local tools used by HelpdeskBot."""

from __future__ import annotations

from typing import Literal

from agent_framework import tool
from pydantic import Field
from typing_extensions import Annotated


TOOL_NAMES = (
    "get_system_status",
    "get_user_account",
    "search_kb",
    "create_escalation_ticket",
)

_TICKETS: list[dict[str, str]] = []


def _get_system_status(service: str) -> dict[str, str]:
    normalized = service.strip().lower()
    status = {
        "identity": ("operational", "No active identity-service incident."),
        "email": ("degraded", "Synthetic mail queue delay: 4 minutes."),
        "network": ("operational", "No active network incident."),
    }
    state, detail = status.get(
        normalized, ("unknown", "Service is not in the mock catalog.")
    )
    return {
        "service": normalized,
        "state": state,
        "detail": detail,
        "source": "in-memory-mock",
    }


def _get_user_account(account_alias: str) -> dict[str, str | bool]:
    normalized = account_alias.strip().lower()
    if normalized != "demo-user":
        return {
            "account_alias": normalized,
            "found": False,
            "source": "in-memory-mock",
        }
    return {
        "account_alias": "demo-user",
        "found": True,
        "state": "active",
        "sign_in_allowed": True,
        "token_state": "expired",
        "source": "in-memory-mock",
    }


def _search_kb(query: str) -> dict[str, str | list[str]]:
    normalized = query.strip().lower()
    if "sign-in" in normalized or "token" in normalized:
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
    category: str,
    summary: str,
    severity: str,
    account_alias: str,
    diagnosis: str,
) -> dict[str, str]:
    record = {
        "ticket_id": f"MOCK-{len(_TICKETS) + 1:04d}",
        "category": category.strip().lower(),
        "summary": summary.strip(),
        "severity": severity.strip().lower(),
        "account_alias": account_alias.strip().lower(),
        "diagnosis": diagnosis.strip().lower(),
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
    service: Annotated[
        str, Field(description="Mock service name: identity, email, or network.")
    ],
) -> dict[str, str]:
    """Return deterministic operational status from the local mock catalog."""
    return _get_system_status(service)


@tool(approval_mode="never_require")
def get_user_account(
    account_alias: Annotated[
        str, Field(description="Fictional alias. Use demo-user for the demo case.")
    ],
) -> dict[str, str | bool]:
    """Return non-PII account state from the local mock catalog."""
    return _get_user_account(account_alias)


@tool(approval_mode="never_require")
def search_kb(
    query: Annotated[str, Field(description="Helpdesk terms to search in the mock KB.")],
) -> dict[str, str | list[str]]:
    """Search the deterministic in-memory knowledge base."""
    return _search_kb(query)


@tool(approval_mode="never_require")
def create_escalation_ticket(
    category: Annotated[str, Field(description="Non-PII issue category.")],
    summary: Annotated[str, Field(description="Brief non-PII issue summary.")],
    severity: Annotated[
        Literal["low", "medium", "high"], Field(description="Mock ticket severity.")
    ],
    account_alias: Annotated[str, Field(description="Fictional account alias.")],
    diagnosis: Annotated[
        Literal["no-local-remediation", "urgency-only"],
        Field(description="Evidence supporting escalation."),
    ],
) -> dict[str, str]:
    """Create a harmless deterministic ticket in process memory only."""
    return _create_escalation_ticket(
        category, summary, severity, account_alias, diagnosis
    )


TOOLS = [
    get_system_status,
    get_user_account,
    search_kb,
    create_escalation_ticket,
]

