"""Host authority composed with the complete native Agent Hooks middleware.

ACS/Regorus is the sole evaluator. This public emitter specialization owns
effective arguments, pending evidence, serialized tool brackets and fatality.
It never resumes a terminal denial or uses an audit sink for enforcement.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import inspect
from pathlib import Path
import re
from typing import Any

from agent_control_spec import AcsInterceptor
from agent_framework import (
    Agent, AgentMiddleware, AgentResponse, AgentResponseUpdate, Content, Message,
    MiddlewareFailure, ResponseStream, create_agent_hooks_middleware_from_emitter,
)
from agent_hooks import AgentContextBuilder, InterceptionBlocked, InterceptionEmitter
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from evidence import (
    accept_result_evidence, bind_invocation, clear_invocation_decisions,
    decisions_for_invocation, evidence_snapshot_for_call, new_invocation_id,
    prepare_result_evidence, reset_invocation,
)
from fixtures import ALLOWED_TICKET_CATEGORY, ALLOWED_TICKET_SEVERITY
from tools import TOOLS, TOOL_NAMES, _create_escalation_ticket

POLICY_MANIFEST = Path(__file__).with_name("policies") / "manifest.yaml"
HOST_NAMESPACE = "safe.example/host"
TRACER = trace.get_tracer("helpdeskbot.acs")


def reason_code(reason: Any) -> str:
    return reason if isinstance(reason, str) and re.fullmatch(r"[a-z][a-z0-9_:]{0,95}", reason) else "policy_failure"


class HostFailure(MiddlewareFailure):
    """A redacted fatal boundary error; the original ACS record stays available."""

    def __init__(self, code: str, record=None):
        self.code = reason_code(code)
        self.record = record
        super().__init__(f"SAFE boundary failed: {self.code}")


class SafeEmitter(InterceptionEmitter):
    """Specialize the SDK's public enforcing entrypoint, not its internals."""

    def __init__(self, invocation_id: str, tools=TOOLS, manifest=POLICY_MANIFEST):
        super().__init__()
        self.register(AcsInterceptor(str(manifest)), "acs")
        self.invocation_id = invocation_id
        self.functions = {item.name: item.func for item in tools}
        self.bracket = asyncio.Lock()
        self.active_call: str | None = None
        self.prior: dict[str, dict] = {}
        self.pending: dict[str, str] = {}
        self.tickets: dict[str, dict] = {}
        self.failure: HostFailure | None = None
        self.completed_calls: set[str] = set()
        self.handoff_messages: list[Message] = []

    def escalations(self):
        return [
            {"case_id": case_id,
             "local_remediation_available": item["facts"]["local_remediation_available"],
             "ticket_exists": case_id in self.tickets}
            for case_id, item in decisions_for_invocation(self.invocation_id).items()
        ]

    def _fail(self, code, record=None):
        self.failure = HostFailure(code, record)
        self.pending.clear()
        with TRACER.start_as_current_span(
            "safe.host.failure", record_exception=False, set_status_on_exception=False,
        ) as span:
            span.set_attribute("safe.failure.code", self.failure.code)
            span.set_status(Status(StatusCode.ERROR, self.failure.code))
        raise self.failure from None

    async def emit(self, ctx):
        point = ctx["interception_point"]
        call = ctx.get("tool_call", {})
        call_id = call.get("id")
        if point == "pre_tool_call":
            await self.bracket.acquire()
            self.active_call = call_id
        terminal = point == "post_tool_call"
        try:
            if self.failure is not None and point != "agent_shutdown":
                raise self.failure
            if point == "pre_tool_call":
                if call_id in self.completed_calls:
                    self._fail("duplicate_tool_call")
                function = self.functions.get(call["name"])
                if function is None:
                    self._fail("unknown_tool")
                bound = inspect.signature(function).bind_partial(**call["args"])
                bound.apply_defaults()
                call["args"] = dict(bound.arguments)
                ctx["target"] = call["args"]
                self.prior[call_id] = evidence_snapshot_for_call(call["name"], call["args"])
            elif point == "post_tool_call":
                if call_id != self.active_call or call_id not in self.prior:
                    self._fail("unpaired_tool_result")
                if not ctx["tool_result"]["is_error"] and call["name"] != "create_escalation_ticket":
                    value, token = prepare_result_evidence(
                        call["name"], call["args"], ctx["target"], self.prior[call_id],
                        call_id=call_id,
                    )
                    self.pending[call_id] = token
                    ctx["target"] = value
                    ctx["tool_result"]["value"] = value
            ctx["extensions"] = {HOST_NAMESPACE: {
                "evidence": self.prior.get(call_id, {}),
                "escalations": self.escalations(),
            }}
            proposed = deepcopy(ctx["target"])
            with TRACER.start_as_current_span(
                "acs.policy.evaluate", record_exception=False, set_status_on_exception=False,
            ) as span:
                span.set_attribute("acs.interception_point", point)
                if call:
                    span.set_attribute("acs.tool.name", call["name"] if call["name"] in TOOL_NAMES else "unknown")
                    span.set_attribute("safe.evidence.valid", self.prior.get(call_id, {}).get("valid") is True)
                try:
                    outcome = await super().emit(ctx)
                except InterceptionBlocked as blocked:
                    record = blocked.result
                    span.set_attribute("acs.verdict", record.verdict.decision.value)
                    span.set_attribute("acs.reason", reason_code(record.verdict.reason))
                    span.set_status(Status(StatusCode.ERROR, reason_code(record.verdict.reason)))
                    if terminal or str(record.verdict.reason).startswith(("runtime_error:", "host_error:")):
                        self._fail(record.verdict.reason, record)
                    raise
                span.set_attribute("acs.verdict", outcome.record.verdict.decision.value)
                span.set_attribute("acs.reason", reason_code(outcome.record.verdict.reason or "allow"))
            # This sample has no transform policies. Never authorize a signed
            # reference or callback against values different from those checked.
            if outcome.target != proposed:
                self._fail("unsupported_policy_transform", outcome.record)
            if terminal:
                if ctx["tool_result"]["is_error"]:
                    self._fail("tool_execution_failed", outcome.record)
                if call_id in self.pending:
                    accept_result_evidence(self.pending.pop(call_id))
                elif call["name"] == "create_escalation_ticket":
                    result = outcome.target
                    if not isinstance(result, dict) or result.get("case_id") != call["args"]["case_id"].strip().lower() or not result.get("ticket_id"):
                        self._fail("invalid_ticket_result", outcome.record)
                    self.tickets[result["case_id"]] = dict(result)
                self.completed_calls.add(call_id)
            return outcome
        except (InterceptionBlocked, HostFailure, asyncio.CancelledError):
            if point == "pre_tool_call":
                terminal = True
            raise
        except Exception:
            terminal = True
            self._fail("host_processing_failed")
        finally:
            if terminal and self.active_call == call_id:
                self.pending.pop(call_id, None)
                self.prior.pop(call_id, None)
                self.active_call = None
                if self.bracket.locked():
                    self.bracket.release()

    async def handoff(self, builder, arguments):
        call_id = new_invocation_id()
        outcome = await self.emit(builder.pre_tool_call(
            call_id=call_id, name="create_escalation_ticket", args=arguments,
        ))
        try:
            value = _create_escalation_ticket(**outcome.target)
        except Exception:
            await self.emit(builder.post_tool_call(
                call_id=call_id, name="create_escalation_ticket",
                args=outcome.target, value="tool_execution_failed", is_error=True,
            ))
            raise
        result = await self.emit(builder.post_tool_call(
            call_id=call_id, name="create_escalation_ticket", args=outcome.target, value=value,
        ))
        self.handoff_messages.extend([
            Message("assistant", [Content.from_function_call(
                call_id, "create_escalation_ticket", arguments=deepcopy(outcome.target),
            )]),
            Message("tool", [Content.from_function_result(call_id, result=deepcopy(result.target))]),
        ])
        return result.target


