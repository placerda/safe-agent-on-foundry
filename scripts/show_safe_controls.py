from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from agent_control_specification import (
    AgentControl,
    AgentControlBlocked,
    EnforcementMode,
    InterventionPoint,
)


ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = ROOT / "src" / "helpdeskbot"
MANIFEST = AGENT_ROOT / "policies" / "manifest.yaml"
sys.path.insert(0, str(AGENT_ROOT))


def snapshot(
    *,
    valid: bool = True,
    sequence: list[str] | None = None,
    case_id: str = "locked-signin",
    stage: str = "decision",
    audience: str = "create_escalation_ticket",
    local_remediation_available: bool = False,
) -> dict:
    return {
        "safe": {
            "evidence": {
                "valid": valid,
                "stage": stage,
                "audience": audience,
                "case_id": case_id,
                "sequence": sequence
                or ["get_system_status", "get_user_account", "search_kb"],
                "facts": {
                    "local_remediation_available": local_remediation_available,
                },
            }
        }
    }


async def check(
    control: AgentControl,
    principle: str,
    *,
    tool_name: str,
    arguments: dict,
    evidence: dict,
) -> tuple[str, str, bool]:
    executed = False

    async def execute(_arguments: dict) -> dict:
        nonlocal executed
        executed = True
        return {"ticket_id": "MOCK-0001"}

    try:
        await control.run_tool(
            tool_name,
            arguments,
            execute,
            snapshot=evidence,
        )
    except AgentControlBlocked as exc:
        return principle, exc.result.verdict.reason or "policy_denied", executed
    return principle, "allow", executed


async def check_output(
    control: AgentControl,
    *,
    ticket_exists: bool,
) -> tuple[str, str]:
    result = await control.evaluate_intervention_point(
        InterventionPoint.OUTPUT,
        {
            "output": "Diagnostics are complete.",
            "safe": {
                "escalations": [
                    {
                        "case_id": "locked-signin",
                        "local_remediation_available": False,
                        "ticket_exists": ticket_exists,
                    }
                ]
            },
        },
    )
    try:
        await control.enforce(
            InterventionPoint.OUTPUT, result, EnforcementMode.ENFORCE
        )
    except AgentControlBlocked as exc:
        return (
            "Missing handoff",
            exc.result.verdict.reason or "policy_denied",
        )
    return "Completed handoff", result.verdict.reason or "allow"


async def main() -> None:
    from acs_middleware import _configure_bundled_opa

    _configure_bundled_opa()
    control = AgentControl.from_path(str(MANIFEST))
    ticket = {
        "case_id": "locked-signin",
        "category": "access",
        "severity": "medium",
        "decision_evidence_reference": "ev:demo",
    }

    results = [
        await check(
            control,
            "Scope",
            tool_name="create_escalation_ticket",
            arguments={**ticket, "severity": "high"},
            evidence=snapshot(),
        ),
        await check(
            control,
            "Anchored Decisions",
            tool_name="create_escalation_ticket",
            arguments={**ticket, "decision_evidence_reference": "fabricated"},
            evidence=snapshot(valid=False),
        ),
        await check(
            control,
            "Flow Integrity",
            tool_name="search_kb",
            arguments={"case_id": "locked-signin", "query": "locked account"},
            evidence=snapshot(
                stage="account",
                audience="search_kb",
                sequence=["get_system_status"],
            ),
        ),
        await check(
            control,
            "Escalation",
            tool_name="create_escalation_ticket",
            arguments={
                **ticket,
                "case_id": "token-expired-signin",
            },
            evidence=snapshot(
                case_id="token-expired-signin",
                local_remediation_available=True,
            ),
        ),
        await check(
            control,
            "Valid handoff",
            tool_name="create_escalation_ticket",
            arguments=ticket,
            evidence=snapshot(),
        ),
    ]

    expected = [
        ("Scope", "scope_boundary", False),
        ("Anchored Decisions", "unanchored_decision", False),
        ("Flow Integrity", "flow_integrity_violation", False),
        ("Escalation", "local_remediation_available", False),
        ("Valid handoff", "allow", True),
    ]
    if results != expected:
        raise AssertionError(f"Unexpected SAFE control results: {results!r}")

    output_results = [
        await check_output(control, ticket_exists=False),
        await check_output(control, ticket_exists=True),
    ]
    expected_output = [
        ("Missing handoff", "missing_escalation_ticket"),
        ("Completed handoff", "output_clear"),
    ]
    if output_results != expected_output:
        raise AssertionError(
            f"Unexpected SAFE output results: {output_results!r}"
        )

    print(f"{'Check':<20} {'ACS result':<42} Tool executed")
    print("-" * 78)
    for principle, verdict, executed in results:
        result = verdict if verdict == "allow" else f"deny: {verdict}"
        print(f"{principle:<20} {result:<42} {str(executed).lower()}")

    print()
    print(f"{'Output check':<20} ACS result")
    print("-" * 64)
    for check_name, verdict in output_results:
        result = (
            "allow"
            if verdict == "output_clear"
            else f"deny: {verdict}"
        )
        print(f"{check_name:<20} {result}")


if __name__ == "__main__":
    asyncio.run(main())
