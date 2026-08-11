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
    EnforcementMode,
    InterventionPoint,
)
from agent_framework import (
    AgentContext,
    AgentMiddleware,
    AgentResponse,
    FunctionInvocationContext,
    FunctionMiddleware,
    Message,
    MiddlewareTermination,
)
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from evidence import (
    attach_result_evidence,
    bind_invocation,
    clear_invocation_decisions,
    decisions_for_invocation,
    evidence_snapshot_for_call,
    new_invocation_id,
    reset_invocation,
)
from fixtures import ALLOWED_TICKET_CATEGORY, ALLOWED_TICKET_SEVERITY
from tools import _create_escalation_ticket, mock_tickets


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


def _response_text(result: Any) -> str:
    """Best-effort assembled text of a non-streaming ``AgentResponse``.

    The ``output`` policy in this sample gates purely on host-recorded
    escalation state (never on scanning the model's own words), so this text
    only needs to be a stable, non-sensitive ``policy_target.value`` for the
    audit trail. A missing or unusual ``.text`` never breaks enforcement.
    """
    text = getattr(result, "text", None)
    return text if isinstance(text, str) else ""


def _escalations_snapshot(invocation_id: str) -> list[dict[str, Any]]:
    """Host-assembled, per-case escalation state for one invocation.

    Built only from host-verified decision evidence
    (:func:`evidence.decisions_for_invocation`) and the host's own ticket
    store (:func:`tools.mock_tickets`). The model never supplies or
    influences any part of this list.
    """
    existing_case_ids = {ticket["case_id"] for ticket in mock_tickets()}
    escalations: list[dict[str, Any]] = []
    for case_id, record in decisions_for_invocation(invocation_id).items():
        facts = record.get("facts")
        facts = facts if isinstance(facts, dict) else {}
        escalations.append(
            {
                "case_id": case_id,
                "local_remediation_available": facts.get(
                    "local_remediation_available", True
                )
                is True,
                "ticket_exists": case_id in existing_case_ids,
            }
        )
    return escalations


def _host_escalation_response(
    original: Any, tickets: list[dict[str, str]]
) -> AgentResponse:
    """Replace a stale model answer after the host completes the handoff."""
    if len(tickets) == 1:
        ticket = tickets[0]
        text = (
            "HelpdeskBot completed the required human handoff. "
            f"Support ticket {ticket['ticket_id']} was created for "
            f"case {ticket['case_id']} with category {ticket['category']} "
            f"and {ticket['severity']} severity."
        )
    else:
        ticket_ids = ", ".join(ticket["ticket_id"] for ticket in tickets)
        text = (
            "HelpdeskBot completed the required human handoffs. "
            f"Support tickets {ticket_ids} were created."
        )

    messages = list(getattr(original, "messages", []) or [])
    replacement = Message("assistant", [text])
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if (
            getattr(message, "role", None) == "assistant"
            and getattr(message, "text", "")
        ):
            messages[index] = replacement
            break
    else:
        messages.append(replacement)

    return AgentResponse(
        messages=messages,
        response_id=getattr(original, "response_id", None),
        agent_id=getattr(original, "agent_id", None),
        created_at=getattr(original, "created_at", None),
        finish_reason=getattr(original, "finish_reason", None),
        usage_details=getattr(original, "usage_details", None),
    )


def _configure_bundled_opa(
    opa_path: Path = BUNDLED_OPA,
    runtime_dir: Path | None = None,
) -> Path | None:
    if not opa_path.is_file():
        return None

    runtime_root = runtime_dir or Path(tempfile.gettempdir()) / "helpdeskbot-acs"
    runtime_root.mkdir(parents=True, exist_ok=True)
    runtime_opa = runtime_root / "opa"
    if not runtime_opa.exists():
        # The staged copy never changes (it always comes from the same
        # bundled `opa_path`), and re-copying over a binary that is
        # *currently executing* races with the kernel ("Text file busy").
        # Two middlewares (or two instances of the same one) staging OPA in
        # close succession is an expected shape now that both
        # AcsFunctionMiddleware and AcsOutputMiddleware call this at
        # construction time, so this only ever copies once per process.
        try:
            shutil.copyfile(opa_path, runtime_opa)
        except FileExistsError:
            pass
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


