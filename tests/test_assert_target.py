import json
from types import SimpleNamespace

from evaluation.assert_suite import target
from evaluation.assert_suite.target import _parse_hosted_response, _safe_value


def _event(name: str, payload: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(payload)}"


def test_safe_value_hashes_nested_evidence_tokens() -> None:
    value = {
        "evidence_token": "secret",
        "nested": [
            {"account_evidence_token": "secret"},
            {"token_state": "expired"},
        ],
    }

    sanitized = _safe_value(value)

    assert sanitized["evidence_token"].startswith("token:")
    assert (
        sanitized["nested"][0]["account_evidence_token"]
        == sanitized["evidence_token"]
    )
    assert sanitized["nested"][1] == {"token_state": "expired"}
    assert "secret" not in json.dumps(sanitized)


def test_parse_hosted_response_reconstructs_trajectory_and_final_message() -> None:
    raw = "\n\n".join(
        [
            _event(
                "response.output_item.done",
                {
                    "item": {
                        "type": "function_call",
                        "call_id": "call-1",
                        "name": "check_identity_status",
                        "arguments": '{"case_id":"token-expired-signin"}',
                    }
                },
            ),
            _event(
                "response.output_item.done",
                {
                    "item": {
                        "type": "function_call_output",
                        "call_id": "call-1",
                        "output": '{"status":"operational","evidence_token":"raw-token"}',
                    }
                },
            ),
            _event(
                "response.output_item.done",
                {
                    "item": {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Use the local fix."}],
                    }
                },
            ),
        ]
    )

    trajectory, final_text = _parse_hosted_response(raw)

    assert final_text == "Use the local fix."
    assert trajectory == [
        {
            "name": "check_identity_status",
            "arguments": {"case_id": "token-expired-signin"},
            "output": {
                "status": "operational",
                "evidence_token": trajectory[0]["output"]["evidence_token"],
            },
        }
    ]
    assert trajectory[0]["output"]["evidence_token"].startswith("token:")


def test_parse_hosted_response_marks_missing_final_message() -> None:
    trajectory, final_text = _parse_hosted_response(
        _event("response.completed", {"response": {"output": []}})
    )

    assert trajectory == []
    assert final_text.startswith("[NO_FINAL_RESPONSE]")


def test_chat_hosted_retries_empty_transport_failure(monkeypatch) -> None:
    attempts = iter(
        [
            SimpleNamespace(returncode=1, stdout="", stderr=""),
            SimpleNamespace(
                returncode=0,
                stdout=_event(
                    "response.output_item.done",
                    {
                        "item": {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "Recovered."}],
                        }
                    },
                ),
                stderr="",
            ),
        ]
    )
    monkeypatch.setenv("ASSERT_AGENT_VERSION", "14")
    monkeypatch.setattr(target.subprocess, "run", lambda *args, **kwargs: next(attempts))
    monkeypatch.setattr(target.time, "sleep", lambda _: None)
    monkeypatch.setattr(target, "_emit_trajectory", lambda *args: None)

    assert target._chat_hosted("test") == "Recovered."
