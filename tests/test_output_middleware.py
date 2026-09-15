"""Host repair precedes the sole terminal native output approval."""
import asyncio

import pytest
from agent_framework import Message, tool, SKIP_PARSING
from agent_hooks import InterceptionBlocked
import evidence
import host_boundary
from host_boundary import HostFailure, SafeAgent
from native_support import ScriptClient, chain_script, fault_manifest, one_call, call
from tools import mock_tickets


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_output_allowed_without_ticket_when_local_remediation_exists(stream):
    agent = SafeAgent(client=ScriptClient(chain_script("token-expired-signin")))
    response = await agent.run("help", stream=True).get_final_response() if stream else await agent.run("help")
    assert response.text == "MODEL_DID_NOT_HANDOFF"
    assert not mock_tickets()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("model_ticket", [False, True])
async def test_host_creates_exactly_one_valid_ticket_when_required(stream, model_ticket, monkeypatch):
    points = []
    original = host_boundary.SafeEmitter.emit

    async def observe(self, ctx):
        outcome = await original(self, ctx)
        points.append((ctx["interception_point"], ctx.get("tool_call", {}).get("name")))
        return outcome
    monkeypatch.setattr(host_boundary.SafeEmitter, "emit", observe)
    agent = SafeAgent(client=ScriptClient(chain_script(model_ticket=model_ticket)))
    response = await agent.run("help", stream=True).get_final_response() if stream else await agent.run("help")
    assert len(mock_tickets()) == 1
    assert mock_tickets()[0]["category"] == "access"
    assert mock_tickets()[0]["severity"] == "medium"
    assert points.count(("pre_tool_call", "create_escalation_ticket")) == 1
    assert points.count(("post_tool_call", "create_escalation_ticket")) == 1
    assert points.count(("output", None)) == 1
    assert points.count(("agent_startup", None)) == 1
    assert points.count(("input", None)) == 1
    assert points.count(("agent_shutdown", None)) == 1
    assert {point for point, _ in points} == {
        "agent_startup", "input", "pre_model_call", "post_model_call",
        "pre_tool_call", "post_tool_call", "output", "agent_shutdown",
    }
    assert points.index(("post_tool_call", "create_escalation_ticket")) < points.index(("output", None))
    assert "MODEL_DID_NOT_HANDOFF" not in response.text
    assert not evidence._EVIDENCE_REGISTRY
    assert not evidence._DECISION_EVIDENCE_BY_INVOCATION


@pytest.mark.asyncio
@pytest.mark.parametrize("query", [False, True])
@pytest.mark.parametrize("stream", [False, True])
async def test_streaming_output_failure_releases_no_updates_and_discards_state(tmp_path, query, stream):
    agent = SafeAgent(client=ScriptClient(chain_script()), manifest=fault_manifest(tmp_path, "output", query=query))
    updates = []
    with pytest.raises((InterceptionBlocked, HostFailure)):
        if stream:
            async for update in agent.run("help", stream=True):
                updates.append(update)
        else:
            await agent.run("help")
    assert not updates
    assert not evidence._EVIDENCE_REGISTRY
    assert not evidence._DECISION_EVIDENCE_BY_INVOCATION


@pytest.mark.asyncio
async def test_missing_ticket_cannot_leave_host_when_handoff_fails(monkeypatch):
    def fail(**kwargs):
        raise ValueError("SECRET")
    monkeypatch.setattr(host_boundary, "_create_escalation_ticket", fail)
    with pytest.raises(HostFailure, match="tool_execution_failed"):
        await SafeAgent(client=ScriptClient(chain_script())).run("help")
    assert not mock_tickets()
    assert not evidence._EVIDENCE_REGISTRY
    assert not evidence._DECISION_EVIDENCE_BY_INVOCATION


@pytest.mark.asyncio
async def test_stale_host_decision_cannot_authorize_a_handoff(monkeypatch):
    monkeypatch.setattr(host_boundary, "decisions_for_invocation", lambda _: {
        "locked-signin": {"facts": {"local_remediation_available": False},
                          "evidence_reference": "ev:stale-from-another-invocation"},
    })
    calls = []
    monkeypatch.setattr(host_boundary, "_create_escalation_ticket", lambda **_: calls.append(True))
    agent = SafeAgent(client=ScriptClient(lambda *_: Message("assistant", ["candidate"])))
    with pytest.raises(InterceptionBlocked) as error:
        await agent.run("help")
    assert error.value.result.verdict.reason == "unanchored_decision"
    assert not calls


