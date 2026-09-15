import pytest

from host_boundary import SafeAgent
from native_support import ScriptClient, chain_script, one_call, results
from tools import mock_tickets


@pytest.mark.asyncio
async def test_token_expired_case_preserves_flow_and_stops_on_local_remediation():
    client = ScriptClient(chain_script("token-expired-signin"))
    await SafeAgent(client=client).run("help")
    assert len(client.requests) == 4
    assert len(results(client.requests[-1])) == 3
    assert not mock_tickets()


@pytest.mark.asyncio
async def test_locked_case_creates_exactly_one_anchored_handoff():
    client = ScriptClient(chain_script(model_ticket=True))
    response = await SafeAgent(client=client).run("help")
    assert "completed" in response.text
    assert len(mock_tickets()) == 1
    assert len(client.requests) == 5


@pytest.mark.asyncio
async def test_real_anchored_local_remediation_denies_model_ticket():
    client = ScriptClient(chain_script("token-expired-signin", model_ticket=True))
    await SafeAgent(client=client).run("help")
    assert "local_remediation_available" in str(results(client.requests[-1])[-1])
    assert not mock_tickets()


@pytest.mark.asyncio
@pytest.mark.parametrize("name,args,reason", [
    ("get_system_status", {"case_id": "outside", "service": "identity"}, "scope_boundary"),
    ("search_kb", {"case_id": "locked-signin", "query": "q", "account_evidence_reference": "fake"}, "flow_integrity_violation"),
    ("create_escalation_ticket", {"case_id": "locked-signin", "category": "access", "severity": "medium",
                                  "decision_evidence_reference": "fake"}, "unanchored_decision"),
])
async def test_scope_and_flow_fail_before_tool_execution(name, args, reason):
    client = one_call(name, args)
    await SafeAgent(client=client).run("help")
    assert reason in str(results(client.requests[-1]))
    assert not mock_tickets()
