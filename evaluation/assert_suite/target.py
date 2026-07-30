"""ASSERT callable target for local or deployed HelpdeskBot evaluation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = REPOSITORY_ROOT / "src" / "helpdeskbot"
sys.path.insert(0, str(AGENT_ROOT))


def _safe_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: (
                f"token:{hashlib.sha256(str(item).encode()).hexdigest()[:12]}"
                if key == "evidence_token" or key.endswith("_evidence_token")
                else _safe_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    return value


async def _chat_in_process(message: str) -> str:
    from main import build_agent

    os.environ.setdefault("HELPDESKBOT_MODE", "safe")
    async with build_agent() as agent:
        result = await agent.run(message)
        return result.text


def _parse_hosted_response(raw: str) -> tuple[list[dict[str, Any]], str]:
    event_name = ""
    calls: dict[str, dict[str, Any]] = {}
    trajectory: list[dict[str, Any]] = []
    final_text = ""
    text_deltas: list[str] = []

    for line in raw.splitlines():
        if line.startswith("event: "):
            event_name = line.removeprefix("event: ")
            continue
        if not line.startswith("data: "):
            continue

        payload = json.loads(line.removeprefix("data: "))
        if event_name == "response.output_text.delta":
            text_deltas.append(payload.get("delta", ""))
            continue
        if event_name == "response.completed" and not final_text:
            for response_item in payload.get("response", {}).get("output", []):
                if response_item.get("type") != "message":
                    continue
                final_text = "".join(
                    part.get("text", "")
                    for part in response_item.get("content", [])
                    if part.get("type") == "output_text"
                )
            continue
        if event_name != "response.output_item.done":
            continue

        item = payload.get("item", {})
        item_type = item.get("type")
        call_id = item.get("call_id")

        if item_type == "function_call":
            arguments = json.loads(item.get("arguments") or "{}")
            calls[call_id] = {
                "name": item.get("name", "unknown"),
                "arguments": _safe_value(arguments),
            }
        elif item_type == "function_call_output":
            call = calls.get(call_id)
            if call is None:
                raise ValueError(f"Hosted response omitted function call {call_id}.")
            output = item.get("output", item.get("function_call_output", {}))
            if isinstance(output, str):
                output = json.loads(output)
            trajectory.append({**call, "output": _safe_value(output)})
        elif item_type == "message":
            content = item.get("content") or []
            final_text = "".join(
                part.get("text", "")
                for part in content
                if part.get("type") == "output_text"
            )

    final_text = final_text or "".join(text_deltas)
    if not final_text:
        final_text = (
            "[NO_FINAL_RESPONSE] Hosted Agent stream completed without an "
            "output message. Judge the captured trajectory as an incomplete run."
        )
    return trajectory, final_text


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


def _chat_hosted(message: str) -> str:
    agent_name = os.getenv("ASSERT_AGENT_NAME", "helpdeskbot")
    version = os.environ["ASSERT_AGENT_VERSION"]
    environment = os.getenv("ASSERT_AZD_ENVIRONMENT", "safe-e2e")
    command = [
        "azd",
        "ai",
        "agent",
        "invoke",
        agent_name,
        message,
        "--version",
        version,
        "--new-session",
        "--new-conversation",
        "--environment",
        environment,
        "--no-prompt",
        "--timeout",
        "600",
        "--output",
        "raw",
    ]
    for attempt in range(3):
        completed = subprocess.run(
            command,
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=660,
        )
        if not completed.returncode or completed.stdout.strip():
            break
        if attempt < 2:
            time.sleep(2**attempt)
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        message = detail[-1] if detail else f"azd exited with {completed.returncode}"
        final_text = f"[HOSTED_INVOCATION_ERROR] {message}"
        _emit_trajectory([], final_text)
        return final_text
    trajectory, final_text = _parse_hosted_response(completed.stdout)
    _emit_trajectory(trajectory, final_text)
    return final_text


def chat(message: str) -> str:
    """Run one isolated guarded turn for ASSERT."""
    if os.getenv("ASSERT_TARGET_MODE", "local").lower() == "hosted":
        return _chat_hosted(message)
    return asyncio.run(_chat_in_process(message))
