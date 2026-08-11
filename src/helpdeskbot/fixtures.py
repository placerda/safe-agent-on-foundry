"""Deterministic fictional scope for the SAFE helpdesk sample."""

CASE_ACCOUNT_STATES: dict[str, dict[str, str | bool]] = {
    "token-expired-signin": {
        "found": True,
        "state": "active",
        "sign_in_allowed": True,
        "token_state": "expired",
        "source": "in-memory-mock",
    },
    "locked-signin": {
        "found": True,
        "state": "locked",
        "sign_in_allowed": False,
        "token_state": "valid",
        "source": "in-memory-mock",
    },
}

SUPPORTED_CASES = frozenset(CASE_ACCOUNT_STATES)

ALLOWED_SERVICE = "identity"
ALLOWED_TICKET_CATEGORY = "access"
ALLOWED_TICKET_SEVERITY = "medium"