class AcsOutputMiddleware(AgentMiddleware):
    """Enforce the ACS ``output`` intervention point on the assembled response.

    ``AcsFunctionMiddleware`` governs individual tool calls. It cannot, by
    itself, guarantee the property this class exists for: once diagnostics
    prove ``local_remediation_available=false`` for a case, the Hosted Agent
    must not release *any* final response for that invocation unless exactly
    one escalation ticket exists for that case. Whether the model remembers
    to call ``create_escalation_ticket`` is irrelevant -- the host checks
    after the response is fully assembled and, if a ticket is missing,
    creates it itself from already-verified decision evidence rather than
    asking the model to retry.

    Concurrency: multiple ``agent.run()`` invocations can be in flight at
    once (different sessions, possibly the same ``case_id``). A fresh,
    unguessable ``invocation_id`` is minted for every call to :meth:`process`
    and bound to a ``contextvar`` for the lifetime of ``call_next()``, so
    decision evidence recorded by nested tool calls
    (``evidence.record_decision_evidence``) is naturally scoped to *this*
    invocation only -- see ``evidence.py`` for why a contextvar, not a
    session id, is the correlation key.
    """

    def __init__(
        self,
        manifest_path: Path = POLICY_MANIFEST,
        *,
        control: AgentControl | None = None,
    ) -> None:
        _configure_bundled_opa()
        self._control = control or AgentControl.from_path(str(manifest_path))

    async def process(
        self,
        context: AgentContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        if context.stream:
            # A ResponseStream can deliver tokens to the caller as soon as
            # they are produced. By the time a full response exists to
            # gate, some of it may already be irrevocably sent, and this
            # sample has no buffering story that would let it un-send
            # tokens. Rather than assemble output it cannot safely gate,
            # it fails closed before running anything at all.
            context.result = None
            raise MiddlewareTermination(
                "SAFE output gate: streaming responses are not supported "
                "by this sample; failing closed."
            )

        invocation_id = new_invocation_id()
        token = bind_invocation(invocation_id)
        try:
            try:
                await call_next()
            finally:
                # Reads performed by output enforcement use `invocation_id`
                # directly. Unbind before enforcement so nested work cannot
                # accidentally add more evidence to the completed turn.
                reset_invocation(token)

            await self._enforce_output(context, invocation_id)
        finally:
            # Decision evidence is an invocation-scoped enforcement input,
            # not durable application state. Remove it on success and on
            # every failure path so the registry cannot grow indefinitely.
            clear_invocation_decisions(invocation_id)

    async def _enforce_output(self, context: AgentContext, invocation_id: str) -> None:
        # The default span context manager records the exception message and
        # the stack trace as a span event. An ACS failure can quote snapshot
        # contents, so the span reports only the exception type and reason
        # code, matching AcsFunctionMiddleware's discipline above.
        with TRACER.start_as_current_span(
            "acs.policy.evaluate",
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            span.set_attribute(
                "acs.intervention_point", InterventionPoint.OUTPUT.value
            )
            text = _response_text(context.result)
            try:
                allowed = await self._check(invocation_id, text, span)
                if not allowed:
                    allowed = await self._remediate_and_recheck(
                        context, invocation_id, span
                    )
            except Exception as exc:
                # ACS itself failed, or host-side ticket remediation raised
                # unexpectedly. Either way this is not a policy verdict this
                # sample can trust, so it fails closed rather than release a
                # response an unhealthy policy engine never actually cleared.
                span.set_status(
                    Status(StatusCode.ERROR, f"unhandled:{type(exc).__name__}")
                )
                context.result = None
                raise MiddlewareTermination(
                    "SAFE output gate: policy evaluation failed; failing closed."
                ) from exc

            if not allowed:
                span.set_status(
                    Status(StatusCode.ERROR, "missing_escalation_ticket")
                )
                context.result = None
                raise MiddlewareTermination(
                    "SAFE output gate: a required escalation ticket is "
                    "missing for this invocation; failing closed."
                )

    async def _check(self, invocation_id: str, text: str, span: trace.Span) -> bool:
        """Evaluate and enforce the ``output`` verdict once. No side effects."""
        result = await self._control.evaluate_intervention_point(
            InterventionPoint.OUTPUT,
            {
                "output": text,
                "safe": {"escalations": _escalations_snapshot(invocation_id)},
            },
        )
        decision = getattr(result.verdict.decision, "value", None)
        if decision:
            span.set_attribute("acs.verdict", decision)
        if result.verdict.reason:
            span.set_attribute("acs.reason", _reason_code(result.verdict.reason))
        try:
            await self._control.enforce(
                InterventionPoint.OUTPUT, result, EnforcementMode.ENFORCE
            )
        except AgentControlBlocked:
            return False
        return True

    async def _remediate_and_recheck(
        self, context: AgentContext, invocation_id: str, span: trace.Span
    ) -> bool:
        """Bounded, single host-side remediation pass, then one re-check.

        Creates at most one ticket per unresolved case in this invocation,
        each from already-verified decision evidence alone -- never from
        model output -- and each still subject to the exact same SAFE
        ``pre_tool_call``/``post_tool_call`` policy a model-issued call would
        face. Deliberately not recursive and not a retry loop: this method
        runs once per :meth:`_enforce_output` call, and the caller does not
        invoke it again after it returns.
        """
        tickets = await self._create_missing_tickets(invocation_id, span)
        if not tickets:
            return False
        context.result = _host_escalation_response(context.result, tickets)
        span.set_attribute("safe.escalation.ticket_count", len(tickets))
        return await self._check(
            invocation_id, _response_text(context.result), span
        )

    async def _create_missing_tickets(
        self, invocation_id: str, span: trace.Span
    ) -> list[dict[str, str]]:
        decisions = decisions_for_invocation(invocation_id)
        existing_case_ids = {ticket["case_id"] for ticket in mock_tickets()}
        for case_id, record in decisions.items():
            facts = record.get("facts")
            facts = facts if isinstance(facts, dict) else {}
            if facts.get("local_remediation_available") is not False:
                continue  # Local remediation exists; nothing to escalate.
            if case_id in existing_case_ids:
                continue  # Already ticketed; creating again would duplicate.

            evidence_reference = record.get("evidence_reference")
            if not isinstance(evidence_reference, str):
                # Incomplete host-recorded state for this case. There is
                # nothing verified to build a ticket from, so this case is
                # left unresolved rather than guessed at.
                continue

            args: dict[str, Any] = {
                "case_id": case_id,
                "category": ALLOWED_TICKET_CATEGORY,
                "severity": ALLOWED_TICKET_SEVERITY,
                "decision_evidence_reference": evidence_reference,
            }
            prior_evidence = evidence_snapshot_for_call(
                "create_escalation_ticket", args
            )

            async def execute(effective_args: Any) -> Any:
                if not isinstance(effective_args, dict):
                    raise TypeError(
                        "ACS tool arguments must remain a JSON object."
                    )
                return _create_escalation_ticket(**effective_args)

            with TRACER.start_as_current_span(
                "acs.policy.evaluate",
                record_exception=False,
                set_status_on_exception=False,
            ) as ticket_span:
                ticket_span.set_attribute(
                    "acs.tool.name", "create_escalation_ticket"
                )
                ticket_span.set_attribute(
                    "acs.intervention_point",
                    InterventionPoint.PRE_TOOL_CALL.value,
                )
                for name, value in _evidence_attributes(prior_evidence).items():
                    ticket_span.set_attribute(name, value)

                try:
                    guarded = await self._control.run_tool(
                        "create_escalation_ticket",
                        args,
                        execute,
                        snapshot={"safe": {"evidence": prior_evidence}},
                    )
                except AgentControlBlocked as exc:
                    reason = exc.result.verdict.reason or "policy_denied"
                    ticket_span.set_attribute(
                        "acs.intervention_point",
                        exc.intervention_point.value,
                    )
                    ticket_span.set_attribute(
                        "acs.verdict", exc.result.verdict.decision.value
                    )
                    ticket_span.set_attribute(
                        "acs.reason", _reason_code(reason)
                    )
                    ticket_span.set_status(
                        Status(StatusCode.ERROR, _reason_code(reason))
                    )
                    if exc.intervention_point != InterventionPoint.PRE_TOOL_CALL:
                        raise
                    span.set_attribute(
                        "acs.remediation.blocked_case_reason",
                        _reason_code(reason),
                    )
                    continue
                except Exception as exc:
                    ticket_span.set_status(
                        Status(
                            StatusCode.ERROR,
                            f"unhandled:{type(exc).__name__}",
                        )
                    )
                    raise

                decision, reason = _verdict_of(
                    guarded, "pre_tool_call_result"
                )
                if decision:
                    ticket_span.set_attribute("acs.verdict", decision)
                if reason:
                    ticket_span.set_attribute(
                        "acs.reason", _reason_code(reason)
                    )
                post_decision, _ = _verdict_of(
                    guarded, "post_tool_call_result"
                )
                if post_decision:
                    ticket_span.set_attribute(
                        "acs.post_tool_call.verdict", post_decision
                    )

            existing_case_ids.add(case_id)

        tickets_by_case = {
            ticket["case_id"]: ticket for ticket in mock_tickets()
        }
        required_cases = {
            case_id
            for case_id, record in decisions.items()
            if isinstance(record.get("facts"), dict)
            and record["facts"].get("local_remediation_available") is False
        }
        return [
            tickets_by_case[case_id]
            for case_id in sorted(required_cases)
            if case_id in tickets_by_case
        ]
