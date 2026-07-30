from __future__ import annotations

import sys
from pathlib import Path

import pytest


AGENT_ROOT = Path(__file__).resolve().parents[1] / "src" / "helpdeskbot"
sys.path.insert(0, str(AGENT_ROOT))


@pytest.fixture(autouse=True)
def evidence_secret(monkeypatch):
    from evidence import clear_evidence_registry

    clear_evidence_registry()
    monkeypatch.setenv(
        "SAFE_EVIDENCE_SECRET",
        "unit-test-secret-with-at-least-32-characters",
    )
