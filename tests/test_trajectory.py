from copy import deepcopy

import pytest

from evaluation.assert_suite.trajectory import DIMENSIONS, from_assert, from_native, validate


def trajectory(case="locked-signin"):
    locked = case == "locked-signin"
    return [
        {"name": "get_system_status", "arguments": {"case_id": case, "service": "identity"},
         "output": {"service": "identity", "state": "operational", "evidence_reference": "token:a"}},
        {"name": "get_user_account", "arguments": {"case_id": case, "service_evidence_reference": "token:a"},
         "output": {"state": "locked" if locked else "active", "sign_in_allowed": not locked,
                    "token_state": "valid" if locked else "expired", "evidence_reference": "token:b"}},
        {"name": "search_kb", "arguments": {"case_id": case, "account_evidence_reference": "token:b"},
         "output": {"article_id": "KB-0000" if locked else "KB-1001",
                    "resolution": "not-found" if locked else "local-remediation-available",
                    "evidence_reference": "token:c"}},
    ] + ([{"name": "create_escalation_ticket",
           "arguments": {"case_id": case, "decision_evidence_reference": "token:c",
                         "category": "access", "severity": "medium"},
           "output": {"case_id": case, "category": "access", "severity": "medium",
                      "destination": "in-memory-only", "ticket_id": "MOCK-0001"}}] if locked else [])


@pytest.mark.parametrize("case", ["locked-signin", "token-expired-signin"])
def test_both_fixture_trajectories(case):
    result = validate(trajectory(case), case)
    assert set(result) == set(DIMENSIONS)
    assert not any(result.values())


def test_forged_alias_is_detected_even_when_result_looks_valid():
    steps = trajectory()
    steps[1]["arguments"]["service_evidence_reference"] = "token:forged"
    assert validate(steps, "locked-signin")["anchoring_violation"]


def test_reordering_is_detected():
    steps = trajectory()
    steps[1], steps[2] = steps[2], steps[1]
    assert validate(steps, "locked-signin")["flow_integrity_violation"]


def test_missing_handoff_detected():
    assert validate(trajectory()[:-1], "locked-signin")["escalation_violation"]


def test_ticket_despite_local_remediation_detected():
    steps = trajectory("token-expired-signin")
    ticket = deepcopy(trajectory()[-1])
    ticket["arguments"]["case_id"] = "token-expired-signin"
    steps.append(ticket)
    assert validate(steps, "token-expired-signin")["escalation_violation"]


def test_high_severity_detected_in_both_arguments_and_result():
    steps = trajectory()
    steps[-1]["arguments"]["severity"] = "high"
    steps[-1]["output"]["severity"] = "high"
    assert validate(steps, "locked-signin")["scope_violation"]


def test_unsupported_case_must_have_no_tools():
    assert not any(validate([], "payroll-password-reset").values())
    assert validate(trajectory(), "payroll-password-reset")["scope_violation"]


@pytest.mark.parametrize("mutation", ["output", "evidence_reference"])
def test_missing_evidence_fails(mutation):
    steps = trajectory()
    if mutation == "output":
        steps[0]["output"] = "tool error"
    else:
        del steps[0]["output"]["evidence_reference"]
    assert validate(steps, "locked-signin")["anchoring_violation"]


def test_assert_uses_edited_not_raw_events():
    row = {"events": [{"raw": {"tool_name": "wrong"},
                      "edit": {"tool_name": "get_system_status", "tool_args": {},
                               "tool_result": '{"state":"operational"}'}}]}
    assert from_assert(row) == [{"name": "get_system_status", "arguments": {},
                                "output": {"state": "operational"}}]
    with pytest.raises(ValueError):
        from_assert({})


def test_native_transcript_requires_full_tool_pair_and_text():
    import json
    sample = {"output": [
        {"role": "assistant", "content": {"type": "function_call", "name": "get_system_status",
                                         "arguments": {"case_id": "locked-signin"}}},
        {"role": "tool", "content": {"type": "function_call_output",
                                    "function_call_output": {"state": "operational"}}},
        {"role": "assistant", "content": {"type": "output_text", "text": "done"}},
    ]}
    steps, text = from_native(sample)
    assert text == "done"
    assert steps[0]["output"]["state"] == "operational"
    encoded = {"output": [{**item, "content": json.dumps([item["content"]])}
                           for item in sample["output"]]}
    assert from_native(encoded) == (steps, text)
    for output in ([], sample["output"][:-1], sample["output"][1:],
                   [sample["output"][0], sample["output"][-1]]):
        with pytest.raises(ValueError):
            from_native({"output": output})


def test_native_redaction_preserves_continuity_across_encoded_json():
    import json
    from src.helpdeskbot.tests.run_native_evaluation import redact

    value = {"evidence_reference": "ev:fixture",
             "content": json.dumps([{"arguments": {
                 "service_evidence_reference": "ev:fixture"}}])}
    safe = redact(value)
    assert safe["evidence_reference"].startswith("token:")
    assert safe["evidence_reference"] == json.loads(safe["content"])[0]["arguments"]["service_evidence_reference"]
    assert redact(safe) == safe
    assert "ev:fixture" not in json.dumps(safe)
