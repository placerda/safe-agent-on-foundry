from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from agent_control_spec import AcsInterceptor
from agent_hooks import AgentContextBuilder, InterceptionBlocked, InterceptionEmitter


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
    control: InterceptionEmitter,
    principle: str,
    *,
    tool_name: str,
    arguments: dict,
    evidence: dict,
) -> tuple[str, str, bool]:
    executed = False

    builder = AgentContextBuilder(agent_id="safe-demo", framework="offline", session_id=principle)
    builder.with_l2(extensions={"safe.example/host": evidence["safe"]})
    try:
        outcome = await control.emit(builder.pre_tool_call(call_id="demo", name=tool_name, args=arguments))
        executed = True
        await control.emit(builder.post_tool_call(
            call_id="demo", name=tool_name, args=outcome.target, value={"ticket_id": "MOCK-0001"},
        ))
    except InterceptionBlocked as exc:
        return principle, exc.result.verdict.reason or "policy_denied", executed
    return principle, "allow", executed


async def check_output(
    control: InterceptionEmitter,
    *,
    ticket_exists: bool,
) -> tuple[str, str]:
    builder = AgentContextBuilder(agent_id="safe-demo", framework="offline", session_id="output")
    builder.with_l2(extensions={
            "safe.example/host": {
                "escalations": [
                    {
                        "case_id": "locked-signin",
                        "local_remediation_available": False,
                        "ticket_exists": ticket_exists,
                    }
                ]
            },
        })
    try:
        result = await control.emit(builder.output(content="Diagnostics are complete."))
    except InterceptionBlocked as exc:
        return (
            "Missing handoff",
            exc.result.verdict.reason or "policy_denied",
        )
    return "Completed handoff", result.record.verdict.reason or "allow"


async def main() -> None:
    control = InterceptionEmitter().register(AcsInterceptor(str(MANIFEST)), "acs")
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
        ("Completed handoff", "allow"),
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
            if verdict == "allow"
            else f"deny: {verdict}"
        )
        print(f"{check_name:<20} {result}")


if __name__ == "__main__":
    asyncio.run(main())
