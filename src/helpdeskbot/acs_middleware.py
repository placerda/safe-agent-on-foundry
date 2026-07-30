"""Agent Framework middleware that enforces ACS around every tool call."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from agent_control_specification import (
    AgentControl,
    AgentControlBlocked,
    InterventionPoint,
)
from agent_framework import FunctionInvocationContext, FunctionMiddleware

from evidence import attach_result_evidence, evidence_snapshot_for_call


POLICY_MANIFEST = Path(__file__).with_name("policies") / "manifest.yaml"
BUNDLED_OPA = Path(__file__).with_name("opa")
LOGGER = logging.getLogger(__name__)


def _result_shape(result: Any) -> str:
    if isinstance(result, list):
        item_types = ",".join(type(item).__name__ for item in result)
        return f"list[{item_types}]"
    return type(result).__name__


def _configure_bundled_opa(
    opa_path: Path = BUNDLED_OPA,
    runtime_dir: Path | None = None,
) -> Path | None:
    if not opa_path.is_file():
        return None

    runtime_root = runtime_dir or Path(tempfile.gettempdir()) / "helpdeskbot-acs"
    runtime_root.mkdir(parents=True, exist_ok=True)
    runtime_opa = runtime_root / "opa"
    shutil.copyfile(opa_path, runtime_opa)
    runtime_opa.chmod(0o755)
    parent = str(runtime_root)
    current_path = os.environ.get("PATH", "")
    path_entries = current_path.split(os.pathsep) if current_path else []
    if parent not in path_entries:
        os.environ["PATH"] = os.pathsep.join((parent, current_path))
    return runtime_opa


class AcsFunctionMiddleware(FunctionMiddleware):
    """Use Agent Framework as the policy-enforcement point for ACS."""

    def __init__(self, manifest_path: Path = POLICY_MANIFEST) -> None:
        _configure_bundled_opa()
        self._control = AgentControl.from_path(str(manifest_path))

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        tool_name = context.function.name
        arguments = dict(context.arguments)
        prior_evidence = evidence_snapshot_for_call(tool_name, arguments)

        async def execute(effective_args: Any) -> Any:
            if not isinstance(effective_args, dict):
                raise TypeError("ACS tool arguments must remain a JSON object.")
            context.arguments = effective_args
            await call_next()
            LOGGER.info(
                "SAFE middleware received %s result envelope for %s.",
                _result_shape(context.result),
                tool_name,
            )
            return attach_result_evidence(
                tool_name,
                effective_args,
                context.result,
                prior_evidence,
            )

        try:
            guarded = await self._control.run_tool(
                tool_name,
                arguments,
                execute,
                snapshot={
                    "safe": {
                        "evidence": prior_evidence
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

        context.result = guarded.value
