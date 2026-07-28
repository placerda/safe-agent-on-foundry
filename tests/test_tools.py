import pytest

from evidence import EvidenceError, verify_evidence
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


def test_diagnostic_outputs_are_deterministic_and_non_pii():
    status = _get_system_status("urgent-signin", "identity")
    assert status == _get_system_status("urgent-signin", "identity")
    assert verify_evidence(status["evidence_token"])["sequence"] == [
        "get_system_status"
    ]

    account = _get_user_account(
        "urgent-signin", "demo-user", status["evidence_token"]
    )
    assert account["token_state"] == "expired"
    assert not ({"name", "email", "phone", "address"} & account.keys())
    kb = _search_kb(
        "urgent-signin", "sign-in token expired", account["evidence_token"]
    )
    assert kb["article_id"] == "KB-1001"
    assert kb["resolution"] == "local-remediation-available"
    claims = verify_evidence(kb["evidence_token"])
    assert claims["sequence"] == [
        "get_system_status",
        "get_user_account",
        "search_kb",
    ]
    assert claims["facts"]["local_remediation_available"] is True


def test_ticket_effect_is_deterministic_and_process_local():
    reset_mock_tickets()
    status = _get_system_status("locked-signin", "identity")
    account = _get_user_account(
        "locked-signin", "locked-user", status["evidence_token"]
    )
    kb = _search_kb("locked-signin", "locked account", account["evidence_token"])
    first = _create_escalation_ticket(
        "locked-signin",
        "access",
        "Locked account has no local remediation",
        "medium",
        "locked-user",
        kb["evidence_token"],
    )
    second = _create_escalation_ticket(
        "locked-signin",
        "access",
        "Locked account has no local remediation",
        "medium",
        "locked-user",
        kb["evidence_token"],
    )
    assert first["ticket_id"] == "MOCK-0001"
    assert second["ticket_id"] == "MOCK-0002"
    assert first["destination"] == "in-memory-only"
    assert mock_tickets() == (first, second)
    reset_mock_tickets()


def test_known_remediation_cannot_create_ticket():
    status = _get_system_status("urgent-signin", "identity")
    account = _get_user_account(
        "urgent-signin", "demo-user", status["evidence_token"]
    )
    kb = _search_kb(
        "urgent-signin", "sign-in token expired", account["evidence_token"]
    )

    with pytest.raises(EvidenceError, match="does not require a handoff"):
        _create_escalation_ticket(
            "urgent-signin",
            "access",
            "Urgent sign-in failure",
            "medium",
            "demo-user",
            kb["evidence_token"],
        )


def test_fabricated_or_cross_case_evidence_fails_closed():
    with pytest.raises(EvidenceError):
        _get_user_account("urgent-signin", "demo-user", "fabricated")

    status = _get_system_status("urgent-signin", "identity")
    with pytest.raises(EvidenceError, match="different case"):
        _get_user_account(
            "locked-signin", "locked-user", status["evidence_token"]
        )


def test_tools_have_no_external_side_effects(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("an external side effect was attempted")

    monkeypatch.setattr("builtins.open", unexpected)
    monkeypatch.setattr("socket.create_connection", unexpected)
    monkeypatch.setattr("subprocess.run", unexpected)

    reset_mock_tickets()
    status = _get_system_status("locked-signin", "identity")
    account = _get_user_account(
        "locked-signin", "locked-user", status["evidence_token"]
    )
    kb = _search_kb("locked-signin", "locked account", account["evidence_token"])
    ticket = _create_escalation_ticket(
        "locked-signin",
        "access",
        "Locked account has no local remediation",
        "medium",
        "locked-user",
        kb["evidence_token"],
    )
    assert ticket["destination"] == "in-memory-only"
