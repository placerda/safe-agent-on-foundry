from types import SimpleNamespace

import pytest
from agent_control_specification import (
    AgentControlBlocked,
    Decision,
    InterventionPoint,
    InterventionPointResult,
    Verdict,
)

from acs_middleware import AcsFunctionMiddleware


def middleware_with(control):
    middleware = AcsFunctionMiddleware.__new__(AcsFunctionMiddleware)
    middleware._control = control
    return middleware


@pytest.mark.asyncio
async def test_allowed_call_uses_acs_value():
    class AllowingControl:
        async def run_tool(self, name, args, execute):
            raw = await execute(args)
            return SimpleNamespace(value={**raw, "validated": True})

    context = SimpleNamespace(
        function=SimpleNamespace(name="search_kb"),
        arguments={"query": "expired token"},
        result=None,
    )
    calls = 0

    async def call_next():
        nonlocal calls
        calls += 1
        context.result = {"article_id": "KB-1001"}

    await middleware_with(AllowingControl()).process(context, call_next)

    assert calls == 1
    assert context.result == {"article_id": "KB-1001", "validated": True}


@pytest.mark.asyncio
async def test_deny_never_executes_tool_and_becomes_structured_result():
    denial = InterventionPointResult(
        verdict=Verdict(
            decision=Decision.DENY,
            reason="diagnosis_required",
            message="Diagnose first.",
        ),
        transformed_policy_target=None,
        policy_input={},
    )

    class DenyingControl:
        async def run_tool(self, name, args, execute):
            raise AgentControlBlocked(InterventionPoint.PRE_TOOL_CALL, denial)

    context = SimpleNamespace(
        function=SimpleNamespace(name="create_escalation_ticket"),
        arguments={"diagnosis": "urgency-only"},
        result=None,
    )
    calls = 0

    async def call_next():
        nonlocal calls
        calls += 1

    await middleware_with(DenyingControl()).process(context, call_next)

    assert calls == 0
    assert context.result["status"] == "blocked_by_acs"
    assert context.result["reason"] == "diagnosis_required"


@pytest.mark.asyncio
async def test_runtime_failure_propagates_fail_closed():
    class FailingControl:
        async def run_tool(self, name, args, execute):
            raise RuntimeError("policy runtime unavailable")

    context = SimpleNamespace(
        function=SimpleNamespace(name="search_kb"),
        arguments={"query": "expired token"},
        result=None,
    )

    async def call_next():
        raise AssertionError("tool must not run")

    with pytest.raises(RuntimeError, match="policy runtime unavailable"):
        await middleware_with(FailingControl()).process(context, call_next)

