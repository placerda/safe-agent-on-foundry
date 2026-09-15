"""Real ASGI Responses serialization/history; no socket or Azure dependency."""
import json

import httpx
import pytest
from agent_framework import Message, SessionStore
from agent_framework_foundry_hosting import ResponsesHostServer, StoreProvider
from azure.ai.agentserver.responses import InMemoryResponseProvider
from evaluation.assert_suite.target import parse_responses_payload

from host_boundary import SafeAgent
from native_support import ScriptClient, chain_script, fault_manifest


class MemorySessions(StoreProvider):
    def __init__(self):
        self.store = SessionStore()

    def get_store(self, *, config, platform_context):
        return self.store


def host(agent):
    return ResponsesHostServer(
        agent, store=InMemoryResponseProvider(),
        agent_session_store_provider=MemorySessions(),
        configure_observability=None,
    )


@pytest.mark.asyncio
async def test_assert_build_agent_async_context_contract(monkeypatch):
    from types import SimpleNamespace
    import main
    monkeypatch.setattr(main, "get_agent_config", lambda: SimpleNamespace(
        project_endpoint="https://fixture.invalid/projects/test",
        model_deployment_name="fixture",
    ))
    monkeypatch.setattr(main, "DefaultAzureCredential", lambda: object())
    monkeypatch.setattr(main, "FoundryChatClient", lambda **_: ScriptClient(chain_script()))
    async with main.build_agent() as agent:
        result = await agent.run("help")
    assert "human handoff" in result.text


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_real_responses_handoff_serializes_only_approved_text(tmp_path, monkeypatch, stream):
    monkeypatch.setenv("AGENTSERVER_STATE_ROOT", str(tmp_path))
    server = host(SafeAgent(client=ScriptClient(chain_script())))
    async with server.router.lifespan_context(server):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://local") as client:
            response = await client.post("/responses", json={"input": "help", "stream": stream})
            assert response.status_code == 200, response.text
            assert "human handoff" in response.text
            assert "MODEL_DID_NOT_HANDOFF" not in response.text
            if stream:
                assert "response.completed" in response.text
                assert "text/event-stream" in response.headers["content-type"]
                payload = next(
                    event["response"]
                    for line in response.text.splitlines() if line.startswith("data: {")
                    if (event := json.loads(line[6:]))["type"] == "response.completed"
                )
            else:
                assert response.json()["status"] == "completed"
                payload = response.json()
            trajectory, text = parse_responses_payload(payload)
            assert [step["name"] for step in trajectory] == [
                "get_system_status", "get_user_account", "search_kb", "create_escalation_ticket",
            ]
            assert all(isinstance(step["output"], dict) for step in trajectory)
            assert trajectory[-1]["output"]["ticket_id"] in text


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["pre_tool_call", "post_tool_call", "output"])
@pytest.mark.parametrize("stream", [False, True])
async def test_real_responses_failures_never_serialize_assistant_output(tmp_path, monkeypatch, point, stream):
    monkeypatch.setenv("AGENTSERVER_STATE_ROOT", str(tmp_path))
    server = host(SafeAgent(client=ScriptClient(chain_script()),
                            manifest=fault_manifest(tmp_path, point, query=point == "pre_tool_call")))
    async with server.router.lifespan_context(server):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://local") as client:
            response = await client.post("/responses", json={"input": "help", "stream": stream})
            assert response.status_code == 200, response.text
            if stream:
                assert "response.failed" in response.text
                assert "response.output_text.delta" not in response.text
                assert "response.output_item.added" not in response.text
            else:
                assert response.json()["status"] == "failed"
                assert response.json().get("output", []) == []
            assert "MODEL_DID_NOT_HANDOFF" not in response.text
            assert "human handoff" not in response.text


@pytest.mark.asyncio
async def test_default_host_history_survives_real_multi_turn_without_duplication(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTSERVER_STATE_ROOT", str(tmp_path))
    observed = []
    def script(messages, count):
        observed.append([m.text for m in messages if m.role in {"user", "assistant"}])
        return Message("assistant", ["first answer" if count == 1 else "second answer"])
    server = host(SafeAgent(client=ScriptClient(script)))
    async with server.router.lifespan_context(server):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://local") as client:
            first = await client.post("/responses", json={"input": "remember blue", "store": True})
            assert first.status_code == 200, first.text
            assert first.json()["status"] == "completed", first.text
            second = await client.post("/responses", json={
                "input": "what color?", "previous_response_id": first.json()["id"], "store": True,
            })
            assert second.status_code == 200, second.text
            assert second.json()["status"] == "completed", second.text
    assert observed[0] == ["remember blue"]
    assert observed[1] == ["remember blue", "first answer", "what color?"]


@pytest.mark.asyncio
async def test_host_history_persists_repaired_answer_not_stale_model_text(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTSERVER_STATE_ROOT", str(tmp_path))
    followup_messages = []
    def script(messages, count):
        if messages[-1].role == "user" and messages[-1].text == "followup":
            followup_messages.extend(messages)
            return Message("assistant", ["followup approved"])
        return chain_script()(messages, count)
    server = host(SafeAgent(client=ScriptClient(script)))
    async with server.router.lifespan_context(server):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://local") as client:
            first = await client.post("/responses", json={"input": "help", "store": True})
            assert first.json()["status"] == "completed", first.text
            second = await client.post("/responses", json={
                "input": "followup", "previous_response_id": first.json()["id"], "store": True,
            })
            assert second.json()["status"] == "completed", second.text
    text = " ".join(message.text for message in followup_messages)
    assert "human handoff" in text
    assert "MODEL_DID_NOT_HANDOFF" not in text
    calls = [content for message in followup_messages for content in message.contents
             if content.type == "function_call"]
    results = [content for message in followup_messages for content in message.contents
               if content.type == "function_result"]
    assert [content.name for content in calls] == [
        "get_system_status", "get_user_account", "search_kb", "create_escalation_ticket",
    ]
    assert len({content.call_id for content in calls}) == 4
    assert [content.call_id for content in calls] == [content.call_id for content in results]
    assert parse_responses_payload(second.json())[0] == []
