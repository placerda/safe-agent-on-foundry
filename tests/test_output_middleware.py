"""Tests for the host-owned ACS ``output`` intervention point.

``AcsFunctionMiddleware`` (see ``test_middleware.py`` / ``test_safe_integration.py``)
governs individual tool calls. These tests cover the separate guarantee added by
``AcsOutputMiddleware``: once diagnostics prove
``local_remediation_available=false`` for a case, the Hosted Agent must not
release *any* final response for that invocation unless exactly one
escalation ticket exists for that case -- enforced by the host after the
response is assembled, never by hoping the model remembers.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from acs_middleware import AcsFunctionMiddleware, AcsOutputMiddleware
from agent_framework import AgentResponse, Message, MiddlewareTermination, ResponseStream
import evidence
from evidence import bind_invocation, decisions_for_invocation, new_invocation_id, reset_invocation
from tools import (
    _create_escalation_ticket,
    _get_system_status,
    _get_user_account,
    _search_kb,
    mock_tickets,
    reset_mock_tickets,
)


async def invoke(middleware, tool_name, arguments, implementation):
    """Drive one tool call through ``AcsFunctionMiddleware``.

    Mirrors ``test_safe_integration.invoke`` exactly; duplicated locally so
    this file has no import-order dependency on another test module.
    """
    context = SimpleNamespace(
        function=SimpleNamespace(name=tool_name),
        arguments=arguments,
        result=None,
    )

    async def call_next():
        context.result = implementation(**context.arguments)

    await middleware.process(context, call_next)
    return context.result


async def diagnostic_flow(case_id: str):
    """Run the standard get_system_status -> get_user_account -> search_kb flow."""
    middleware = AcsFunctionMiddleware()
    status = await invoke(
        middleware,
        "get_system_status",
        {"case_id": case_id, "service": "identity"},
        _get_system_status,
    )
    account = await invoke(
        middleware,
        "get_user_account",
        {
            "case_id": case_id,
            "service_evidence_reference": status["evidence_reference"],
        },
        _get_user_account,
    )
    kb = await invoke(
        middleware,
        "search_kb",
        {
            "case_id": case_id,
            "query": "sign-in diagnosis",
            "account_evidence_reference": account["evidence_reference"],
        },
        _search_kb,
    )
    return middleware, status, account, kb


async def run_output(middleware, produce_response, *, stream: bool = False):
    """Drive ``AcsOutputMiddleware.process`` with a fake ``AgentContext``."""
    context = SimpleNamespace(stream=stream, result=None)

    async def call_next():
        context.result = await produce_response()

    await middleware.process(context, call_next)
    return context


async def run_output_expect_blocked(middleware, produce_response, *, stream: bool = False):
    """Like :func:`run_output`, but assert the gate blocks the response."""
    context = SimpleNamespace(stream=stream, result=None)
    call_count = 0

    async def call_next():
        nonlocal call_count
        call_count += 1
        context.result = await produce_response()

    with pytest.raises(MiddlewareTermination):
        await middleware.process(context, call_next)
    return context, call_count


@pytest.mark.asyncio
async def test_output_allowed_without_ticket_when_local_remediation_exists():
    """Local remediation exists -> allow, and no ticket is ever attempted."""
    reset_mock_tickets()
    middleware = AcsOutputMiddleware()

    async def produce_response():
        _, _, _, kb = await diagnostic_flow("token-expired-signin")
        assert kb["evidence_reference"]  # sanity: the diagnostic flow itself ran
        return SimpleNamespace(text="Your sign-in token will refresh automatically.")

    context = await run_output(middleware, produce_response)

    assert context.result is not None
    assert context.result.text == "Your sign-in token will refresh automatically."
    assert mock_tickets() == ()


@pytest.mark.asyncio
async def test_missing_ticket_cannot_leave_the_host_when_remediation_is_unavailable(
    monkeypatch,
):
    """If host-side remediation cannot resolve a case, the response stays blocked.

    Isolates the "never leak" guarantee from the "auto-create" behaviour
    (covered separately below) by forcing remediation to find nothing to do,
    the way it legitimately would for a case with no usable evidence.
    """
    reset_mock_tickets()
    middleware = AcsOutputMiddleware()
    monkeypatch.setattr(
        middleware, "_create_missing_tickets", AsyncMock(return_value=[])
    )

    async def produce_response():
        await diagnostic_flow("locked-signin")
        return SimpleNamespace(text="No local remediation was found.")

    context, call_count = await run_output_expect_blocked(middleware, produce_response)

    assert call_count == 1  # the agent's own turn is never re-run to "fix" this
    assert context.result is None
    assert mock_tickets() == ()


@pytest.mark.asyncio
async def test_host_creates_exactly_one_valid_ticket_when_required():
    """No ticket from the model -> the host creates exactly one, then allows."""
    reset_mock_tickets()
    middleware = AcsOutputMiddleware()

    async def produce_response():
        await diagnostic_flow("locked-signin")
        return AgentResponse(
            messages=[
                Message("assistant", []),
                Message("assistant", ["No local remediation was found."]),
            ],
            response_id="response-123",
        )

    context = await run_output(middleware, produce_response)

    assert context.result is not None
    tickets = mock_tickets()
    assert len(tickets) == 1
    ticket = tickets[0]
    assert ticket["case_id"] == "locked-signin"
    assert ticket["category"] == "access"
    assert ticket["severity"] == "medium"
    assert ticket["ticket_id"] == "MOCK-0001"
    assert context.result.text == (
        "HelpdeskBot completed the required human handoff. "
        "Support ticket MOCK-0001 was created for case locked-signin with "
        "category access and medium severity."
    )
    assert "No local remediation was found." not in context.result.text
    assert context.result.response_id == "response-123"
    assert len(context.result.messages) == 2


@pytest.mark.asyncio
async def test_completed_invocation_discards_decision_state(monkeypatch):
    """Decision evidence is removed after output enforcement completes."""
    reset_mock_tickets()
    invocation_id = "fixed-test-invocation"
    monkeypatch.setattr(
        "acs_middleware.new_invocation_id", lambda: invocation_id
    )
    middleware = AcsOutputMiddleware()

    async def produce_response():
        await diagnostic_flow("locked-signin")
        return SimpleNamespace(text="No local remediation was found.")

    await run_output(middleware, produce_response)

    assert decisions_for_invocation(invocation_id) == {}


@pytest.mark.asyncio
async def test_failed_invocation_discards_decision_state(monkeypatch):
    """Decision evidence is also removed when the agent turn raises."""
    invocation_id = "failed-test-invocation"
    monkeypatch.setattr(
        "acs_middleware.new_invocation_id", lambda: invocation_id
    )
    middleware = AcsOutputMiddleware()

    async def produce_response():
        await diagnostic_flow("locked-signin")
        raise RuntimeError("simulated model failure")

    with pytest.raises(RuntimeError, match="simulated model failure"):
        await run_output(middleware, produce_response)

    assert decisions_for_invocation(invocation_id) == {}


def test_decisions_for_invocation_never_leak_across_invocations():
    """Wrong-invocation isolation at the evidence layer, independent of ACS."""
    evidence.clear_decision_state()
    invocation_a = new_invocation_id()
    token_a = bind_invocation(invocation_a)
    try:
        evidence.record_decision_evidence(
            "locked-signin",
            {
                "evidence_reference": "sig:placeholder-a",
                "facts": {"local_remediation_available": False},
            },
        )
    finally:
        reset_invocation(token_a)

    invocation_b = new_invocation_id()
    token_b = bind_invocation(invocation_b)
    try:
        assert decisions_for_invocation(invocation_b) == {}
    finally:
        reset_invocation(token_b)

    assert "locked-signin" in decisions_for_invocation(invocation_a)
    evidence.clear_decision_state()


@pytest.mark.asyncio
async def test_stale_or_mismatched_decision_facts_cannot_trigger_a_ticket():
    """A corrupted/stale host record cannot manufacture a ticket for the wrong subject.

    Ticket creation re-verifies against the cryptographically signed evidence
    itself (via ``evidence_snapshot_for_call`` / the Rego ``evidence_subject_mismatch``
    rule), not against whatever this in-memory bookkeeping claims. If the
    escalation-tracking record disagrees with what the evidence actually
    proves, remediation must refuse rather than paper over the mismatch.
    """
    reset_mock_tickets()
    middleware = AcsOutputMiddleware()

    async def produce_response():
        _, _, _, kb = await diagnostic_flow("locked-signin")
        # Replace this invocation's record with a different case while keeping
        # evidence that cryptographically proves locked-signin.
        evidence.clear_decision_state()
        evidence.record_decision_evidence(
            "token-expired-signin",
            {
                "evidence_reference": kb["evidence_reference"],
                "facts": {
                    "local_remediation_available": False,
                },
            },
        )
        return SimpleNamespace(text="No local remediation was found.")

    context, _ = await run_output_expect_blocked(middleware, produce_response)

    assert context.result is None
    assert mock_tickets() == ()


@pytest.mark.asyncio
async def test_acs_evaluation_failure_fails_closed(monkeypatch):
    """An ACS/OPA outage must block output, never silently allow it."""
    reset_mock_tickets()
    middleware = AcsOutputMiddleware()

    async def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("simulated ACS outage")

    monkeypatch.setattr(middleware._control, "evaluate_intervention_point", boom)

    async def produce_response():
        await diagnostic_flow("token-expired-signin")
        return SimpleNamespace(text="Your sign-in token will refresh automatically.")

    context, _ = await run_output_expect_blocked(middleware, produce_response)

    assert context.result is None
    assert mock_tickets() == ()


@pytest.mark.asyncio
async def test_repeated_output_evaluation_is_idempotent():
    """Re-evaluating the same case never creates a second ticket."""
    reset_mock_tickets()
    middleware = AcsOutputMiddleware()

    async def produce_response():
        await diagnostic_flow("locked-signin")
        return SimpleNamespace(text="No local remediation was found.")

    first = await run_output(middleware, produce_response)
    second = await run_output(middleware, produce_response)

    assert first.result is not None
    assert second.result is not None
    tickets = mock_tickets()
    assert len(tickets) == 1
    assert tickets[0]["case_id"] == "locked-signin"


@pytest.mark.asyncio
async def test_enforce_output_invoked_twice_for_same_invocation_creates_no_extra_ticket():
    """Idempotent even at the lower level of a single invocation being re-checked."""
    reset_mock_tickets()
    middleware = AcsOutputMiddleware()
    invocation_id = new_invocation_id()
    token = bind_invocation(invocation_id)
    try:
        await diagnostic_flow("locked-signin")
    finally:
        reset_invocation(token)

    context = SimpleNamespace(
        stream=False, result=SimpleNamespace(text="No local remediation was found.")
    )
    await middleware._enforce_output(context, invocation_id)
    await middleware._enforce_output(context, invocation_id)

    tickets = mock_tickets()
    assert len(tickets) == 1


@pytest.mark.asyncio
async def test_streaming_is_released_only_after_output_enforcement():
    """The azd streaming contract receives only the host-approved final response."""
    reset_mock_tickets()
    middleware = AcsOutputMiddleware()

    async def produce_response():
        await diagnostic_flow("locked-signin")
        return AgentResponse(
            messages=[Message("assistant", ["No local remediation was found."])],
            response_id="stream-response-123",
        )

    context = await run_output(middleware, produce_response, stream=True)

    assert context.stream is True
    assert isinstance(context.result, ResponseStream)
    updates = [update async for update in context.result]
    emitted_text = "".join(update.text for update in updates)
    assert emitted_text == (
        "HelpdeskBot completed the required human handoff. "
        "Support ticket MOCK-0001 was created for case locked-signin with "
        "category access and medium severity."
    )
    final_response = await context.result.get_final_response()
    assert final_response.text == emitted_text
    assert final_response.response_id == "stream-response-123"
    assert len(mock_tickets()) == 1


@pytest.mark.asyncio
async def test_streaming_output_failure_releases_no_updates(monkeypatch):
    """A denied assembled response never becomes a stream visible to the caller."""
    reset_mock_tickets()
    middleware = AcsOutputMiddleware()
    monkeypatch.setattr(
        middleware, "_create_missing_tickets", AsyncMock(return_value=[])
    )

    async def produce_response():
        await diagnostic_flow("locked-signin")
        return AgentResponse(
            messages=[Message("assistant", ["No local remediation was found."])]
        )

    context, call_count = await run_output_expect_blocked(
        middleware, produce_response, stream=True
    )

    assert call_count == 1
    assert context.stream is True
    assert context.result is None
    assert mock_tickets() == ()


@pytest.mark.asyncio
async def test_model_initiated_ticket_creation_still_works_alongside_output_gate():
    """Existing pre/post tool-call ticket creation is unaffected by the output gate.

    When the model itself does the right thing and calls
    ``create_escalation_ticket`` during its own turn (exactly as in
    ``test_safe_integration.test_locked_case_creates_exactly_one_anchored_handoff``),
    the output gate must not re-create or duplicate anything -- it should see
    the ticket already exists and simply allow.
    """
    reset_mock_tickets()
    function_middleware = AcsFunctionMiddleware()
    output_middleware = AcsOutputMiddleware()

    async def produce_response():
        status = await invoke(
            function_middleware,
            "get_system_status",
            {"case_id": "locked-signin", "service": "identity"},
            _get_system_status,
        )
        account = await invoke(
            function_middleware,
            "get_user_account",
            {
                "case_id": "locked-signin",
                "service_evidence_reference": status["evidence_reference"],
            },
            _get_user_account,
        )
        kb = await invoke(
            function_middleware,
            "search_kb",
            {
                "case_id": "locked-signin",
                "query": "locked account",
                "account_evidence_reference": account["evidence_reference"],
            },
            _search_kb,
        )
        ticket = await invoke(
            function_middleware,
            "create_escalation_ticket",
            {
                "case_id": "locked-signin",
                "category": "access",
                "severity": "medium",
                "decision_evidence_reference": kb["evidence_reference"],
            },
            _create_escalation_ticket,
        )
        assert ticket["ticket_id"] == "MOCK-0001"
        return SimpleNamespace(text="Escalated to the account team.")

    context = await run_output(output_middleware, produce_response)

    assert context.result is not None
    tickets = mock_tickets()
    assert len(tickets) == 1
    assert tickets[0]["ticket_id"] == "MOCK-0001"


@pytest.mark.asyncio
async def test_concurrent_invocations_for_different_cases_do_not_cross_contaminate():
    """Two sessions in flight at once must never see each other's decision state.

    ``test_decisions_for_invocation_never_leak_across_invocations`` proves
    isolation sequentially (one invocation fully finishes, then the next
    starts). This test proves the stronger, real-world property: with two
    ``AcsOutputMiddleware.process()`` calls genuinely interleaved on the
    *same* middleware instance via ``asyncio.gather`` (mirroring two
    concurrent hosted-agent sessions sharing one process), the per-invocation
    contextvar binding -- not any attribute on the middleware instance, and
    not a single "last case" global -- keeps each invocation's evidence
    scoped to itself. A case with local remediation must stay ticket-free
    even while another concurrent invocation is creating a ticket for a
    different, unresolved case.
    """
    reset_mock_tickets()
    middleware = AcsOutputMiddleware()

    async def resolved_case():
        _, _, _, kb = await diagnostic_flow("token-expired-signin")
        assert kb["evidence_reference"]
        return SimpleNamespace(text="Your sign-in token will refresh automatically.")

    async def unresolved_case():
        await diagnostic_flow("locked-signin")
        return SimpleNamespace(text="No local remediation was found.")

    resolved_context, unresolved_context = await asyncio.gather(
        run_output(middleware, resolved_case),
        run_output(middleware, unresolved_case),
    )

    assert resolved_context.result is not None
    assert unresolved_context.result is not None
    tickets = mock_tickets()
    assert len(tickets) == 1
    assert tickets[0]["case_id"] == "locked-signin"


@pytest.mark.asyncio
async def test_concurrent_invocations_for_same_case_create_only_one_ticket():
    """Two concurrent sessions escalating the same case must not double-create.

    Simulates two users (or two retries) hitting the exact same unresolved
    case at the same moment. Even though each invocation independently
    observes "no ticket yet" before either one finishes creating it, the
    result must still be exactly one ticket: the host-side pre-check in
    ``_create_missing_tickets`` plus the tool's own idempotency guard
    (``tools._create_escalation_ticket`` returns the existing ticket for a
    known ``case_id`` instead of appending a second one) together make
    concurrent escalation of the same case safe.
    """
    reset_mock_tickets()
    middleware = AcsOutputMiddleware()

    async def unresolved_case():
        await diagnostic_flow("locked-signin")
        return SimpleNamespace(text="No local remediation was found.")

    first_context, second_context = await asyncio.gather(
        run_output(middleware, unresolved_case),
        run_output(middleware, unresolved_case),
    )

    assert first_context.result is not None
    assert second_context.result is not None
    tickets = mock_tickets()
    assert len(tickets) == 1
    assert tickets[0]["case_id"] == "locked-signin"