def response_stream(response):
    async def updates():
        for message in response.messages:
            yield AgentResponseUpdate(
                contents=message.contents, role=message.role,
                response_id=response.response_id, agent_id=response.agent_id,
            )
    return ResponseStream(updates(), finalizer=lambda _: response)


class HostHandoff(AgentMiddleware):
    """One bounded repair pass before, never after, the native output verdict."""

    def __init__(self, emitter, builder):
        self.emitter, self.builder = emitter, builder

    async def process(self, context, call_next):
        await call_next()
        result = context.result
        if isinstance(result, ResponseStream):
            result = await result.get_final_response()
        if not isinstance(result, AgentResponse):
            raise HostFailure("missing_agent_response")
        tickets = []
        for case_id, decision in decisions_for_invocation(self.emitter.invocation_id).items():
            if decision["facts"]["local_remediation_available"] is False and case_id not in self.emitter.tickets:
                tickets.append(await self.emitter.handoff(self.builder, {
                    "case_id": case_id,
                    "category": ALLOWED_TICKET_CATEGORY,
                    "severity": ALLOWED_TICKET_SEVERITY,
                    "decision_evidence_reference": decision["evidence_reference"],
                }))
        if tickets:
            text = "HelpdeskBot completed the required human handoff. " + " ".join(
                f"Support ticket {ticket['ticket_id']} for case {ticket['case_id']} "
                f"has category {ticket['category']} and {ticket['severity']} severity."
                for ticket in tickets
            )
            # Mutate the response object used by the deferred history persistence
            # as well as the output gate: stale model text must not persist.
            for message in reversed(result.messages):
                if message.role == "assistant" and message.text:
                    message.contents[:] = [
                        content for content in message.contents if content.type != "text"
                    ]
                    break
            result.messages[:] = [message for message in result.messages if message.contents]
            result.messages.extend(self.emitter.handoff_messages)
            result.messages.append(Message("assistant", [text]))
        context.result = response_stream(result) if context.stream else result


