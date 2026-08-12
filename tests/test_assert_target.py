"""Offline tests for the ASSERT callable target.

Every test mocks the HTTP call and the Entra token, so the suite never reaches
Azure and never costs an inference.
"""

import json
from types import SimpleNamespace

import httpx
import pytest

from evaluation.assert_suite import target
from evaluation.assert_suite.target import (
    FoundryTargetError,
    _safe_value,
    build_responses_input,
    normalize_agent_endpoint,
    parse_responses_payload,
)


def _message_item(text: str) -> dict:
    return {"type": "message", "content": [{"type": "output_text", "text": text}]}


def _call_items(call_id: str, name: str, arguments: dict, output: dict) -> list[dict]:
    return [
        {
            "type": "function_call",
            "call_id": call_id,
            "name": name,
            "arguments": json.dumps(arguments),
        },
        {
            "type": "function_call_output",
            "call_id": call_id,
            "output": json.dumps(output),
        },
    ]


def _completed_payload(output: list[dict] | None = None, **extra) -> dict:
    return {"status": "completed", "output": output or [], **extra}


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("POST", "https://agent.example/responses"),
                response=httpx.Response(self.status_code),
            )

    def json(self) -> dict:
        return self._payload


@pytest.fixture
def hosted_mode(monkeypatch):
    """Configure hosted mode with a stub token and capture the outgoing call."""
    monkeypatch.setenv("ASSERT_TARGET_MODE", "hosted")
    monkeypatch.setenv("FOUNDRY_AGENT_ENDPOINT", "https://agent.example/responses")
    monkeypatch.setattr(target, "_access_token", lambda: "stub-token")
    monkeypatch.setattr(target, "_emit_trajectory", lambda *args: None)
    captured: dict = {}

    def _post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return captured["response"]

    monkeypatch.setattr(target.httpx, "post", _post)
    return captured


def test_safe_value_hashes_nested_evidence_references() -> None:
    value = {
        "evidence_reference": "secret",
        "nested": [
            {"account_evidence_reference": "secret"},
            {"token_state": "expired"},
        ],
    }

    sanitized = _safe_value(value)

    assert sanitized["evidence_reference"].startswith("token:")
    assert (
        sanitized["nested"][0]["account_evidence_reference"]
        == sanitized["evidence_reference"]
    )
    assert sanitized["nested"][1] == {"token_state": "expired"}
    assert "secret" not in json.dumps(sanitized)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://agent.example/responses", "https://agent.example/responses"),
        ("https://agent.example/responses/", "https://agent.example/responses"),
        (
            "  https://agent.example/agents/helpdeskbot  ",
            "https://agent.example/agents/helpdeskbot/responses",
        ),
        ("https://agent.example", "https://agent.example/responses"),
        (
            "https://agent.example/responses?api-version=v1",
            "https://agent.example/responses",
        ),
    ],
)
def test_normalize_agent_endpoint_canonicalizes_supported_forms(raw, expected) -> None:
    assert normalize_agent_endpoint(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "agent.example/responses", "ftp://agent.example"])
def test_normalize_agent_endpoint_rejects_unusable_values(raw) -> None:
    with pytest.raises(FoundryTargetError):
        normalize_agent_endpoint(raw)


def test_chat_hosted_fails_when_endpoint_is_missing(monkeypatch) -> None:
    monkeypatch.setenv("ASSERT_TARGET_MODE", "hosted")
    monkeypatch.delenv("FOUNDRY_AGENT_ENDPOINT", raising=False)

    with pytest.raises(FoundryTargetError, match="FOUNDRY_AGENT_ENDPOINT"):
        target.chat("DEMO_CASE: locked-signin")


def test_build_responses_input_sends_bare_message_without_history() -> None:
    assert build_responses_input("first turn", None) == "first turn"
    assert build_responses_input("first turn", []) == "first turn"


def test_build_responses_input_keeps_current_turn_once() -> None:
    history = [
        {"role": "user", "content": "first turn"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "second turn"},
    ]

    payload = build_responses_input("second turn", history)

    assert payload == history
    assert payload is not history


def test_build_responses_input_appends_missing_current_turn() -> None:
    history = [{"role": "user", "content": "first turn"}]

    payload = build_responses_input("second turn", history)

    assert payload[-1] == {"role": "user", "content": "second turn"}
    assert history == [{"role": "user", "content": "first turn"}]


