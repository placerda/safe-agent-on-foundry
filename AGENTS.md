# Contributor guidance

- Keep the four tools deterministic and free of network, file, and subprocess
  side effects.
- Preserve the deliberate safe and vulnerable instruction modes. They create
  the controlled comparison used by the article and evaluations.
- Preserve explicit coverage of Scope, Anchored Decisions, Flow Integrity, and
  Escalation in code, policies, tests, ASSERT, and Foundry datasets.
- Never replace signed host evidence with a model-authored diagnosis string.
- ACS must mediate tool execution before `call_next()`. A deny verdict must
  never execute the underlying tool.
- Runtime or policy-engine failures must propagate. Do not convert them into
  successful tool results.
- Keep Foundry evaluation assets in `src/helpdeskbot/` because
  `azd ai agent eval` resolves configuration relative to the agent source.
- Keep ASSERT assets in `evaluation/assert_suite/`.
- Run `python -m pytest` before proposing changes.
