from importlib import import_module
from pathlib import Path

import yaml


def test_assert_callable_resolves():
    target = import_module("evaluation.assert_suite.target")

    assert callable(target.chat)


def test_assert_judges_all_four_safe_principles():
    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load(
        (root / "evaluation" / "assert_suite" / "eval_config.yaml").read_text(
            encoding="utf-8"
        )
    )

    assert set(config["pipeline"]["judge"]["dimensions"]) == {
        "scope_violation",
        "anchoring_violation",
        "flow_integrity_violation",
        "escalation_violation",
    }
