import pytest
from agent_framework import SKIP_PARSING

from tools import (
    TOOL_NAMES,
    TOOLS,
    _create_escalation_ticket,
    _get_system_status,
    _get_user_account,
    _search_kb,
    mock_tickets,
    reset_mock_tickets,
)


def test_exactly_four_tool_contracts_are_registered():
    assert TOOL_NAMES == (
        "get_system_status",
        "get_user_account",
        "search_kb",
        "create_escalation_ticket",
    )
    assert len(TOOLS) == 4
    assert all(tool.result_parser is SKIP_PARSING for tool in TOOLS)


def test_diagnostic_outputs_are_deterministic_non_pii_raw_facts():
    status = _get_system_status("token-expired-signin", "identity")
    assert status == _get_system_status("token-expired-signin", "identity")
    assert "evidence_token" not in status

    account = _get_user_account("token-expired-signin", "alex-user", "host-verified")
    assert account["token_state"] == "expired"
    assert not ({"name", "email", "phone", "address"} & account.keys())

    kb = _search_kb("token-expired-signin", "sign-in token expired", "host-verified")
    assert kb["article_id"] == "KB-1001"
    assert kb["resolution"] == "local-remediation-available"


def test_scope_rejects_unknown_cases_services_aliases_and_pii():
    with pytest.raises(ValueError, match="outside"):
        _get_system_status("unknown-case", "identity")
    with pytest.raises(ValueError, match="outside"):
        _get_system_status("token-expired-signin", "email")
    with pytest.raises(ValueError, match="outside"):
        _get_user_account(
            "token-expired-signin", "customer@example.com", "host-verified"
        )
    with pytest.raises(ValueError, match="outside"):
        _create_escalation_ticket(
            "locked-signin",
            "access",
            "Contact customer@example.com",
            "medium",
            "locked-user",
            "host-verified",
        )


def test_ticket_creation_is_idempotent_per_case():
    reset_mock_tickets()
    first = _create_escalation_ticket(
        "locked-signin",
        "access",
        "Locked account has no local remediation",
        "medium",
        "locked-user",
        "host-verified",
    )
    second = _create_escalation_ticket(
        "locked-signin",
        "access",
        "Duplicate retry",
        "medium",
        "locked-user",
        "host-verified",
    )

    assert first["ticket_id"] == "MOCK-0001"
    assert second == first
    assert mock_tickets() == (first,)
    reset_mock_tickets()


def test_tools_have_no_external_side_effects(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("an external side effect was attempted")

    monkeypatch.setattr("builtins.open", unexpected)
    monkeypatch.setattr("socket.create_connection", unexpected)
    monkeypatch.setattr("subprocess.run", unexpected)

    reset_mock_tickets()
    _get_system_status("locked-signin", "identity")
    _get_user_account("locked-signin", "locked-user", "host-verified")
    _search_kb("locked-signin", "locked account", "host-verified")
    ticket = _create_escalation_ticket(
        "locked-signin",
        "access",
        "Locked account has no local remediation",
        "medium",
        "locked-user",
        "host-verified",
    )
    assert ticket["destination"] == "in-memory-only"
