"""ASSERT callable target for local or deployed HelpdeskBot evaluation.

ASSERT (https://github.com/responsibleai/ASSERT) is a standalone open-source
evaluation harness. It is not a Microsoft Foundry feature and there is no
Foundry-hosted ASSERT service, so the deployed agent is reached the same way any
other client reaches it: an authenticated HTTP call to its Responses endpoint.

Two modes, selected with `ASSERT_TARGET_MODE`:

- `local` (default) builds the guarded agent in-process and replays ASSERT's
  conversation history as Agent Framework messages. It needs no deployment,
  which keeps development cheap, but it exercises the local process rather than
  the deployed revision.
- `hosted` calls the deployed Hosted Agent over the Responses (v2) protocol.
  This mirrors the official Foundry hosted-agent example
  (https://github.com/responsibleai/ASSERT/tree/main/examples/langgraph-foundry-hosted),
  whose `auto_trace.py` uses `httpx` plus `DefaultAzureCredential`. ASSERT's
  native `azure_ai/agents/<id>` target covers the v1 Assistants surface, not a
  custom Responses hosted agent, so a callable wrapper is the supported path.

Hosted mode is opt-in on purpose. Every ASSERT turn runs the real agent and its
real tools, so point it only at a fixture-backed or sandbox deployment.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from azure.identity import DefaultAzureCredential


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = REPOSITORY_ROOT / "src" / "helpdeskbot"
sys.path.insert(0, str(AGENT_ROOT))

AZURE_AI_SCOPE = "https://ai.azure.com/.default"
RESPONSES_API_VERSION = "v1"
RESPONSES_PATH_SUFFIX = "/responses"
REQUEST_TIMEOUT_SECONDS = 300.0

_credential_instance: DefaultAzureCredential | None = None


class FoundryTargetError(RuntimeError):
    """Raised when the deployed agent cannot be called or did not answer."""


def _safe_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: (
                f"token:{hashlib.sha256(str(item).encode()).hexdigest()[:12]}"
                if key == "evidence_reference" or key.endswith("_evidence_reference")
                else _safe_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    return value


def _decode_json(value: Any) -> Any:
    """Decode a JSON string when possible, otherwise keep the original value."""
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


async def _chat_in_process(
    message: str, history: list[dict[str, Any]] | None = None
) -> str:
    from agent_framework import Message
    from main import build_agent

    os.environ.setdefault("HELPDESKBOT_MODE", "safe")
    responses_input = build_responses_input(message, history)
    agent_input: str | list[Message]
    if isinstance(responses_input, str):
        agent_input = responses_input
    else:
        agent_input = []
        for item in responses_input:
            role = item.get("role")
            content = item.get("content")
            if not isinstance(role, str) or not role.strip() or content is None:
                raise FoundryTargetError(
                    "ASSERT history items must contain a non-empty role and content."
                )
            contents = content if isinstance(content, list) else [content]
            agent_input.append(Message(role=role, contents=contents))

    async with build_agent() as agent:
        result = await agent.run(agent_input)
        return result.text


def normalize_agent_endpoint(raw: str) -> str:
    """Return the canonical `/responses` URL for a deployed Hosted Agent.

    `azd deploy` prints the Responses endpoint, but the agent base URL gets
    pasted just as often, and both forms arrive with stray slashes or a leftover
    `?api-version=` query. Normalizing here keeps one shape on the wire instead
    of failing later with a confusing 404.
    """
    candidate = raw.strip()
    if not candidate:
        raise FoundryTargetError(
            "FOUNDRY_AGENT_ENDPOINT is empty. Set it to the deployed agent's "
            "Responses endpoint printed by `azd deploy`."
        )

    parts = urlsplit(candidate)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise FoundryTargetError(
            f"FOUNDRY_AGENT_ENDPOINT must be an absolute http(s) URL, got {raw!r}."
        )

    path = parts.path.rstrip("/")
    if not path.endswith(RESPONSES_PATH_SUFFIX):
        path = f"{path}{RESPONSES_PATH_SUFFIX}"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _agent_endpoint() -> str:
    return normalize_agent_endpoint(os.getenv("FOUNDRY_AGENT_ENDPOINT", ""))


def _credential() -> DefaultAzureCredential:
    global _credential_instance
    if _credential_instance is None:
        _credential_instance = DefaultAzureCredential()
    return _credential_instance


def _access_token() -> str:
    """Return a short-lived Entra token. Never logged, never persisted."""
    return _credential().get_token(AZURE_AI_SCOPE).token


def build_responses_input(
    message: str, history: list[dict[str, Any]] | None
) -> list[dict[str, Any]] | str:
    """Build the Responses `input` payload from ASSERT's callable arguments.

    ASSERT puts the current user turn at `history[-1]` and passes `message` as a
    convenience, so appending unconditionally would duplicate the last turn.
    """
    if not history:
        return message

    messages = [dict(item) for item in history]
    if messages[-1].get("content") != message:
        messages.append({"role": "user", "content": message})
    return messages


def parse_responses_payload(payload: Any) -> tuple[list[dict[str, Any]], str]:
    """Reconstruct the tool trajectory and the final assistant text.

    Intermediate assistant messages can precede the answer, so the final text
    comes from the last `message` item rather than the first.
    """
    if not isinstance(payload, Mapping):
        raise FoundryTargetError(
            f"Hosted agent returned {type(payload).__name__}, expected a JSON object."
        )

    status = payload.get("status")
    if status != "completed":
        details = [f"status={status!r}"]
        error = payload.get("error")
        if isinstance(error, Mapping):
            if error.get("code"):
                details.append(f"error_code={error['code']!r}")
            if error.get("message"):
                details.append(f"error_message={error['message']!r}")
        incomplete = payload.get("incomplete_details")
        if isinstance(incomplete, Mapping) and incomplete.get("reason"):
            details.append(f"incomplete_reason={incomplete['reason']!r}")
        raise FoundryTargetError(
            "Hosted agent response did not complete successfully: "
            + ", ".join(details)
            + ". Treat this as a failed evaluation run."
        )

    output = payload.get("output")
    output_items = output if isinstance(output, list) else []

    calls: dict[str, dict[str, Any]] = {}
    seen_call_ids: set[str] = set()
    trajectory: list[dict[str, Any]] = []
    for item in output_items:
        if not isinstance(item, Mapping):
            continue
        item_type = item.get("type")
        call_id = item.get("call_id")
        if item_type == "function_call":
            if not isinstance(call_id, str) or not call_id:
                raise FoundryTargetError(
                    "Hosted response contained a function call with no valid call_id."
                )
            if call_id in seen_call_ids:
                raise FoundryTargetError(
                    f"Hosted response repeated function call ID {call_id!r}."
                )
            seen_call_ids.add(call_id)
            calls[call_id] = {
                "name": item.get("name", "unknown"),
                "arguments": _safe_value(_decode_json(item.get("arguments") or {})),
            }
        elif item_type == "function_call_output":
            if not isinstance(call_id, str) or not call_id:
                raise FoundryTargetError(
                    "Hosted response contained a function result with no valid call_id."
                )
            call = calls.pop(call_id, None)
            if call is None:
                raise FoundryTargetError(
                    f"Hosted response omitted the function call for {call_id!r}."
                )
            trajectory.append(
                {**call, "output": _safe_value(_decode_json(item.get("output")))}
            )

    if calls:
        raise FoundryTargetError(
            "Hosted response omitted function results for call IDs: "
            + ", ".join(repr(call_id) for call_id in sorted(calls))
            + ". Treat this as a truncated or failed run."
        )

    for item in reversed(output_items):
        if not isinstance(item, Mapping) or item.get("type") != "message":
            continue
        text = "".join(
            part.get("text", "")
            for part in item.get("content", [])
            if isinstance(part, Mapping) and part.get("type") == "output_text"
        )
        if text:
            return trajectory, text

    fallback = payload.get("output_text")
    if isinstance(fallback, str) and fallback:
        return trajectory, fallback

    seen = sorted(
        {str(item.get("type")) for item in output_items if isinstance(item, Mapping)}
    )
    raise FoundryTargetError(
        "Hosted agent returned no assistant output text. "
        f"Output item types: {seen or 'none'}. "
        "Treat this as a failed run rather than an empty answer."
    )


def _emit_trajectory(trajectory: list[dict[str, Any]], final_text: str) -> None:
    from opentelemetry import trace

    tracer = trace.get_tracer("helpdeskbot.assert_target")
    for step in trajectory:
        with tracer.start_as_current_span(f"helpdeskbot.{step['name']}") as span:
            span.set_attribute("openinference.span.kind", "TOOL")
            span.set_attribute("tool.name", step["name"])
            span.set_attribute("input.value", json.dumps(step["arguments"], sort_keys=True))
            span.set_attribute("output.value", json.dumps(step["output"], sort_keys=True))

    with tracer.start_as_current_span("helpdeskbot.response") as span:
        span.set_attribute("openinference.span.kind", "LLM")
        span.set_attribute("output.value", final_text)
        span.set_attribute("llm.model_name", "hosted-helpdeskbot")


def _chat_hosted(message: str, history: list[dict[str, Any]] | None = None) -> str:
    endpoint = _agent_endpoint()
    response = httpx.post(
        endpoint,
        params={"api-version": RESPONSES_API_VERSION},
        headers={
            "Authorization": f"Bearer {_access_token()}",
            "Content-Type": "application/json",
        },
        json={"input": build_responses_input(message, history), "stream": False},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    trajectory, final_text = parse_responses_payload(response.json())
    _emit_trajectory(trajectory, final_text)
    return final_text


def chat(message: str, history: list[dict[str, Any]] | None = None) -> str:
    """Run one guarded turn for ASSERT.

    `history` is optional so single-turn callers still resolve the signature.
    Hosted mode replays it so the deployed agent sees the full conversation.
    """
    mode = os.getenv("ASSERT_TARGET_MODE", "local").strip().lower()
    if mode == "hosted":
        return _chat_hosted(message, history)
    if mode == "local":
        return asyncio.run(_chat_in_process(message, history))
    raise FoundryTargetError(
        "ASSERT_TARGET_MODE must be either 'local' or 'hosted', "
        f"got {mode!r}."
    )