def test_parse_responses_payload_reconstructs_trajectory_and_answer() -> None:
    payload = _completed_payload(
        [
            *_call_items(
                "call-1",
                "get_system_status",
                {"case_id": "token-expired-signin"},
                {"status": "operational", "evidence_reference": "raw-token"},
            ),
            _message_item("Use the local fix."),
        ]
    )

    trajectory, final_text = parse_responses_payload(payload)

    assert final_text == "Use the local fix."
    assert trajectory[0]["name"] == "get_system_status"
    assert trajectory[0]["arguments"] == {"case_id": "token-expired-signin"}
    assert trajectory[0]["output"]["status"] == "operational"
    assert trajectory[0]["output"]["evidence_reference"].startswith("token:")
    assert "raw-token" not in json.dumps(trajectory)


def test_parse_responses_payload_returns_the_last_assistant_message() -> None:
    payload = _completed_payload(
        [
            _message_item('{"route": "diagnose"}'),
            _message_item("Final answer."),
        ]
    )

    _, final_text = parse_responses_payload(payload)

    assert final_text == "Final answer."


def test_parse_responses_payload_falls_back_to_output_text() -> None:
    _, final_text = parse_responses_payload(
        _completed_payload(output_text="Short answer.")
    )

    assert final_text == "Short answer."


def test_parse_responses_payload_keeps_non_json_tool_output() -> None:
    payload = _completed_payload(
        [
            {
                "type": "function_call",
                "call_id": "c1",
                "name": "search_kb",
                "arguments": "not-json",
            },
            {"type": "function_call_output", "call_id": "c1", "output": "blocked_by_acs"},
            _message_item("Reporting the policy block."),
        ]
    )

    trajectory, _ = parse_responses_payload(payload)

    assert trajectory[0]["arguments"] == "not-json"
    assert trajectory[0]["output"] == "blocked_by_acs"


@pytest.mark.parametrize(
    "payload",
    [
        _completed_payload(),
        _completed_payload([_message_item("")]),
        _completed_payload([{"type": "reasoning"}], output_text=""),
    ],
)
def test_parse_responses_payload_fails_without_assistant_output(payload) -> None:
    with pytest.raises(FoundryTargetError, match="no assistant output text"):
        parse_responses_payload(payload)


def test_parse_responses_payload_rejects_non_object_payloads() -> None:
    with pytest.raises(FoundryTargetError, match="expected a JSON object"):
        parse_responses_payload(["not", "an", "object"])


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {
                "status": "failed",
                "error": {"code": "agent_error", "message": "Tool execution failed."},
                "output": [_message_item("Partial answer.")],
            },
            "agent_error",
        ),
        (
            {
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [_message_item("Truncated answer.")],
            },
            "max_output_tokens",
        ),
        ({"output": [_message_item("Missing status.")]}, "status=None"),
    ],
)
def test_parse_responses_payload_rejects_non_completed_runs(payload, expected) -> None:
    with pytest.raises(FoundryTargetError, match=expected):
        parse_responses_payload(payload)


def test_parse_responses_payload_fails_on_orphan_tool_output() -> None:
    payload = _completed_payload(
        [
            {"type": "function_call_output", "call_id": "missing", "output": "{}"},
            _message_item("Answer."),
        ]
    )

    with pytest.raises(FoundryTargetError, match="omitted the function call"):
        parse_responses_payload(payload)


def test_parse_responses_payload_fails_on_missing_tool_output() -> None:
    payload = _completed_payload(
        [
            {
                "type": "function_call",
                "call_id": "unfinished",
                "name": "create_escalation_ticket",
                "arguments": "{}",
            },
            _message_item("Partial answer."),
        ]
    )

    with pytest.raises(FoundryTargetError, match="omitted function results.*unfinished"):
        parse_responses_payload(payload)


@pytest.mark.parametrize(
    "item",
    [
        {"type": "function_call", "name": "search_kb", "arguments": "{}"},
        {"type": "function_call_output", "output": "{}"},
    ],
)
def test_parse_responses_payload_rejects_missing_call_ids(item) -> None:
    with pytest.raises(FoundryTargetError, match="no valid call_id"):
        parse_responses_payload(_completed_payload([item, _message_item("Answer.")]))


