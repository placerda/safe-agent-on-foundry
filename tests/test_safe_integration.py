from types import SimpleNamespace

import pytest

from acs_middleware import AcsFunctionMiddleware
from evidence import resolve_evidence_reference, verify_evidence
from tools import (
    _create_escalation_ticket,
    _get_system_status,
    _get_user_account,
    _search_kb,
    mock_tickets,
    reset_mock_tickets,
)


async def invoke(middleware, tool_name, arguments, implementation):
    context = SimpleNamespace(
        function=SimpleNamespace(name=tool_name),
        arguments=arguments,
        result=None,
    )

    async def call_next():
        context.result = implementation(**context.arguments)

    await middleware.process(context, call_next)
    return context.result


async def diagnostic_flow(case_id: str, account_alias: str):
    middleware = AcsFunctionMiddleware()
    status = await invoke(
        middleware,
        "get_system_status",
        {"case_id": case_id, "service": "identity"},
        _get_system_status,
    )
    account = await invoke(
        middleware,
        "get_user_account",
        {
            "case_id": case_id,
            "account_alias": account_alias,
            "service_evidence_token": status["evidence_token"],
        },
        _get_user_account,
    )
    kb = await invoke(
        middleware,
        "search_kb",
        {
            "case_id": case_id,
            "query": "sign-in diagnosis",
            "account_evidence_token": account["evidence_token"],
        },
        _search_kb,
    )
    return middleware, status, account, kb


@pytest.mark.asyncio
async def test_token_expired_case_preserves_flow_and_stops_on_local_remediation():
    reset_mock_tickets()
    middleware, status, account, kb = await diagnostic_flow(
        "token-expired-signin", "alex-user"
    )

    assert verify_evidence(resolve_evidence_reference(status["evidence_token"]))[
        "sequence"
    ] == [
        "get_system_status"
    ]
    assert verify_evidence(resolve_evidence_reference(account["evidence_token"]))[
        "sequence"
    ] == [
        "get_system_status",
        "get_user_account",
    ]
    assert verify_evidence(resolve_evidence_reference(kb["evidence_token"]))["facts"][
        "local_remediation_available"
    ] is True

    blocked = await invoke(
        middleware,
        "create_escalation_ticket",
        {
            "case_id": "token-expired-signin",
            "category": "access",
            "summary": "Expired sign-in token",
            "severity": "medium",
            "account_alias": "alex-user",
            "decision_evidence_token": kb["evidence_token"],
        },
        _create_escalation_ticket,
    )

    assert blocked["status"] == "blocked_by_acs"
    assert blocked["reason"] == "local_remediation_available"
    assert mock_tickets() == ()


@pytest.mark.asyncio
async def test_locked_case_creates_exactly_one_anchored_handoff():
    reset_mock_tickets()
    middleware, _, _, kb = await diagnostic_flow("locked-signin", "locked-user")
    arguments = {
        "case_id": "locked-signin",
        "category": "access",
        "summary": "Locked account has no local remediation",
        "severity": "medium",
        "account_alias": "locked-user",
        "decision_evidence_token": kb["evidence_token"],
    }

    first = await invoke(
        middleware,
        "create_escalation_ticket",
        arguments.copy(),
        _create_escalation_ticket,
    )
    replay = await invoke(
        middleware,
        "create_escalation_ticket",
        arguments.copy(),
        _create_escalation_ticket,
    )

    assert first["ticket_id"] == "MOCK-0001"
    assert replay == first
    assert mock_tickets() == (first,)


@pytest.mark.asyncio
async def test_scope_and_flow_fail_before_tool_execution():
    reset_mock_tickets()
    middleware = AcsFunctionMiddleware()

    skipped = await invoke(
        middleware,
        "search_kb",
        {
            "case_id": "token-expired-signin",
            "query": "skip account lookup",
            "account_evidence_token": "fabricated",
        },
        _search_kb,
    )
    pii = await invoke(
        middleware,
        "create_escalation_ticket",
        {
            "case_id": "locked-signin",
            "category": "access",
            "summary": "Contact customer@example.com",
            "severity": "medium",
            "account_alias": "locked-user",
            "decision_evidence_token": "fabricated",
        },
        _create_escalation_ticket,
    )

    assert skipped["reason"] == "flow_integrity_violation"
    assert pii["reason"] == "pii_in_ticket"
    assert mock_tickets() == ()
