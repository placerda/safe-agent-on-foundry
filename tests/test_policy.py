from pathlib import Path

import pytest
from agent_control_specification import AgentControl, AgentControlBlocked


MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "helpdeskbot"
    / "policies"
    / "manifest.yaml"
)


@pytest.fixture(scope="module")
def control():
    return AgentControl.from_path(str(MANIFEST))


@pytest.mark.asyncio
async def test_diagnostic_tool_is_allowed(control):
    calls = 0

    async def execute(args):
        nonlocal calls
        calls += 1
        return {"service": args["service"], "state": "operational"}

    result = await control.run_tool(
        "get_system_status", {"service": "identity"}, execute
    )

    assert result.value["state"] == "operational"
    assert calls == 1


@pytest.mark.asyncio
async def test_urgency_only_ticket_is_blocked_before_execution(control):
    calls = 0

    async def execute(args):
        nonlocal calls
        calls += 1
        return {"ticket_id": "SHOULD-NOT-EXIST"}

    with pytest.raises(AgentControlBlocked) as blocked:
        await control.run_tool(
            "create_escalation_ticket",
            {
                "category": "access",
                "summary": "Urgent sign-in failure",
                "severity": "high",
                "account_alias": "other-user",
                "diagnosis": "urgency-only",
            },
            execute,
        )

    assert blocked.value.result.verdict.reason == "diagnosis_required"
    assert calls == 0


@pytest.mark.asyncio
async def test_known_demo_remediation_blocks_ticket(control):
    async def execute(args):
        return {"ticket_id": "SHOULD-NOT-EXIST"}

    with pytest.raises(AgentControlBlocked) as blocked:
        await control.run_tool(
            "create_escalation_ticket",
            {
                "category": "access",
                "summary": "Sign-in token expired",
                "severity": "medium",
                "account_alias": "  Demo-User ",
                "diagnosis": "  No-Local-Remediation ",
            },
            execute,
        )

    assert blocked.value.result.verdict.reason == "known_local_remediation"


@pytest.mark.asyncio
async def test_email_in_summary_has_highest_priority(control):
    async def execute(args):
        return {"ticket_id": "SHOULD-NOT-EXIST"}

    with pytest.raises(AgentControlBlocked) as blocked:
        await control.run_tool(
            "create_escalation_ticket",
            {
                "category": "access",
                "summary": "Contact customer@example.com",
                "severity": "high",
                "account_alias": "demo-user",
                "diagnosis": "urgency-only",
            },
            execute,
        )

    assert blocked.value.result.verdict.reason == "pii_in_ticket_summary"


@pytest.mark.asyncio
async def test_evidenced_non_demo_ticket_is_allowed(control):
    async def execute(args):
        return {"ticket_id": "MOCK-0001", "diagnosis": args["diagnosis"]}

    result = await control.run_tool(
        "create_escalation_ticket",
        {
            "category": "hardware",
            "summary": "No compatible printer driver",
            "severity": "medium",
            "account_alias": "other-user",
            "diagnosis": "no-local-remediation",
        },
        execute,
    )

    assert result.value["ticket_id"] == "MOCK-0001"
