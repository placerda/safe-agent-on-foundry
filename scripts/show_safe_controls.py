from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from agent_control_specification import AgentControl, AgentControlBlocked


ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = ROOT / "src" / "helpdeskbot"
MANIFEST = AGENT_ROOT / "policies" / "manifest.yaml"
sys.path.insert(0, str(AGENT_ROOT))


def snapshot(
    *,
    valid: bool = True,
    sequence: list[str] | None = None,
    case_id: str = "locked-signin",
    account_alias: str = "locked-user",
    local_remediation_available: bool = False,
) -> dict:
    return {
        "safe": {
            "evidence": {
                "valid": valid,
                "stage": "decision",
                "audience": "create_escalation_ticket",
                "case_id": case_id,
                "sequence": sequence
                or ["get_system_status", "get_user_account", "search_kb"],
                "facts": {
                    "account_alias": account_alias,
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


async def main() -> None:
    control = AgentControl.from_path(str(MANIFEST))
    ticket = {
        "case_id": "locked-signin",
        "category": "access",
        "summary": "Locked account has no local remediation",
        "severity": "medium",
        "account_alias": "locked-user",
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
            evidence=snapshot(valid=False),
        ),
        await check(
            control,
            "Escalation",
            tool_name="create_escalation_ticket",
            arguments={
                **ticket,
                "case_id": "token-expired-signin",
                "account_alias": "alex-user",
            },
            evidence=snapshot(
                case_id="token-expired-signin",
                account_alias="alex-user",
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

    print(f"{'Check':<20} {'ACS result':<42} Tool executed")
    print("-" * 78)
    for principle, verdict, executed in results:
        result = verdict if verdict == "allow" else f"deny: {verdict}"
        print(f"{principle:<20} {result:<42} {str(executed).lower()}")


if __name__ == "__main__":
    asyncio.run(main())
