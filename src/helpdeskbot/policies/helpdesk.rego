package helpdeskbot

import rego.v1

default verdict := {"decision": "allow"}

args := object.get(input.policy_target, "value", {})

safe_snapshot := object.get(input.snapshot, "safe", {})

evidence := object.get(safe_snapshot, "evidence", {})

case_id := lower(trim_space(object.get(args, "case_id", "")))

account_alias := lower(trim_space(object.get(args, "account_alias", "")))

allowed_case if {
    case_id in {"token-expired-signin", "locked-signin"}
}

case_account_pair if {
    case_id == "token-expired-signin"
    account_alias == "alex-user"
}

case_account_pair if {
    case_id == "locked-signin"
    account_alias == "locked-user"
}

pii_in_ticket if {
    input.tool.name == "create_escalation_ticket"
    contains(object.get(args, "summary", ""), "@")
}

pii_in_ticket if {
    input.tool.name == "create_escalation_ticket"
    contains(case_id, "@")
}

pii_in_ticket if {
    input.tool.name == "create_escalation_ticket"
    contains(account_alias, "@")
}

scope_violation if {
    not allowed_case
}

scope_violation if {
    input.tool.name == "get_system_status"
    lower(trim_space(object.get(args, "service", ""))) != "identity"
}

scope_violation if {
    input.tool.name == "get_user_account"
    not case_account_pair
}

scope_violation if {
    input.tool.name == "create_escalation_ticket"
    not case_account_pair
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
    "reason": "pii_in_ticket",
    "message": "Ticket fields must use only the fictional case and alias.",
} if {
    pii_in_ticket
}
else := {
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
    "reason": "evidence_subject_mismatch",
    "message": "Escalation evidence belongs to a different fictional account.",
} if {
    input.tool.name == "create_escalation_ticket"
    account_alias != lower(trim_space(
        object.get(object.get(evidence, "facts", {}), "account_alias", ""),
    ))
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
