from types import SimpleNamespace

import pytest

from evidence import (
    EvidenceError,
    attach_result_evidence,
    evidence_snapshot_for_call,
    issue_evidence,
    resolve_evidence_reference,
    verify_evidence,
)


def decision_facts(
    *,
    account_alias: str = "locked-user",
    local_remediation_available: bool = False,
) -> dict:
    return {
        "service": "identity",
        "service_state": "operational",
        "account_alias": account_alias,
        "account_found": True,
        "account_state": "locked",
        "sign_in_allowed": False,
        "token_state": "valid",
        "kb_query": "locked account",
        "kb_article_id": "KB-0000",
        "local_remediation_available": local_remediation_available,
    }


def decision_token(
    *,
    case_id: str = "locked-signin",
    sequence: list[str] | None = None,
) -> str:
    return issue_evidence(
        case_id=case_id,
        stage="decision",
        audience="create_escalation_ticket",
        sequence=sequence
        if sequence is not None
        else ["get_system_status", "get_user_account", "search_kb"],
        predecessor_id="account-evidence-id",
        facts=decision_facts(),
    )


def test_signature_tampering_is_rejected():
    token = decision_token()
    body, signature = token.split(".")
    replacement = "A" if signature[-1] != "A" else "B"

    with pytest.raises(EvidenceError, match="signature"):
        verify_evidence(f"{body}.{signature[:-1]}{replacement}")


def test_snapshot_contains_only_verified_strict_claims():
    snapshot = evidence_snapshot_for_call(
        "create_escalation_ticket",
        {
            "case_id": "locked-signin",
            "decision_evidence_token": decision_token(),
        },
    )

    assert snapshot["valid"] is True
    assert snapshot["stage"] == "decision"
    assert snapshot["audience"] == "create_escalation_ticket"
    assert snapshot["facts"]["local_remediation_available"] is False


def test_missing_cross_case_and_reordered_tokens_are_untrusted():
    missing = evidence_snapshot_for_call(
        "create_escalation_ticket", {"case_id": "locked-signin"}
    )
    cross_case = evidence_snapshot_for_call(
        "create_escalation_ticket",
        {
            "case_id": "token-expired-signin",
            "decision_evidence_token": decision_token(),
        },
    )
    reordered = evidence_snapshot_for_call(
        "create_escalation_ticket",
        {
            "case_id": "locked-signin",
            "decision_evidence_token": decision_token(
                sequence=["search_kb", "get_user_account"]
            ),
        },
    )

    assert missing["valid"] is False
    assert cross_case["valid"] is False
    assert reordered == {
        "valid": False,
        "reason": "flow_integrity_violation",
        "case_id": "locked-signin",
        "facts": {},
        "sequence": [],
    }


def test_host_issues_complete_chained_evidence_from_raw_results():
    start = evidence_snapshot_for_call(
        "get_system_status",
        {"case_id": "locked-signin", "service": "identity"},
    )
    status = attach_result_evidence(
        "get_system_status",
        {"case_id": "locked-signin", "service": "identity"},
        {"service": "identity", "state": "operational"},
        start,
    )
    account_input = {
        "case_id": "locked-signin",
        "account_alias": "locked-user",
        "service_evidence_token": status["evidence_token"],
    }
    account_prior = evidence_snapshot_for_call("get_user_account", account_input)
    account = attach_result_evidence(
        "get_user_account",
        account_input,
        {
            "account_alias": "locked-user",
            "found": True,
            "state": "locked",
            "sign_in_allowed": False,
            "token_state": "valid",
        },
        account_prior,
    )
    kb_input = {
        "case_id": "locked-signin",
        "query": "locked account",
        "account_evidence_token": account["evidence_token"],
    }
    kb_prior = evidence_snapshot_for_call("search_kb", kb_input)
    decision = attach_result_evidence(
        "search_kb",
        kb_input,
        {
            "article_id": "KB-0000",
            "resolution": "not-found",
        },
        kb_prior,
    )

    claims = verify_evidence(
        resolve_evidence_reference(decision["evidence_token"]),
        expected_case_id="locked-signin",
        expected_stage="decision",
        expected_audience="create_escalation_ticket",
        expected_sequence=["get_system_status", "get_user_account", "search_kb"],
    )
    assert claims["predecessor_id"] == kb_prior["evidence_id"]
    assert claims["facts"]["local_remediation_available"] is False


def test_host_accepts_framework_serialized_tool_object():
    result = attach_result_evidence(
        "get_system_status",
        {"case_id": "token-expired-signin", "service": "identity"},
        '{"service":"identity","state":"operational"}',
        {},
    )

    assert result["state"] == "operational"
    assert result["evidence_token"].startswith("ev:")
    assert (
        verify_evidence(resolve_evidence_reference(result["evidence_token"]))["stage"]
        == "system_status"
    )


def test_host_accepts_framework_content_wrapper():
    result = attach_result_evidence(
        "get_system_status",
        {"case_id": "token-expired-signin", "service": "identity"},
        [SimpleNamespace(text='{"service":"identity","state":"operational"}')],
        {},
    )

    assert result["state"] == "operational"
    assert (
        verify_evidence(resolve_evidence_reference(result["evidence_token"]))["stage"]
        == "system_status"
    )


def test_host_accepts_framework_function_result_wrapper():
    result = attach_result_evidence(
        "get_system_status",
        {"case_id": "token-expired-signin", "service": "identity"},
        [
            SimpleNamespace(
                text=None,
                result={"service": "identity", "state": "operational"},
            )
        ],
        {},
    )

    assert result["state"] == "operational"
    assert (
        verify_evidence(resolve_evidence_reference(result["evidence_token"]))["stage"]
        == "system_status"
    )


def test_host_rejects_result_that_changes_the_evidence_subject():
    status_token = issue_evidence(
        case_id="locked-signin",
        stage="system_status",
        audience="get_user_account",
        sequence=["get_system_status"],
        predecessor_id=None,
        facts={"service": "identity", "service_state": "operational"},
    )
    arguments = {
        "case_id": "locked-signin",
        "account_alias": "locked-user",
        "service_evidence_token": status_token,
    }
    prior = evidence_snapshot_for_call("get_user_account", arguments)

    with pytest.raises(EvidenceError, match="outside the case scope"):
        attach_result_evidence(
            "get_user_account",
            arguments,
            {
                "account_alias": "alex-user",
                "found": True,
                "state": "active",
                "sign_in_allowed": True,
                "token_state": "expired",
            },
            prior,
        )