@pytest.mark.asyncio
async def test_handoff_pre_deny_never_calls_callback_or_retries(tmp_path, monkeypatch):
    import yaml
    from host_boundary import POLICY_MANIFEST
    doc = yaml.safe_load(POLICY_MANIFEST.read_text())
    doc["policies"]["helpdesk"]["bundle"] = str(POLICY_MANIFEST.parent)
    # A real policy denies only ticket calls, leaving the diagnostic chain intact.
    (tmp_path / "fault.rego").write_text(
        'package fault\nimport rego.v1\ndefault verdict := {"decision":"allow"}\n'
        'verdict := {"decision":"deny","reason":"ticket_denied"} if { input.tool.name == "create_escalation_ticket" }\n')
    doc["policies"]["fault"] = {"type": "rego", "bundle": str(tmp_path), "query": "data.fault.verdict"}
    doc["intervention_points"]["pre_tool_call"]["policy"] = {"id": "fault"}
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(doc))
    calls = []
    monkeypatch.setattr(host_boundary, "_create_escalation_ticket", lambda **_: calls.append(True))
    with pytest.raises(InterceptionBlocked) as error:
        await SafeAgent(client=ScriptClient(chain_script()), manifest=path).run("help")
    assert error.value.result.verdict.reason == "ticket_denied"
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("same_case", [False, True])
async def test_concurrent_invocations_do_not_cross_contaminate_and_tickets_are_idempotent(same_case):
    ready = asyncio.Barrier(2)
    original = host_boundary.SafeEmitter.emit

    # A public emitter entrypoint observer is a test synchronization seam only.
    async def synchronize(self, ctx):
        if ctx["interception_point"] == "agent_startup":
            await ready.wait()
        return await original(self, ctx)

    from unittest.mock import patch
    agents = [
        SafeAgent(client=ScriptClient(chain_script("locked-signin"))),
        SafeAgent(client=ScriptClient(chain_script("locked-signin" if same_case else "token-expired-signin"))),
    ]
    with patch.object(host_boundary.SafeEmitter, "emit", synchronize):
        responses = await asyncio.gather(*(agent.run("help") for agent in agents))
    assert "human handoff" in responses[0].text
    if not same_case:
        assert responses[1].text == "MODEL_DID_NOT_HANDOFF"
    assert len(mock_tickets()) == 1
    assert not evidence._EVIDENCE_REGISTRY


@pytest.mark.asyncio
async def test_repeated_invocations_same_case_create_no_extra_ticket():
    for _ in range(2):
        await SafeAgent(client=ScriptClient(chain_script())).run("help")
    assert len(mock_tickets()) == 1


@pytest.mark.asyncio
async def test_one_shared_raw_agent_has_distinct_concurrent_authority_scopes(monkeypatch):
    ready = asyncio.Barrier(2)
    scopes = []
    original = host_boundary.SafeEmitter.emit

    async def observe(self, ctx):
        if ctx["interception_point"] == "agent_startup":
            scopes.append(self.invocation_id)
            await ready.wait()
        return await original(self, ctx)
    monkeypatch.setattr(host_boundary.SafeEmitter, "emit", observe)
    def script(messages, count):
        case = next(message.text for message in reversed(messages) if message.role == "user")
        return chain_script(case)(messages, count)
    agent = SafeAgent(client=ScriptClient(script))
    responses = await asyncio.gather(agent.run("locked-signin"), agent.run("token-expired-signin"))
    assert len(set(scopes)) == 2
    assert "human handoff" in responses[0].text
    assert responses[1].text == "MODEL_DID_NOT_HANDOFF"
    assert len(mock_tickets()) == 1
    assert not evidence._EVIDENCE_REGISTRY


@pytest.mark.asyncio
async def test_local_session_also_persists_only_the_repaired_answer():
    observed = []
    def script(messages, count):
        if any(message.role == "user" and message.text == "followup" for message in messages):
            observed.extend(messages)
            return Message("assistant", ["approved"])
        return chain_script()(messages, count)
    agent = SafeAgent(client=ScriptClient(script))
    session = agent.create_session()
    await agent.run("help", session=session)
    await agent.run("followup", session=session)
    text = " ".join(message.text for message in observed)
    assert "MODEL_DID_NOT_HANDOFF" not in text
    assert "human handoff" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_cancellation_cleans_state_and_never_releases_output(stream):
    started = asyncio.Event()
    cancelled = asyncio.Event()
    callbacks = []
    @tool(result_parser=SKIP_PARSING)
    async def get_system_status(case_id: str, service: str):
        callbacks.append(True)
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    client = ScriptClient(lambda *_: Message("assistant", [
        call("get_system_status", {"case_id": "locked-signin", "service": "identity"}, "first"),
        call("get_system_status", {"case_id": "locked-signin", "service": "identity"}, "second"),
    ]))
    agent = SafeAgent(client=client,
                      tools=[get_system_status])
    invocation = agent.run("help", stream=True).get_final_response() if stream else agent.run("help")
    task = asyncio.create_task(invocation)
    await asyncio.wait_for(started.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(cancelled.wait(), 3)
    assert callbacks == [True]
    assert not evidence._EVIDENCE_REGISTRY
    assert not evidence._DECISION_EVIDENCE_BY_INVOCATION
