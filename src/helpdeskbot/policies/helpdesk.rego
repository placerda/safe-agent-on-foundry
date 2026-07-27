package helpdeskbot

import rego.v1

default verdict := {"decision": "allow"}

post_tool_verdict := {
    "decision": "allow",
    "reason": "result_observed",
    "message": "Tool result completed policy evaluation.",
}

verdict := {
    "decision": "deny",
    "reason": "pii_in_ticket_summary",
    "message": "Ticket summaries must not contain email addresses.",
} if {
    input.tool.name == "create_escalation_ticket"
    args := object.get(input.policy_target, "value", {})
    contains(object.get(args, "summary", ""), "@")
}
else := {
    "decision": "deny",
    "reason": "known_local_remediation",
    "message": "The demo-user fixture has a documented local remediation.",
} if {
    input.tool.name == "create_escalation_ticket"
    args := object.get(input.policy_target, "value", {})
    lower(trim_space(object.get(args, "account_alias", ""))) == "demo-user"
}
else := {
    "decision": "deny",
    "reason": "diagnosis_required",
    "message": "Escalation requires diagnostic evidence, not urgency alone.",
} if {
    input.tool.name == "create_escalation_ticket"
    args := object.get(input.policy_target, "value", {})
    lower(trim_space(object.get(args, "diagnosis", ""))) != "no-local-remediation"
}
