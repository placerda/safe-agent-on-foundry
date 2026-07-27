from pathlib import Path

import pytest

import config
from config import (
    DemoMode,
    SAFE_SEQUENCE,
    VULNERABLE_SEQUENCE,
    expected_demo_sequence,
    get_agent_config,
    get_instructions,
    get_mode,
)


def test_default_mode_is_safe(monkeypatch):
    monkeypatch.delenv("HELPDESKBOT_MODE", raising=False)
    assert get_mode() is DemoMode.SAFE


def test_modes_have_distinct_reproducible_routes():
    assert expected_demo_sequence(DemoMode.SAFE) == SAFE_SEQUENCE
    assert expected_demo_sequence(DemoMode.VULNERABLE) == VULNERABLE_SEQUENCE
    assert SAFE_SEQUENCE == ("get_system_status", "get_user_account", "search_kb")
    assert VULNERABLE_SEQUENCE == ("create_escalation_ticket",)


def test_vulnerable_mode_expects_acs_recovery():
    safe = get_instructions(DemoMode.SAFE)
    vulnerable = get_instructions(DemoMode.VULNERABLE)
    assert "Do not create a ticket" in safe
    assert "immediately call create_escalation_ticket" in vulnerable
    assert "If ACS blocks the call" in vulnerable


def test_unknown_mode_fails_closed():
    with pytest.raises(ValueError, match="safe, vulnerable"):
        get_mode("fast")


def test_repository_dotenv_overrides_stale_values(monkeypatch, tmp_path: Path):
    expected_endpoint = (
        "https://demo.services.ai.azure.com/api/projects/helpdeskbot"
    )
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                f"FOUNDRY_PROJECT_ENDPOINT={expected_endpoint}",
                "AZURE_AI_MODEL_DEPLOYMENT_NAME=gpt-5.4-mini",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://stale.example")
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "stale-model")

    agent_config = get_agent_config()

    assert agent_config.project_endpoint == expected_endpoint
    assert agent_config.model_deployment_name == "gpt-5.4-mini"


def test_missing_configuration_fails_fast(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(config, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.delenv("FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", raising=False)

    with pytest.raises(ValueError, match="Missing required configuration"):
        get_agent_config()

