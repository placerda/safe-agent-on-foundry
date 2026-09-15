package helpdeskbot

import rego.v1

default verdict := {"decision": "allow"}

default output_verdict := {
    "decision": "allow",
    "reason": "output_clear",
    "message": "No unresolved escalation is pending for this invocation.",
}

args := object.get(input.policy_target, "value", {})

safe_snapshot := object.get(object.get(input.snapshot, "extensions", {}), "safe.example/host", {})

evidence := object.get(safe_snapshot, "evidence", {})

escalations := object.get(safe_snapshot, "escalations", [])

case_id := lower(trim_space(object.get(args, "case_id", "")))

allowed_case if {
    case_id in {"token-expired-signin", "locked-signin"}
}

scope_violation if {
    not allowed_case
}

scope_violation if {
    input.tool.name == "get_system_status"
    lower(trim_space(object.get(args, "service", ""))) != "identity"
}

scope_violation if {
    input.tool.name == "create_escalation_ticket"
    lower(trim_space(object.get(args, "category", ""))) != "access"
}

scope_violation if {
    input.tool.name == "create_escalation_ticket"
    lower(trim_space(object.get(args, "severity", ""))) != "medium"
}

exact_flow_evidence if {
    input.tool.name == "get_user_account"
    object.get(evidence, "stage", "") == "system_status"
    object.get(evidence, "audience", "") == "get_user_account"
    object.get(evidence, "sequence", []) == ["get_system_status"]
}

exact_flow_evidence if {
    input.tool.name == "search_kb"
    object.get(evidence, "stage", "") == "account"
    object.get(evidence, "audience", "") == "search_kb"
    object.get(evidence, "sequence", []) == [
        "get_system_status",
        "get_user_account",
    ]
}

exact_flow_evidence if {
    input.tool.name == "create_escalation_ticket"
    object.get(evidence, "stage", "") == "decision"
    object.get(evidence, "audience", "") == "create_escalation_ticket"
    object.get(evidence, "sequence", []) == [
        "get_system_status",
        "get_user_account",
        "search_kb",
    ]
}

post_tool_verdict := {
    "decision": "allow",
    "reason": "result_observed",
    "message": "Tool result completed policy evaluation.",
}

verdict := {
    "decision": "deny",
    "reason": "scope_boundary",
    "message": "The requested case, tool arguments, or ticket authority is out of scope.",
} if {
    scope_violation
}
else := {
    "decision": "deny",
    "reason": "flow_integrity_violation",
    "message": "This step requires the exact trusted trajectory from prior tools.",
} if {
    input.tool.name in {"get_user_account", "search_kb"}
    object.get(evidence, "valid", false) != true
}
else := {
    "decision": "deny",
    "reason": "flow_integrity_violation",
    "message": "The evidence sequence, stage, or audience does not match this step.",
} if {
    input.tool.name in {
        "get_user_account",
        "search_kb",
        "create_escalation_ticket",
    }
    object.get(evidence, "valid", false) == true
    not exact_flow_evidence
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
    object.get(evidence, "case_id", "") != case_id
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

# --- output intervention point ---------------------------------------------
#
# `escalations` is a host-assembled list, one entry per case_id diagnosed
# during this invocation: {"case_id", "local_remediation_available",
# "ticket_exists"}. It never comes from the model. An unresolved escalation
# is a case where diagnostics already proved no local remediation exists and
# no escalation ticket exists yet -- the Hosted Agent must not release a
# final response in that state.

unresolved_escalation contains item if {
    some item in escalations
    object.get(item, "local_remediation_available", true) == false
    object.get(item, "ticket_exists", false) != true
}

output_verdict := {
    "decision": "deny",
    "reason": "missing_escalation_state",
    "message": "The host did not report escalation state for this invocation.",
} if {
    not is_array(escalations)
}
else := {
    "decision": "deny",
    "reason": "missing_escalation_ticket",
    "message": "Diagnostics found no local remediation and no escalation ticket exists for the case.",
} if {
    is_array(escalations)
    count(unresolved_escalation) > 0
}
