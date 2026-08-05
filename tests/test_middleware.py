import os
from types import SimpleNamespace

import pytest
from agent_control_specification import (
    AgentControlBlocked,
    Decision,
    InterventionPoint,
    InterventionPointResult,
    ToolRunResult,
    Verdict,
)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode

import acs_middleware
from acs_middleware import AcsFunctionMiddleware, _configure_bundled_opa
from evidence import EvidenceError, issue_evidence


@pytest.fixture
def recorded_spans(monkeypatch):
    """Capture spans from a private provider instead of the global one."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(acs_middleware, "TRACER", provider.get_tracer("test"))
    return exporter


def permitting_result(value, decision=Decision.ALLOW):
    """Build the real ToolRunResult that ACS returns when a call proceeds.

    Using the actual dataclass rather than a stand-in means a field rename in
    the ACS package breaks this suite instead of silently degrading the span.
    """
    outcome = InterventionPointResult(
        verdict=Verdict(decision=decision),
        transformed_policy_target=None,
        policy_input={},
    )
    return ToolRunResult(
        value=value,
        pre_tool_call_result=outcome,
        post_tool_call_result=outcome,
    )


def middleware_with(control):
    middleware = AcsFunctionMiddleware.__new__(AcsFunctionMiddleware)
    middleware._control = control
    return middleware


def test_bundled_opa_is_added_to_path(monkeypatch, tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    opa = bundle / "opa"
    opa.write_bytes(b"test")
    monkeypatch.setenv("PATH", os.pathsep.join(("existing", "path")))
    runtime_dir = tmp_path / "runtime"

    runtime_opa = _configure_bundled_opa(opa, runtime_dir)

    assert runtime_opa == runtime_dir / "opa"
    assert runtime_opa.read_bytes() == b"test"
    assert os.environ["PATH"].split(os.pathsep)[0] == str(runtime_dir)


def decision_token() -> str:
    return issue_evidence(
        case_id="locked-signin",
        stage="decision",
        audience="create_escalation_ticket",
        sequence=["get_system_status", "get_user_account", "search_kb"],
        predecessor_id="account-evidence-id",
        facts={
            "service": "identity",
            "service_state": "operational",
            "account_alias": "locked-user",
            "account_found": True,
            "account_state": "locked",
            "sign_in_allowed": False,
            "token_state": "valid",
            "kb_query": "locked account",
            "kb_article_id": "KB-0000",
            "local_remediation_available": False,
        },
    )


@pytest.mark.asyncio
async def test_verified_claims_are_forwarded_to_acs_snapshot():
    captured = {}

    class AllowingControl:
        async def run_tool(self, name, args, execute, **kwargs):
            captured.update(kwargs)
            raw = await execute(args)
            return SimpleNamespace(value={**raw, "validated": True})

    context = SimpleNamespace(
        function=SimpleNamespace(name="create_escalation_ticket"),
        arguments={
            "case_id": "locked-signin",
            "account_alias": "locked-user",
            "decision_evidence_reference": decision_token(),
        },
        result=None,
    )

    async def call_next():
        context.result = {"ticket_id": "MOCK-0001"}

    await middleware_with(AllowingControl()).process(context, call_next)

    evidence = captured["snapshot"]["safe"]["evidence"]
    assert evidence["valid"] is True
    assert evidence["facts"]["local_remediation_available"] is False
    assert context.result["validated"] is True


@pytest.mark.asyncio
async def test_deny_never_executes_tool_and_becomes_structured_result():
    denial = InterventionPointResult(
        verdict=Verdict(
            decision=Decision.DENY,
            reason="unanchored_decision",
            message="Use trusted evidence.",
        ),
        transformed_policy_target=None,
        policy_input={},
    )

    class DenyingControl:
        async def run_tool(self, name, args, execute, **kwargs):
            raise AgentControlBlocked(InterventionPoint.PRE_TOOL_CALL, denial)

    context = SimpleNamespace(
        function=SimpleNamespace(name="create_escalation_ticket"),
        arguments={
            "case_id": "token-expired-signin",
            "decision_evidence_reference": "fabricated",
        },
        result=None,
    )
    calls = 0

    async def call_next():
        nonlocal calls
        calls += 1

    await middleware_with(DenyingControl()).process(context, call_next)

    assert calls == 0
    assert context.result["status"] == "blocked_by_acs"
    assert context.result["reason"] == "unanchored_decision"


@pytest.mark.asyncio
async def test_runtime_failure_propagates_fail_closed():
    class FailingControl:
        async def run_tool(self, name, args, execute, **kwargs):
            raise RuntimeError("policy runtime unavailable")

    context = SimpleNamespace(
        function=SimpleNamespace(name="search_kb"),
        arguments={"case_id": "token-expired-signin"},
        result=None,
    )

    async def call_next():
        raise AssertionError("tool must not run")

    with pytest.raises(RuntimeError, match="policy runtime unavailable"):
        await middleware_with(FailingControl()).process(context, call_next)


@pytest.mark.asyncio
async def test_post_tool_block_propagates_after_execution():
    denial = InterventionPointResult(
        verdict=Verdict(
            decision=Decision.DENY,
            reason="unsafe_result",
            message="Do not expose this result.",
        ),
        transformed_policy_target=None,
        policy_input={},
    )

    class PostDenyingControl:
        async def run_tool(self, name, args, execute, **kwargs):
            await execute(args)
            raise AgentControlBlocked(InterventionPoint.POST_TOOL_CALL, denial)

    context = SimpleNamespace(
        function=SimpleNamespace(name="create_escalation_ticket"),
        arguments={
            "case_id": "locked-signin",
            "decision_evidence_reference": decision_token(),
        },
        result=None,
    )
    calls = 0

    async def call_next():
        nonlocal calls
        calls += 1
        context.result = {"ticket_id": "MOCK-0001"}

    with pytest.raises(AgentControlBlocked) as blocked:
        await middleware_with(PostDenyingControl()).process(context, call_next)

    assert calls == 1
    assert blocked.value.intervention_point == InterventionPoint.POST_TOOL_CALL


@pytest.mark.asyncio
async def test_incomplete_diagnostic_result_fails_closed():
    class AllowingControl:
        async def run_tool(self, name, args, execute, **kwargs):
            return SimpleNamespace(value=await execute(args))

    context = SimpleNamespace(
        function=SimpleNamespace(name="get_system_status"),
        arguments={"case_id": "token-expired-signin", "service": "identity"},
        result=None,
    )

    async def call_next():
        context.result = {"state": "operational"}

    with pytest.raises(EvidenceError, match="outside scope or incomplete"):
        await middleware_with(AllowingControl()).process(context, call_next)


def escalation_context(reference):
    return SimpleNamespace(
        function=SimpleNamespace(name="create_escalation_ticket"),
        arguments={
            "case_id": "locked-signin",
            "account_alias": "locked-user",
            "decision_evidence_reference": reference,
        },
        result=None,
    )


@pytest.mark.asyncio
async def test_allow_span_reports_the_real_verdict(recorded_spans):
    class AllowingControl:
        async def run_tool(self, name, args, execute, **kwargs):
            return permitting_result(await execute(args))

    context = escalation_context(decision_token())

    async def call_next():
        context.result = {"ticket_id": "MOCK-0001"}

    await middleware_with(AllowingControl()).process(context, call_next)

    span = recorded_spans.get_finished_spans()[0]
    assert span.name == "acs.policy.evaluate"
    assert span.attributes["acs.tool.name"] == "create_escalation_ticket"
    assert span.attributes["acs.intervention_point"] == "pre_tool_call"
    assert span.attributes["acs.verdict"] == "allow"
    assert span.attributes["acs.post_tool_call.verdict"] == "allow"
    assert span.attributes["safe.evidence.valid"] is True
    assert span.attributes["safe.evidence.stage"] == "decision"
    assert span.attributes["safe.evidence.id"]
    assert span.status.status_code is not StatusCode.ERROR


@pytest.mark.asyncio
async def test_permitting_verdict_is_not_hardcoded_to_allow(recorded_spans):
    class WarningControl:
        async def run_tool(self, name, args, execute, **kwargs):
            return permitting_result(await execute(args), Decision.WARN)

    context = escalation_context(decision_token())

    async def call_next():
        context.result = {"ticket_id": "MOCK-0001"}

    await middleware_with(WarningControl()).process(context, call_next)

    span = recorded_spans.get_finished_spans()[0]
    assert span.attributes["acs.verdict"] == "warn"


@pytest.mark.asyncio
async def test_span_never_carries_facts_arguments_or_the_signed_token(
    recorded_spans,
):
    class AllowingControl:
        async def run_tool(self, name, args, execute, **kwargs):
            return permitting_result(await execute(args))

    token = decision_token()
    context = escalation_context(token)

    async def call_next():
        context.result = {"ticket_id": "MOCK-0001"}

    await middleware_with(AllowingControl()).process(context, call_next)

    span = recorded_spans.get_finished_spans()[0]
    serialized = repr(dict(span.attributes))
    assert token not in serialized
    assert "locked-user" not in serialized
    assert "unit-test-secret" not in serialized
    assert not any(key.startswith("safe.evidence.facts") for key in span.attributes)


@pytest.mark.asyncio
async def test_deny_span_records_reason_and_error_status(recorded_spans):
    denial = InterventionPointResult(
        verdict=Verdict(
            decision=Decision.DENY,
            reason="unanchored_decision",
            message="Use trusted evidence.",
        ),
        transformed_policy_target=None,
        policy_input={},
    )

    class DenyingControl:
        async def run_tool(self, name, args, execute, **kwargs):
            raise AgentControlBlocked(InterventionPoint.PRE_TOOL_CALL, denial)

    context = escalation_context("fabricated")

    async def call_next():
        raise AssertionError("tool must not run")

    await middleware_with(DenyingControl()).process(context, call_next)

    span = recorded_spans.get_finished_spans()[0]
    assert span.attributes["acs.verdict"] == "deny"
    assert span.attributes["acs.reason"] == "unanchored_decision"
    assert span.attributes["acs.intervention_point"] == "pre_tool_call"
    assert span.attributes["safe.evidence.valid"] is False
    assert span.attributes["safe.evidence.reason"]
    assert span.status.status_code is StatusCode.ERROR


@pytest.mark.asyncio
async def test_post_tool_block_is_recorded_before_it_propagates(recorded_spans):
    denial = InterventionPointResult(
        verdict=Verdict(
            decision=Decision.DENY,
            reason="unsafe_result",
            message="Do not expose this result.",
        ),
        transformed_policy_target=None,
        policy_input={},
    )

    class PostDenyingControl:
        async def run_tool(self, name, args, execute, **kwargs):
            await execute(args)
            raise AgentControlBlocked(InterventionPoint.POST_TOOL_CALL, denial)

    context = escalation_context(decision_token())

    async def call_next():
        context.result = {"ticket_id": "MOCK-0001"}

    with pytest.raises(AgentControlBlocked):
        await middleware_with(PostDenyingControl()).process(context, call_next)

    span = recorded_spans.get_finished_spans()[0]
    assert span.attributes["acs.intervention_point"] == "post_tool_call"
    assert span.attributes["acs.verdict"] == "deny"
    assert span.attributes["acs.reason"] == "unsafe_result"
    assert span.status.status_code is StatusCode.ERROR


@pytest.mark.asyncio
async def test_escaping_exception_never_records_its_message(recorded_spans):
    """A tool failure must not leak its message or stack trace into telemetry.

    OpenTelemetry records an exception event automatically unless the span is
    opened with recording disabled. That event carries the exception message and
    the formatted traceback, neither of which is filtered by the attribute
    allowlist, so the span is opened with automatic recording turned off.
    """

    class ExplodingControl:
        async def run_tool(self, name, args, execute, **kwargs):
            raise RuntimeError("locked-user token unit-test-secret leaked")

    context = escalation_context("fabricated")

    async def call_next():
        raise AssertionError("tool must not run")

    with pytest.raises(RuntimeError):
        await middleware_with(ExplodingControl()).process(context, call_next)

    span = recorded_spans.get_finished_spans()[0]
    assert span.events == ()
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description == "unhandled:RuntimeError"
    assert "locked-user" not in repr(span.status.description)
    assert "unit-test-secret" not in repr(dict(span.attributes))


@pytest.mark.asyncio
async def test_post_tool_block_records_no_exception_event(recorded_spans):
    """The propagating AgentControlBlocked must not add an exception event."""
    denial = InterventionPointResult(
        verdict=Verdict(
            decision=Decision.DENY,
            reason="unsafe_result",
            message="Do not expose account locked-user to the model.",
        ),
        transformed_policy_target=None,
        policy_input={},
    )

    class PostDenyingControl:
        async def run_tool(self, name, args, execute, **kwargs):
            await execute(args)
            raise AgentControlBlocked(InterventionPoint.POST_TOOL_CALL, denial)

    context = escalation_context(decision_token())

    async def call_next():
        context.result = {"ticket_id": "MOCK-0001"}

    with pytest.raises(AgentControlBlocked):
        await middleware_with(PostDenyingControl()).process(context, call_next)

    span = recorded_spans.get_finished_spans()[0]
    assert span.events == ()
    assert span.status.description == "unsafe_result"
    assert "locked-user" not in repr(span.status.description)


def test_evidence_reason_is_reduced_to_a_bounded_code():
    """Rejection sentences are collapsed so the attribute stays queryable."""
    assert acs_middleware._reason_code("flow_integrity_violation") == (
        "flow_integrity_violation"
    )
    assert acs_middleware._reason_code("stage_mismatch") == "stage_mismatch"
    assert (
        acs_middleware._reason_code(
            "Evidence reference ev:4f123 is addressed to search_kb, "
            "not create_escalation_ticket."
        )
        == "evidence_validation_failed"
    )
    assert acs_middleware._reason_code(None) == "evidence_validation_failed"


@pytest.mark.asyncio
async def test_untrusted_evidence_reason_is_a_code_not_a_sentence(recorded_spans):
    class DenyingControl:
        async def run_tool(self, name, args, execute, **kwargs):
            raise AgentControlBlocked(
                InterventionPoint.PRE_TOOL_CALL,
                InterventionPointResult(
                    verdict=Verdict(
                        decision=Decision.DENY, reason="unanchored_decision"
                    ),
                    transformed_policy_target=None,
                    policy_input={},
                ),
            )

    context = escalation_context("ev:definitely-not-a-real-reference")

    async def call_next():
        raise AssertionError("tool must not run")

    await middleware_with(DenyingControl()).process(context, call_next)

    reason = recorded_spans.get_finished_spans()[0].attributes[
        "safe.evidence.reason"
    ]
    assert " " not in reason
    assert reason.islower()
