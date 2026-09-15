import asyncio
import json

import pytest
from agent_framework import Message, tool, SKIP_PARSING
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import evidence
import host_boundary
from host_boundary import HostFailure, SafeAgent, reason_code
from native_support import ScriptClient, chain_script, call, fault_manifest, one_call, results
from tools import TOOLS


@pytest.fixture
def recorded_spans(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(host_boundary, "TRACER", provider.get_tracer("test"))
    return exporter


@pytest.mark.asyncio
async def test_verified_claims_are_forwarded_to_acs_snapshot():
    client = ScriptClient(chain_script())
    await SafeAgent(client=client).run("help")
    assert len(results(client.requests[-1])) == 3
    assert not evidence._EVIDENCE_REGISTRY


@pytest.mark.asyncio
async def test_deny_never_executes_tool_and_becomes_structured_result():
    executed = []

    @tool
    def get_system_status(case_id: str, service: str):
        executed.append(True)
        return {}

    client = one_call("get_system_status", {"case_id": "forbidden", "service": "identity"})
    await SafeAgent(client=client, tools=[get_system_status]).run("fixture")
    assert not executed
    assert "scope_boundary" in str(results(client.requests[-1]))


@pytest.mark.asyncio
@pytest.mark.parametrize("point,query", [("pre_tool_call", True), ("post_tool_call", False)])
@pytest.mark.parametrize("stream", [False, True])
async def test_native_fatal_failure_aborts_batches_before_second_callback(tmp_path, point, query, stream):
    callbacks = []

    @tool(result_parser=SKIP_PARSING)
    def get_system_status(case_id: str, service: str):
        callbacks.append(True)
        return {"service": service, "state": "operational"}

    client = ScriptClient(lambda _, n: Message("assistant", [
        call("get_system_status", {"case_id": "locked-signin", "service": "identity"}, "one"),
        call("get_system_status", {"case_id": "locked-signin", "service": "identity"}, "two"),
    ]))
    agent = SafeAgent(client=client, tools=[get_system_status], manifest=fault_manifest(tmp_path, point, query=query))
    updates = []
    with pytest.raises(HostFailure) as error:
        if stream:
            async for update in agent.run("help", stream=True):
                updates.append(update)
        else:
            await agent.run("help")
    assert len(callbacks) == (0 if query else 1)
    assert len(client.requests) == 1
    assert not updates
    assert error.value.record.verdict.reason == ("runtime_error:policy_output_invalid" if query else "fixture_rejected")
    assert not evidence._EVIDENCE_REGISTRY


@pytest.mark.asyncio
@pytest.mark.parametrize("raises", [False, True])
async def test_incomplete_diagnostic_result_and_raw_exception_fail_closed(raises, recorded_spans):
    @tool(result_parser=SKIP_PARSING)
    def get_system_status(case_id: str, service: str):
        if raises:
            raise ValueError("SECRET_ERROR_MESSAGE")
        return {"service": service}

    client = one_call("get_system_status", {"case_id": "locked-signin", "service": "identity"})
    with pytest.raises(HostFailure) as error:
        await SafeAgent(client=client, tools=[get_system_status]).run("help")
    assert "SECRET_ERROR_MESSAGE" not in str(error.value)
    assert len(client.requests) == 1
    for span in recorded_spans.get_finished_spans():
        assert not span.events
        assert "SECRET_ERROR_MESSAGE" not in str(span.attributes)


@pytest.mark.asyncio
async def test_allow_span_reports_real_verdict_without_sensitive_evidence(recorded_spans):
    await SafeAgent(client=ScriptClient(chain_script())).run("help")
    spans = recorded_spans.get_finished_spans()
    assert any(s.attributes.get("acs.verdict") == "allow" for s in spans)
    assert any(s.attributes.get("safe.evidence.valid") is True for s in spans)
    for span in spans:
        assert not span.events
        assert not any(word in str(span.attributes) for word in ("ev:", "facts", "locked-signin", "unit-test-secret"))


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["pre_tool_call", "post_tool_call"])
async def test_deny_span_records_original_reason_before_propagation_without_exception_event(tmp_path, recorded_spans, point):
    agent = SafeAgent(
        client=one_call("get_system_status", {"case_id": "locked-signin", "service": "identity"}),
        manifest=fault_manifest(tmp_path, point),
    )
    if point == "post_tool_call":
        with pytest.raises(HostFailure):
            await agent.run("help")
    else:
        await agent.run("help")
    denied = [s for s in recorded_spans.get_finished_spans() if s.attributes.get("acs.verdict") == "deny"]
    assert denied and denied[0].attributes["acs.reason"] == "fixture_rejected"
    assert denied[0].status.is_ok is False
    assert not denied[0].events


