"""Agent Framework middleware that enforces ACS around every tool call."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from agent_control_specification import (
    AgentControl,
    AgentControlBlocked,
    InterventionPoint,
)
from agent_framework import FunctionInvocationContext, FunctionMiddleware

from evidence import evidence_snapshot_for_call, validate_result_evidence


POLICY_MANIFEST = Path(__file__).with_name("policies") / "manifest.yaml"


class AcsFunctionMiddleware(FunctionMiddleware):
    """Use Agent Framework as the policy-enforcement point for ACS."""

    def __init__(self, manifest_path: Path = POLICY_MANIFEST) -> None:
        self._control = AgentControl.from_path(str(manifest_path))

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        async def execute(effective_args: Any) -> Any:
            if not isinstance(effective_args, dict):
                raise TypeError("ACS tool arguments must remain a JSON object.")
            context.arguments = effective_args
            await call_next()
            return context.result

        try:
            tool_name = context.function.name
            arguments = dict(context.arguments)
            guarded = await self._control.run_tool(
                tool_name,
                arguments,
                execute,
                snapshot={
                    "safe": {
                        "evidence": evidence_snapshot_for_call(tool_name, arguments)
                    }
                },
            )
        except AgentControlBlocked as exc:
            if exc.intervention_point != InterventionPoint.PRE_TOOL_CALL:
                raise
            verdict = exc.result.verdict
            context.result = {
                "status": "blocked_by_acs",
                "intervention_point": exc.intervention_point.value,
                "reason": verdict.reason or "policy_denied",
                "message": verdict.message or str(exc),
            }
            return

        validate_result_evidence(context.function.name, guarded.value)
        context.result = guarded.value
