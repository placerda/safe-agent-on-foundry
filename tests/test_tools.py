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
    assert _get_system_status("identity") == _get_system_status("identity")
    account = _get_user_account("demo-user")
    assert account["token_state"] == "expired"
    assert not ({"name", "email", "phone", "address"} & account.keys())
    kb = _search_kb("sign-in token expired")
    assert kb["article_id"] == "KB-1001"
    assert kb["resolution"] == "local-remediation-available"


def test_ticket_effect_is_deterministic_and_process_local():
    reset_mock_tickets()
    first = _create_escalation_ticket(
        "access",
        "Printer driver is unavailable",
        "medium",
        "other-user",
        "no-local-remediation",
    )
    second = _create_escalation_ticket(
        "access",
        "Printer driver is unavailable",
        "medium",
        "other-user",
        "no-local-remediation",
    )
    assert first["ticket_id"] == "MOCK-0001"
    assert second["ticket_id"] == "MOCK-0002"
    assert first["destination"] == "in-memory-only"
    assert mock_tickets() == (first, second)
    reset_mock_tickets()


def test_tools_have_no_external_side_effects(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("an external side effect was attempted")

    monkeypatch.setattr("builtins.open", unexpected)
    monkeypatch.setattr("socket.create_connection", unexpected)
    monkeypatch.setattr("subprocess.run", unexpected)

    reset_mock_tickets()
    _get_system_status("identity")
    _get_user_account("demo-user")
    _search_kb("sign-in token expired")
    ticket = _create_escalation_ticket(
        "access",
        "Printer driver is unavailable",
        "medium",
        "other-user",
        "no-local-remediation",
    )
    assert ticket["destination"] == "in-memory-only"

