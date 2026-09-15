from pathlib import Path

import yaml


def test_agentignore_is_at_the_configured_service_root():
    root = Path(__file__).resolve().parents[1]
    deployment = yaml.safe_load((root / "azure.yaml").read_text())
    source = root / deployment["services"]["helpdeskbot"]["project"]
    rules = {
        line.strip()
        for line in (source / ".agentignore").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert ".foundry/" in rules
    assert not any(rule.startswith("!") for rule in rules)
    assert {".env", ".env.*", ".azure/", ".git/", "__pycache__/", ".venv/", "*.pyc"} <= rules
    assert not {"*.py", "policies/", "*.rego", "requirements.txt", "tests/", "eval.yaml"} & rules
    assert (source / "host_boundary.py").is_file()
    assert (source / "policies" / "manifest.yaml").is_file()
