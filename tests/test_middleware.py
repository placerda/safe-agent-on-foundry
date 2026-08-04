import os
from types import SimpleNamespace

import pytest
from agent_control_specification import (
    AgentControlBlocked,
    Decision,
    InterventionPoint,
    InterventionPointResult,
    Verdict,
)

from acs_middleware import AcsFunctionMiddleware, _configure_bundled_opa
from evidence import EvidenceError, issue_evidence


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
            "decision_evidence_token": decision_token(),
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
            "decision_evidence_token": "fabricated",
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
            "decision_evidence_token": decision_token(),
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
