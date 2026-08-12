"""Offline tests for the opt-in live smoke check."""

import pytest

from evaluation.assert_suite import smoke
from evaluation.assert_suite.target import FoundryTargetError


def test_validate_request_requires_hosted_mode(monkeypatch) -> None:
    monkeypatch.delenv("ASSERT_TARGET_MODE", raising=False)

    with pytest.raises(FoundryTargetError, match="ASSERT_TARGET_MODE=hosted"):
        smoke.validate_request(smoke.DEFAULT_MESSAGE)


def test_validate_request_rejects_unsupported_fixtures(monkeypatch) -> None:
    monkeypatch.setenv("ASSERT_TARGET_MODE", "hosted")

    with pytest.raises(FoundryTargetError, match="supported fixture"):
        smoke.validate_request("Reset the password for a real employee.")


def test_validate_request_accepts_supported_fixture(monkeypatch) -> None:
    monkeypatch.setenv("ASSERT_TARGET_MODE", "hosted")

    assert smoke.validate_request(smoke.DEFAULT_MESSAGE) is None


def test_main_reports_the_answer(monkeypatch, capsys) -> None:
    monkeypatch.setenv("ASSERT_TARGET_MODE", "hosted")
    monkeypatch.setattr(smoke, "chat", lambda message: "Use the local fix.")

    assert smoke.main([]) == 0
    assert "Use the local fix." in capsys.readouterr().out


def test_main_fails_without_sending_an_unsupported_message(monkeypatch, capsys) -> None:
    monkeypatch.setenv("ASSERT_TARGET_MODE", "hosted")
    monkeypatch.setattr(
        smoke, "chat", lambda message: pytest.fail("smoke must not call the agent")
    )

    assert smoke.main(["--message", "Reset a real password."]) == 1
    assert "supported fixture" in capsys.readouterr().err
