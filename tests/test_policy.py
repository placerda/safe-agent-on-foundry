from pathlib import Path

import pytest
from agent_hooks import InterceptionBlocked
from policy_support import PolicyDriver


MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "helpdeskbot"
    / "policies"
    / "manifest.yaml"
)


@pytest.fixture(scope="module")
def control():
    return PolicyDriver(MANIFEST)


def safe_snapshot(
    *,
    valid: bool = True,
    case_id: str = "locked-signin",
    local_remediation_available: bool = False,
) -> dict:
    return {
        "safe": {
            "evidence": {
                "valid": valid,
                "stage": "decision",
                "audience": "create_escalation_ticket",
                "case_id": case_id,
                "sequence": [
                    "get_system_status",
                    "get_user_account",
                    "search_kb",
                ],
                "facts": {
                    "local_remediation_available": local_remediation_available,
                },
            }
        }
    }


async def never_execute(_args):
    raise AssertionError("denied tool must not execute")


@pytest.mark.asyncio
async def test_first_diagnostic_step_is_allowed(control):
    calls = 0

    async def execute(args):
        nonlocal calls
        calls += 1
        return {"service": args["service"], "state": "operational"}

    result = await control.run_tool(
        "get_system_status",
        {"case_id": "token-expired-signin", "service": "identity"},
        execute,
        snapshot=safe_snapshot(),
    )

    assert result.value["state"] == "operational"
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["get_user_account", "search_kb"])
async def test_skipped_diagnostic_prerequisite_is_blocked(control, tool_name):
    arguments = {"case_id": "token-expired-signin"}
    with pytest.raises(InterceptionBlocked) as blocked:
        await control.run_tool(
            tool_name,
            arguments,
            never_execute,
            snapshot=safe_snapshot(valid=False),
        )

    assert blocked.value.result.verdict.reason == "flow_integrity_violation"


@pytest.mark.asyncio
async def test_fabricated_escalation_evidence_is_blocked(control):
    with pytest.raises(InterceptionBlocked) as blocked:
        await control.run_tool(
            "create_escalation_ticket",
            {
                "case_id": "locked-signin",
                "category": "access",
                "severity": "medium",
                "decision_evidence_reference": "fabricated",
            },
            never_execute,
            snapshot=safe_snapshot(valid=False),
        )

    assert blocked.value.result.verdict.reason == "unanchored_decision"


@pytest.mark.asyncio
async def test_scope_boundary_blocks_high_or_non_access_tickets(control):
    for category, severity in (("access", "high"), ("hardware", "medium")):
        with pytest.raises(InterceptionBlocked) as blocked:
            await control.run_tool(
                "create_escalation_ticket",
                {
                    "case_id": "locked-signin",
                    "category": category,
                    "severity": severity,
                    "decision_evidence_reference": "placeholder-token",
                },
                never_execute,
                snapshot=safe_snapshot(),
            )

        assert blocked.value.result.verdict.reason == "scope_boundary"


@pytest.mark.asyncio
async def test_known_local_remediation_blocks_escalation(control):
    with pytest.raises(InterceptionBlocked) as blocked:
        await control.run_tool(
            "create_escalation_ticket",
            {
                "case_id": "token-expired-signin",
                "category": "access",
                "severity": "medium",
                "decision_evidence_reference": "placeholder-token",
            },
            never_execute,
            snapshot=safe_snapshot(
                case_id="token-expired-signin",
                local_remediation_available=True,
            ),
        )

    assert blocked.value.result.verdict.reason == "local_remediation_available"


@pytest.mark.asyncio
async def test_evidence_subject_must_match_ticket(control):
    with pytest.raises(InterceptionBlocked) as blocked:
        await control.run_tool(
            "create_escalation_ticket",
            {
                "case_id": "locked-signin",
                "category": "access",
                "severity": "medium",
                "decision_evidence_reference": "placeholder-token",
            },
            never_execute,
            snapshot=safe_snapshot(case_id="token-expired-signin"),
        )

    assert blocked.value.result.verdict.reason == "evidence_subject_mismatch"


@pytest.mark.asyncio
async def test_scope_boundary_has_priority_over_evidence_checks(control):
    with pytest.raises(InterceptionBlocked) as blocked:
        await control.run_tool(
            "create_escalation_ticket",
            {
                "case_id": "locked-signin",
                "category": "hardware",
                "severity": "high",
                "decision_evidence_reference": "fabricated",
            },
            never_execute,
            snapshot=safe_snapshot(valid=False),
        )

    assert blocked.value.result.verdict.reason == "scope_boundary"


@pytest.mark.asyncio
async def test_anchored_no_remediation_ticket_is_allowed(control):
    async def execute(args):
        return {"ticket_id": "MOCK-0001", "case_id": args["case_id"]}

    result = await control.run_tool(
        "create_escalation_ticket",
        {
            "case_id": "locked-signin",
            "category": "access",
            "severity": "medium",
            "decision_evidence_reference": "placeholder-token-value",
        },
        execute,
        snapshot=safe_snapshot(),
    )

    assert result.value["ticket_id"] == "MOCK-0001"
