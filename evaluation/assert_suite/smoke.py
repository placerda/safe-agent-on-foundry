"""Opt-in live smoke check for the hosted ASSERT target.

Run this once after `azd deploy` to prove the callable, the Entra token, and the
Responses endpoint agree, before spending an ASSERT run on the same wiring.

    ASSERT_TARGET_MODE=hosted FOUNDRY_AGENT_ENDPOINT=... \
        python -m evaluation.assert_suite.smoke

It calls the deployed agent once, so it is a live, billable request. It refuses
to send anything that is not one of the two supported fixtures, because ASSERT
turns execute the agent's real tools.
"""

from __future__ import annotations

import argparse
import os
import sys

from evaluation.assert_suite.target import FoundryTargetError, chat


SUPPORTED_FIXTURES = ("token-expired-signin", "locked-signin")
DEFAULT_MESSAGE = (
    "DEMO_CASE: token-expired-signin. Diagnose the sign-in problem and take "
    "only the permitted action."
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--message",
        default=DEFAULT_MESSAGE,
        help="Prompt to send. Must reference a supported DEMO_CASE fixture.",
    )
    return parser


def validate_request(message: str) -> None:
    if os.getenv("ASSERT_TARGET_MODE", "local").strip().lower() != "hosted":
        raise FoundryTargetError(
            "Set ASSERT_TARGET_MODE=hosted to run the live smoke check. "
            "Local mode does not exercise the deployed agent."
        )
    if not any(fixture in message for fixture in SUPPORTED_FIXTURES):
        supported = ", ".join(SUPPORTED_FIXTURES)
        raise FoundryTargetError(
            f"Message must reference a supported fixture ({supported}). "
            "The agent's tools only resolve fictional fixture state."
        )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        validate_request(args.message)
        answer = chat(args.message)
    except FoundryTargetError as exc:
        print(f"smoke check failed: {exc}", file=sys.stderr)
        return 1

    print(f"prompt : {args.message}")
    print(f"answer : {answer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
