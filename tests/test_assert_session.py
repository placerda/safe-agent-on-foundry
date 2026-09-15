"""Version-bound hosted sessions preserve ASSERT's explicit history replay."""

import httpx
import pytest

from evaluation.assert_suite import target


@pytest.mark.parametrize("session_id", [None, "session-fixture", " session-fixture "])
def test_hosted_session_binding_preserves_request_and_history(
    monkeypatch, session_id
) -> None:
    monkeypatch.setenv("ASSERT_TARGET_MODE", "hosted")
    monkeypatch.setenv(
        "FOUNDRY_AGENT_ENDPOINT",
        "https://agent.example/endpoint/protocols/openai/responses",
    )
    if session_id is None:
        monkeypatch.delenv("FOUNDRY_AGENT_SESSION_ID", raising=False)
    else:
        monkeypatch.setenv("FOUNDRY_AGENT_SESSION_ID", session_id)
    monkeypatch.setattr(target, "_access_token", lambda: "stub-token")
    monkeypatch.setattr(target, "_emit_trajectory", lambda *args: None)
    requests = []

    def post(url, **kwargs):
        requests.append(kwargs)
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Approved answer"}],
                    }
                ],
            },
        )

    monkeypatch.setattr(target.httpx, "post", post)
    history = [{"role": "user", "content": "First turn"}]

    assert target.chat("Second turn", history) == "Approved answer"
    assert len(requests) == 1
    expected = {
        "input": [
            {"role": "user", "content": "First turn"},
            {"role": "user", "content": "Second turn"},
        ],
        "stream": False,
    }
    if session_id is not None:
        expected["agent_session_id"] = "session-fixture"
    assert requests[0]["json"] == expected
    assert history == [{"role": "user", "content": "First turn"}]


@pytest.mark.parametrize("session_id", ["", " \t "])
def test_empty_session_binding_fails_before_authentication_or_http(
    monkeypatch, session_id
) -> None:
    monkeypatch.setenv("ASSERT_TARGET_MODE", "hosted")
    monkeypatch.setenv("FOUNDRY_AGENT_ENDPOINT", "https://agent.example/responses")
    monkeypatch.setenv("FOUNDRY_AGENT_SESSION_ID", session_id)

    def unexpected_call(*args, **kwargs):
        pytest.fail("Invalid session binding must fail before authentication or HTTP")

    monkeypatch.setattr(target, "_access_token", unexpected_call)
    monkeypatch.setattr(target.httpx, "post", unexpected_call)
    with pytest.raises(target.FoundryTargetError, match="FOUNDRY_AGENT_SESSION_ID is empty"):
        target.chat("DEMO_CASE: locked-signin")