class SafeAgent(Agent):
    """An ordinary RawAgent-compatible Agent with public per-run middleware."""

    def __init__(self, *, client, instructions="", name="HelpdeskBot", tools=TOOLS,
                 manifest=POLICY_MANIFEST, **kwargs):
        if kwargs.get("middleware"):
            raise ValueError("SAFE owns the first complete middleware bundle.")
        super().__init__(client=client, instructions=instructions, name=name, tools=tools, **kwargs)
        self.safe_tools = list(tools)
        self.safe_manifest = manifest

    def run(self, messages=None, *, stream=False, session=None, **kwargs):
        if any(kwargs.get(key) for key in ("middleware", "tools")):
            raise ValueError("Run-time tool or middleware substitution is not permitted.")
        kwargs.pop("middleware", None)
        kwargs.pop("tools", None)
        final = []

        async def execute():
            invocation_id = new_invocation_id()
            token = bind_invocation(invocation_id)
            emitter = None
            reason = "error"
            try:
                emitter = SafeEmitter(invocation_id, self.safe_tools, self.safe_manifest)
                builder = AgentContextBuilder(
                    agent_id=self.id, framework="agent-framework", session_id=invocation_id,
                )
                await emitter.emit(builder.agent_startup(tools_registered=list(TOOL_NAMES)))
                result = super(SafeAgent, self).run(
                    messages, stream=stream, session=session,
                    middleware=[create_agent_hooks_middleware_from_emitter(emitter, builder),
                                HostHandoff(emitter, builder)],
                    **kwargs,
                )
                response = await result.get_final_response() if stream else await result
                reason = "completed"
                return response
            except asyncio.CancelledError:
                reason = "cancelled"
                raise
            except (HostFailure, InterceptionBlocked):
                raise
            except Exception:
                raise HostFailure("agent_execution_failed") from None
            finally:
                try:
                    if emitter is not None:
                        await emitter.emit(builder.agent_shutdown(reason=reason))
                finally:
                    clear_invocation_decisions(invocation_id)
                    reset_invocation(token)

        if not stream:
            return execute()

        async def updates():
            response = await execute()
            final.append(response)
            async for update in response_stream(response):
                yield update
        return ResponseStream(updates(), finalizer=lambda _: final[0])