def test_evidence_reason_is_reduced_to_a_bounded_code():
    assert reason_code("runtime_error:policy_output_invalid") == "runtime_error:policy_output_invalid"
    assert reason_code("contains user content!") == "policy_failure"
    assert reason_code("a" * 1000) == "policy_failure"


@pytest.mark.asyncio
async def test_default_arguments_are_applied_before_policy_and_callback():
    calls = []

    @tool(result_parser=SKIP_PARSING)
    def get_system_status(case_id: str, service: str = "identity"):
        calls.append(service)
        return {"service": service, "state": "operational"}

    await SafeAgent(client=one_call("get_system_status", {"case_id": "locked-signin"}),
                    tools=[get_system_status]).run("help")
    assert calls == ["identity"]


@pytest.mark.asyncio
async def test_call_scoped_pending_evidence_is_not_authority_before_acceptance(monkeypatch, tmp_path):
    pending = []
    original = host_boundary.prepare_result_evidence

    def prepare(*args, **kwargs):
        value, token = original(*args, **kwargs)
        snapshot = evidence.evidence_snapshot_for_call("get_user_account", {
            "case_id": "locked-signin", "service_evidence_reference": value["evidence_reference"],
        })
        assert snapshot["valid"] is False
        pending.append(value["evidence_reference"])
        return value, token
    monkeypatch.setattr(host_boundary, "prepare_result_evidence", prepare)
    with pytest.raises(HostFailure):
        await SafeAgent(client=one_call("get_system_status", {"case_id": "locked-signin", "service": "identity"}),
                        manifest=fault_manifest(tmp_path, "post_tool_call")).run("help")
    assert len(pending) == 1
    assert not evidence._EVIDENCE_REGISTRY
    followup = one_call("get_user_account", {"case_id": "locked-signin", "service_evidence_reference": pending[0]})
    await SafeAgent(client=followup).run("help")
    assert "flow_integrity_violation" in str(results(followup.requests[-1]))


@pytest.mark.asyncio
async def test_model_runtime_error_is_redacted_and_not_a_success():
    def fail(*_):
        raise ValueError("SECRET_MODEL_PAYLOAD")
    with pytest.raises(HostFailure, match="agent_execution_failed") as error:
        await SafeAgent(client=ScriptClient(fail)).run("help")
    assert "SECRET_MODEL_PAYLOAD" not in str(error.value)


def test_entrypoint_disables_sensitive_content_telemetry(monkeypatch):
    import os
    import main
    calls = []
    class Server:
        def __init__(self, agent):
            assert os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] == "false"
        def run(self):
            calls.append(True)
    monkeypatch.setenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "true")
    monkeypatch.setattr(main, "build_agent", lambda: object())
    monkeypatch.setattr(main, "ResponsesHostServer", Server)
    main.main()
    assert calls == [True]


def test_deployment_defaults_to_content_free_policy_telemetry():
    from pathlib import Path
    import yaml
    document = yaml.safe_load((Path(__file__).resolve().parents[1] / "azure.yaml").read_text())
    variables = document["services"]["helpdeskbot"]["environmentVariables"]
    content_capture = next(item for item in variables if item["name"] == "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT")
    assert content_capture["value"] == "${OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT:=false}"
