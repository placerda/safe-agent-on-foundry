"""Agent Framework middleware that enforces ACS around every tool call."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

from agent_control_specification import (
    AgentControl,
    AgentControlBlocked,
    InterventionPoint,
)
from agent_framework import FunctionInvocationContext, FunctionMiddleware
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from evidence import attach_result_evidence, evidence_snapshot_for_call


POLICY_MANIFEST = Path(__file__).with_name("policies") / "manifest.yaml"
BUNDLED_OPA = Path(__file__).with_name("opa")
LOGGER = logging.getLogger(__name__)
TRACER = trace.get_tracer("helpdeskbot.acs")


_STABLE_CODE = re.compile(r"^[a-z][a-z0-9_]*$")


def _reason_code(reason: Any) -> str:
    """Reduce an evidence rejection to a bounded, queryable code.

    ``evidence_snapshot_for_call`` reports either a stable code such as
    ``missing_evidence`` or a stringified ``EvidenceError``. Those messages are
    written for a developer, not for a telemetry backend: they are unbounded in
    cardinality and nothing stops a future message from quoting user-supplied
    text. Anything that is not already a code collapses to one value.
    """
    if isinstance(reason, str) and _STABLE_CODE.match(reason):
        return reason
    return "evidence_validation_failed"


def _evidence_attributes(prior_evidence: dict[str, Any]) -> dict[str, Any]:
    """Return the non-sensitive part of an evidence snapshot as span attributes.

    The signed envelope, the HMAC key, and the verified ``facts`` never reach
    telemetry. Only the reference, the flow position, and the validation
    outcome do.

    Only ``safe.evidence.valid`` is always present. A bootstrap snapshot has no
    reference to report, and a verified snapshot has nothing to reject, so the
    remaining attributes are conditional by design.
    """
    attributes: dict[str, Any] = {
        "safe.evidence.valid": prior_evidence.get("valid") is True,
    }
    for key, attribute in (
        ("evidence_id", "safe.evidence.id"),
        ("stage", "safe.evidence.stage"),
        ("audience", "safe.evidence.audience"),
    ):
        value = prior_evidence.get(key)
        if isinstance(value, str) and value:
            attributes[attribute] = value
    if prior_evidence.get("reason") is not None:
        attributes["safe.evidence.reason"] = _reason_code(prior_evidence["reason"])
    return attributes


def _verdict_of(run_result: Any, attribute: str) -> tuple[str | None, str | None]:
    """Return the ``(decision, reason)`` pair of one intervention point.

    Both values are optional. Telemetry must never break the enforcement path,
    so an unexpected result shape produces a thinner span rather than an error.
    """
    verdict = getattr(getattr(run_result, attribute, None), "verdict", None)
    decision = getattr(getattr(verdict, "decision", None), "value", None)
    return decision, getattr(verdict, "reason", None)


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

        # The default span context manager records the exception message and the
        # stack trace as a span event. A tool failure or an ACS interruption can
        # quote arguments or account data, so the span reports only the
        # exception type and the decision that produced it.
        with TRACER.start_as_current_span(
            "acs.policy.evaluate",
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            span.set_attribute("acs.tool.name", tool_name)
            span.set_attribute(
                "acs.intervention_point", InterventionPoint.PRE_TOOL_CALL.value
            )
            for name, value in _evidence_attributes(prior_evidence).items():
                span.set_attribute(name, value)

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
                verdict = exc.result.verdict
                reason = verdict.reason or "policy_denied"
                # Record where the block happened before deciding how to handle
                # it, so a post-tool-call block is still visible in the trace.
                span.set_attribute(
                    "acs.intervention_point", exc.intervention_point.value
                )
                span.set_attribute("acs.verdict", verdict.decision.value)
                span.set_attribute("acs.reason", _reason_code(reason))
                span.set_status(Status(StatusCode.ERROR, _reason_code(reason)))
                if exc.intervention_point != InterventionPoint.PRE_TOOL_CALL:
                    raise
                context.result = {
                    "status": "blocked_by_acs",
                    "intervention_point": exc.intervention_point.value,
                    "reason": reason,
                    "message": verdict.message or str(exc),
                }
                return
            except Exception as exc:
                # A tool raised, or ACS itself failed. The type name is enough to
                # find the trace; the message may quote arguments or account
                # data, so it stays out of the span.
                span.set_status(
                    Status(StatusCode.ERROR, f"unhandled:{type(exc).__name__}")
                )
                raise

            # ACS has five decisions, not two. Read the real one instead of
            # assuming that a call which did not raise was decided "allow".
            decision, reason = _verdict_of(guarded, "pre_tool_call_result")
            if decision:
                span.set_attribute("acs.verdict", decision)
            if reason:
                span.set_attribute("acs.reason", _reason_code(reason))
            post_decision, _ = _verdict_of(guarded, "post_tool_call_result")
            if post_decision:
                span.set_attribute("acs.post_tool_call.verdict", post_decision)

        context.result = guarded.value
