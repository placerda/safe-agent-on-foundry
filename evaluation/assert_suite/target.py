"""ASSERT callable target for the in-process HelpdeskBot."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


AGENT_ROOT = Path(__file__).resolve().parents[2] / "src" / "helpdeskbot"
sys.path.insert(0, str(AGENT_ROOT))

from main import build_agent  # noqa: E402


async def _chat(message: str) -> str:
    os.environ.setdefault("HELPDESKBOT_MODE", "vulnerable")
    async with build_agent() as agent:
        result = await agent.run(message)
        return result.text


def chat(message: str) -> str:
    """Run one isolated guarded turn for ASSERT."""
    return asyncio.run(_chat(message))
