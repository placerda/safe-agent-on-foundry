import pytest

from evidence import (
    EvidenceError,
    evidence_snapshot_for_call,
    issue_evidence,
    validate_result_evidence,
    verify_evidence,
)


def decision_token(case_id: str = "locked-signin") -> str:
    return issue_evidence(
        case_id=case_id,
        stage="decision",
        sequence=["get_system_status", "get_user_account", "search_kb"],
        facts={
            "account_alias": "locked-user",
            "local_remediation_available": False,
        },
    )


def test_signature_tampering_is_rejected():
    token = decision_token()
    body, signature = token.split(".")
    replacement = "A" if signature[-1] != "A" else "B"

    with pytest.raises(EvidenceError, match="signature"):
        verify_evidence(f"{body}.{signature[:-1]}{replacement}")


def test_snapshot_contains_only_verified_claims():
    token = decision_token()
    snapshot = evidence_snapshot_for_call(
        "create_escalation_ticket",
        {
            "case_id": "locked-signin",
            "decision_evidence_token": token,
        },
    )

    assert snapshot["valid"] is True
    assert snapshot["stage"] == "decision"
    assert snapshot["facts"]["local_remediation_available"] is False


def test_missing_and_cross_case_tokens_are_untrusted():
    assert (
        evidence_snapshot_for_call(
            "create_escalation_ticket", {"case_id": "locked-signin"}
        )["valid"]
        is False
    )
    assert (
        evidence_snapshot_for_call(
            "create_escalation_ticket",
            {
                "case_id": "urgent-signin",
                "decision_evidence_token": decision_token(),
            },
        )["valid"]
        is False
    )


def test_diagnostic_result_without_signed_evidence_fails_closed():
    with pytest.raises(EvidenceError, match="signed evidence"):
        validate_result_evidence("search_kb", {"article_id": "KB-1001"})
