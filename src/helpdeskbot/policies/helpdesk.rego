package helpdeskbot

import rego.v1

default verdict := {"decision": "allow"}

args := object.get(input.policy_target, "value", {})

safe_snapshot := object.get(input.snapshot, "safe", {})

evidence := object.get(safe_snapshot, "evidence", {})

allowed_ticket_severity if {
    severity := lower(trim_space(object.get(args, "severity", "")))
    severity in {"low", "medium"}
}

ticket_outside_scope if {
    lower(trim_space(object.get(args, "category", ""))) != "access"
}

ticket_outside_scope if {
    not allowed_ticket_severity
}

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
    contains(object.get(args, "summary", ""), "@")
}
else := {
    "decision": "deny",
    "reason": "scope_boundary",
    "message": "HelpdeskBot may create only low or medium access tickets.",
} if {
    input.tool.name == "create_escalation_ticket"
    ticket_outside_scope
}
else := {
    "decision": "deny",
    "reason": "flow_integrity_violation",
    "message": "This diagnostic step requires trusted evidence from the prior step.",
} if {
    input.tool.name in {"get_user_account", "search_kb"}
    object.get(evidence, "valid", false) != true
}
else := {
    "decision": "deny",
    "reason": "unanchored_decision",
    "message": "Escalation requires host-verified diagnostic evidence.",
} if {
    input.tool.name == "create_escalation_ticket"
    object.get(evidence, "valid", false) != true
}
else := {
    "decision": "deny",
    "reason": "evidence_subject_mismatch",
    "message": "Escalation evidence belongs to a different fictional account.",
} if {
    input.tool.name == "create_escalation_ticket"
    lower(trim_space(object.get(args, "account_alias", ""))) !=
        lower(trim_space(object.get(object.get(evidence, "facts", {}), "account_alias", "")))
}
else := {
    "decision": "deny",
    "reason": "local_remediation_available",
    "message": "The anchored decision requires local remediation, not escalation.",
} if {
    input.tool.name == "create_escalation_ticket"
    object.get(
        object.get(evidence, "facts", {}),
        "local_remediation_available",
        false,
    ) == true
}