def test_chat_hosted_sends_authenticated_responses_request(hosted_mode) -> None:
    hosted_mode["response"] = _FakeResponse(
        _completed_payload([_message_item("Answer.")])
    )

    assert target.chat("DEMO_CASE: locked-signin") == "Answer."
    assert hosted_mode["url"] == "https://agent.example/responses"
    assert hosted_mode["params"] == {"api-version": "v1"}
    assert hosted_mode["headers"]["Authorization"] == "Bearer stub-token"
    assert hosted_mode["json"] == {"input": "DEMO_CASE: locked-signin", "stream": False}


def test_chat_hosted_normalizes_endpoint_before_posting(hosted_mode, monkeypatch) -> None:
    monkeypatch.setenv("FOUNDRY_AGENT_ENDPOINT", "https://agent.example/agents/helpdeskbot/")
    hosted_mode["response"] = _FakeResponse(
        _completed_payload([_message_item("Answer.")])
    )

    target.chat("DEMO_CASE: locked-signin")

    assert hosted_mode["url"] == "https://agent.example/agents/helpdeskbot/responses"


def test_chat_hosted_forwards_conversation_history(hosted_mode) -> None:
    hosted_mode["response"] = _FakeResponse(
        _completed_payload([_message_item("Answer.")])
    )
    history = [
        {"role": "user", "content": "first turn"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "second turn"},
    ]

    target.chat("second turn", history)

    assert hosted_mode["json"]["input"] == history


def test_chat_hosted_propagates_http_errors(hosted_mode) -> None:
    hosted_mode["response"] = _FakeResponse({}, status_code=500)

    with pytest.raises(httpx.HTTPStatusError):
        target.chat("DEMO_CASE: locked-signin")


def test_chat_hosted_fails_on_empty_output(hosted_mode) -> None:
    hosted_mode["response"] = _FakeResponse(_completed_payload())

    with pytest.raises(FoundryTargetError, match="no assistant output text"):
        target.chat("DEMO_CASE: locked-signin")


def test_chat_hosted_emits_trajectory_spans(monkeypatch, hosted_mode) -> None:
    emitted: dict = {}
    monkeypatch.setattr(
        target,
        "_emit_trajectory",
        lambda trajectory, final_text: emitted.update(
            trajectory=trajectory, final_text=final_text
        ),
    )
    hosted_mode["response"] = _FakeResponse(
        _completed_payload(
            [
                *_call_items("call-1", "search_kb", {"case_id": "locked-signin"}, {"hit": True}),
                _message_item("Handing off."),
            ]
        )
    )

    target.chat("DEMO_CASE: locked-signin")

    assert emitted["final_text"] == "Handing off."
    assert [step["name"] for step in emitted["trajectory"]] == ["search_kb"]


def test_access_token_reads_the_azure_ai_scope(monkeypatch) -> None:
    requested: list[str] = []
    monkeypatch.setattr(target, "_credential_instance", None)
    monkeypatch.setattr(
        target,
        "DefaultAzureCredential",
        lambda: SimpleNamespace(
            get_token=lambda scope: (
                requested.append(scope) or SimpleNamespace(token="stub-token")
            )
        ),
    )

    assert target._access_token() == "stub-token"
    assert requested == ["https://ai.azure.com/.default"]


def test_chat_defaults_to_local_mode(monkeypatch) -> None:
    monkeypatch.delenv("ASSERT_TARGET_MODE", raising=False)
    captured = {}

    async def _local(message, history):
        captured["message"] = message
        captured["history"] = history
        return "local answer"

    monkeypatch.setattr(target, "_chat_in_process", _local)
    history = [{"role": "user", "content": "Earlier turn"}]

    assert target.chat("DEMO_CASE: locked-signin", history) == "local answer"
    assert captured == {
        "message": "DEMO_CASE: locked-signin",
        "history": history,
    }


def test_chat_rejects_unknown_target_mode(monkeypatch) -> None:
    monkeypatch.setenv("ASSERT_TARGET_MODE", "hostedd")

    with pytest.raises(FoundryTargetError, match="local.*hosted"):
        target.chat("DEMO_CASE: locked-signin")
