from importlib import import_module
from inspect import signature
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ASSERT_SUITE = ROOT / "evaluation" / "assert_suite"


def _config() -> dict:
    return yaml.safe_load((ASSERT_SUITE / "eval_config.yaml").read_text(encoding="utf-8"))


def test_assert_callable_resolves():
    target = import_module("evaluation.assert_suite.target")

    assert callable(target.chat)
    # ASSERT inspects the signature and only replays conversation history when
    # the callable declares a `history` parameter.
    assert "history" in signature(target.chat).parameters


def test_assert_target_matches_the_configured_callable():
    reference = _config()["pipeline"]["inference"]["target"]["callable"]
    module_path, function_name = reference.split(":")

    assert callable(getattr(import_module(module_path), function_name))


def test_assert_trace_backend_uses_a_documented_value():
    trace = _config()["pipeline"]["inference"]["target"]["trace"]

    # ASSERT validates group_by and defaults backend to "phoenix"; keep both on
    # the documented values instead of an unverified backend name.
    assert trace == {"backend": "phoenix", "group_by": "session.id"}


def test_assert_requirements_pin_a_verified_revision():
    lines = [
        line.strip()
        for line in (ASSERT_SUITE / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert_line = next(line for line in lines if line.startswith("assert-ai"))

    # The only PyPI release predates the official Foundry hosted-agent example,
    # so the suite pins a commit. A branch would not be reproducible.
    assert "@ git+https://github.com/responsibleai/ASSERT.git@" in assert_line
    assert "[otel,azure-aad]" in assert_line
    revision = assert_line.rsplit("@", 1)[1]
    assert len(revision) == 40 and revision != "main"


def test_assert_judges_all_four_safe_principles():
    assert set(_config()["pipeline"]["judge"]["dimensions"]) == {
        "scope_violation",
        "anchoring_violation",
        "flow_integrity_violation",
        "escalation_violation",
    }


def test_assert_judge_uses_curated_safe_taxonomy():
    path = ASSERT_SUITE / _config()["pipeline"]["judge"]["taxonomy_path"]
    taxonomy = json.loads(path.read_text(encoding="utf-8"))

    assert path == ASSERT_SUITE / "taxonomy.json"
    assert taxonomy["behavior"]["name"] == _config()["behavior"]["name"]
    categories = taxonomy["behavior_categories"]
    assert {item["name"] for item in categories if not item["permissible"]} == {
        "Scope violation",
        "Anchored Decisions violation",
        "Flow Integrity violation",
        "Escalation violation",
    }
    assert all(item["definition"] and item["examples"] for item in categories)


def test_assert_taxonomy_preserves_opposite_fixture_outcomes_and_scope_refusal():
    taxonomy = json.loads((ASSERT_SUITE / "taxonomy.json").read_text(encoding="utf-8"))
    categories = {item["name"]: item for item in taxonomy["behavior_categories"]}

    local = categories["Permitted local remediation"]
    handoff = categories["Mandatory locked-case handoff"]
    refusal = categories["Correct unsupported-case refusal"]
    assert local["permissible"] is True
    assert "token-expired-signin" in local["definition"]
    assert "without a ticket" in local["definition"]
    assert handoff["permissible"] is True
    assert "locked-signin" in handoff["definition"]
    assert "exactly one medium access" in handoff["definition"]
    assert refusal["permissible"] is True
    assert "payroll-password-reset" in refusal["definition"]
