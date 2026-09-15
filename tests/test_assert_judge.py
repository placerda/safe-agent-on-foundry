"""Optional ASSERT integration checks; run in the pinned ASSERT environment."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation.assert_suite.rejudge import contract_from_config, provider_audit, replay


CONFIG = Path(__file__).resolve().parents[1] / "evaluation" / "assert_suite" / "eval_config.yaml"


@pytest.fixture(autouse=True)
def evidence_secret():
    """Judge-only tests do not import or execute the native hosted runtime."""


@pytest.fixture
def engine():
    return pytest.importorskip("assert_ai.core.judge")


def test_actual_assert_parser_applies_judge_settings_and_taxonomy(engine):
    judge, _, contract = contract_from_config(CONFIG)
    assert judge.model.reasoning_effort == "high"
    assert judge.model.max_tokens == 32768
    assert judge.n == 1
    assert "Evidence precedence" in contract["system_prompt"]
    assert "Compare each successful" in contract["system_prompt"]
    assert set(contract["score_keys"]) >= {
        "scope_violation", "anchoring_violation",
        "flow_integrity_violation", "escalation_violation",
    }


@pytest.mark.parametrize("invalid_dimension", [False, True])
def test_actual_assert_request_preserves_settings_and_raw_booleans(engine, monkeypatch, invalid_dimension):
    from assert_ai.core.transcript import Transcript, TranscriptEvent, TranscriptMetadata

    judge, taxonomy, contract = contract_from_config(CONFIG)
    transcript = Transcript(
        metadata=TranscriptMetadata(kind="synthetic", test_case_id="offline", behavior="", target="", tester_model=""),
        events=[TranscriptEvent.model_validate({
            "view": ["target", "combined"], "actor": "target",
            "edit": {"type": "add_message", "message": {"role": "assistant", "content": "No ticket."}},
        })],
    )
    xml, mapping = transcript.format_transcript_xml("target", skip_system=False)
    dimensions = {key: key == "anchoring_violation" for key in contract["score_keys"]}
    if invalid_dimension:
        dimensions["anchoring_violation"] = "true"
    raw = json.dumps({
        "dimensions": dimensions,
        "dimension_justifications": {key: "Fixture evidence [1]." for key in dimensions},
        "node_judgments": [],
        "highlights": '1. <cite id="1" description="Response">No ticket.</cite>',
        "narrative": "Synthetic offline normalization fixture.",
    })

    async def generate(model, messages, *, options, **kwargs):
        assert model == "azure/gpt-5.4-mini"
        assert options.reasoning_effort == "high"
        assert options.max_tokens == 32768
        assert options.temperature is None
        assert messages[0].content == contract["system_prompt"]
        assert messages[1].content == f"# Transcript\n{xml}"
        return SimpleNamespace(text=raw, parsed=json.loads(raw))

    monkeypatch.setattr(engine, "generate_structured", generate)
    result = asyncio.run(engine.run_transcript_judge(
        judge_model=judge.model.name, system_prompt=contract["system_prompt"],
        user_message=f"# Transcript\n{xml}", transcript=transcript,
        index_to_message_id=mapping, policy_raw=taxonomy,
        judge_max_tokens=judge.model.max_tokens, reasoning_effort=judge.model.reasoning_effort,
        **{key: contract[key] for key in
           ("score_keys", "response_schema", "not_applicable_score_keys", "dimension_scales")},
    ))
    if invalid_dimension:
        assert result["judge_status"] == "judge_failed"
        assert result["judge_error"] == "invalid_dimension:anchoring_violation"
        assert "dimensions" not in result["verdict"]
        assert result["parseable_raws"] == [raw]
    else:
        assert result["judge_status"] == "ok"
        assert result["raw"] == raw
        assert result["verdict"]["dimensions"] == dimensions


def test_replay_does_not_overwrite_existing_run(engine, tmp_path):
    source = tmp_path / "input.jsonl"
    source.write_text(json.dumps({"events": [{"irrelevant": True}]}) + "\n")
    with pytest.raises(FileExistsError):
        asyncio.run(replay(CONFIG, source, tmp_path, 0))


def test_replay_rejects_missing_events_before_creating_artifacts(engine, tmp_path):
    source = tmp_path / "input.jsonl"
    source.write_text('{"test_case_id": "missing"}\n')
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="captured events"):
        asyncio.run(replay(CONFIG, source, output, 0))
    assert not output.exists()


def test_provider_audit_retains_finish_reason_but_not_credentials(engine, tmp_path):
    import litellm

    previous = list(litellm.callbacks)
    with provider_audit(tmp_path):
        callback = litellm.callbacks[-1]
        response = SimpleNamespace(model_dump=lambda: {
            "model": "test-model", "usage": {"completion_tokens": 32768},
            "choices": [{"finish_reason": "length", "message": {
                "content": "", "reasoning_content": "not-for-storage"}}],
            "api_key": "never-store-this",
        })
        asyncio.run(callback.async_log_success_event(
            {"api_key": "never-store-this"}, response, None, None))
    assert litellm.callbacks == previous
    text = (tmp_path / "provider_responses.jsonl").read_text()
    record = json.loads(text)
    assert record["choices"] == [{"finish_reason": "length", "content": ""}]
    assert "never-store-this" not in text
    assert "not-for-storage" not in text


def test_heldout_generation_is_synthetic_and_does_not_modify_captures():
    from evaluation.assert_suite.calibration_inputs import held_out

    def message(role):
        return {"view": ["target"], "actor": "target", "raw": {"private": "discard"},
                "edit": {"type": "add_message", "message": {"role": role, "content": "fixture"}}}

    names = ["get_system_status", "get_user_account", "search_kb", "create_escalation_ticket"]
    tools = [{"view": ["target"], "actor": "tool", "edit": {
        "type": "tool_call", "tool_name": name,
        "tool_args": {"service_evidence_reference": "token:status",
                      "account_evidence_reference": "token:account"},
        "tool_result": '{"evidence_reference":"token:status","category":"access"}',
    }} for name in names]
    rows = [{"events": [message("user"), *tools, message("assistant")]} for _ in range(4)]
    original = json.dumps(rows)
    controls, expected = held_out(rows)
    assert json.dumps(rows) == original
    assert len(controls) == 8 and len(expected) == 8
    assert all(row["type"] == "synthetic-heldout" for row in controls)
    assert all(event["raw"] is None for row in controls for event in row["events"])
    assert all(expected[row["test_case_id"]] == [] for row in controls[:4])
    assert "token:status" not in json.dumps(controls)
    assert "expectations" not in json.dumps(controls)
    assert [len(value) for value in expected.values()] == [0, 0, 0, 0, 1, 1, 1, 1]
    reordered = [event["edit"].get("tool_name") for event in controls[6]["events"]]
    assert reordered.index("create_escalation_ticket") < reordered.index("search_kb")
    assert all(event["edit"].get("tool_name") != "create_escalation_ticket"
               for event in controls[7]["events"])


@pytest.mark.parametrize("transport_error", [False, True])
def test_replay_fails_loudly_and_never_fabricates_a_verdict(engine, monkeypatch, tmp_path, transport_error):
    source = tmp_path / "input.jsonl"
    source.write_text(json.dumps({
        "test_case_id": "synthetic-offline",
        "events": [{"view": ["target"], "actor": "target", "edit": {
            "type": "add_message", "message": {"role": "assistant", "content": "No ticket."}}}],
    }) + "\n")
    output = tmp_path / "run"
    failed_result = {"judge_status": "judge_failed", "verdict": {"error": "judge_failed"}, "raw": ""}

    async def fail(**kwargs):
        if transport_error:
            raise ConnectionError("Unavailable test provider")
        return failed_result

    monkeypatch.setattr(engine, "run_transcript_judge", fail)
    with pytest.raises((RuntimeError, ConnectionError)):
        asyncio.run(replay(CONFIG, source, output, 0))
    if not transport_error:
        saved = json.loads((output / "scores.jsonl").read_text())
        assert saved["verdict"] == failed_result["verdict"]
        assert saved["judge_status"] == "judge_failed"
    assert (output / "inference_set.jsonl").read_bytes() == source.read_bytes()
