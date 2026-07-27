"""Agent Framework middleware that enforces ACS around every tool call."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from agent_control_specification import AgentControl, AgentControlBlocked
from agent_framework import FunctionInvocationContext, FunctionMiddleware


POLICY_MANIFEST = Path(__file__).with_name("policies") / "manifest.yaml"


class AcsFunctionMiddleware(FunctionMiddleware):
    """Use Agent Framework as the policy-enforcement point for ACS."""

    def __init__(self, manifest_path: Path = POLICY_MANIFEST) -> None:
        self._control = AgentControl.from_path(manifest_path)

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
            guarded = await self._control.run_tool(
                context.function.name,
                dict(context.arguments),
                execute,
            )
        except AgentControlBlocked as exc:
            verdict = exc.result.verdict
            context.result = {
                "status": "blocked_by_acs",
                "intervention_point": exc.intervention_point.value,
                "reason": verdict.reason or "policy_denied",
                "message": verdict.message or str(exc),
            }
            return

        context.result = guarded.value

