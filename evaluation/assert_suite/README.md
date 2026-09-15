# ASSERT suite

[ASSERT](https://github.com/responsibleai/ASSERT) generates adversarial cases,
drives multi-turn conversations against a target, and judges the resulting
trajectory. Microsoft
[released it as open source](https://commandline.microsoft.com/assert-written-intent-executable-evals/),
and it is explicitly not tied to Microsoft Foundry: there is no portal
integration, no Foundry SDK surface, and no Microsoft-hosted ASSERT service. It
is also beta, and a passing run is evidence about the cases you ran, not a
compliance certification.

This suite judges HelpdeskBot against the four SAFE principles.

| File | Purpose |
| --- | --- |
| `behavior.md` | Prose behavior spec for the systematize stage |
| `eval_config.yaml` | Suite, behavior, target, and judge dimensions |
| `taxonomy.json` | Reviewed SAFE judgment contract, independent of generated taxonomy |
| `test_set.jsonl` | Checked-in adversarial cases, one fixture per case |
| `target.py` | Callable target: local in-process or deployed Hosted Agent |
| `smoke.py` | Opt-in one-shot live check of the hosted target |
| `requirements.txt` | Pinned ASSERT revision plus the target's own dependencies |

## Why a callable target

ASSERT can target a Foundry agent natively with `target.model:
azure_ai/agents/<id>`, but that path covers the **v1 Assistants** surface.
HelpdeskBot is a custom Hosted Agent on the **Responses (v2)** protocol, so this
suite uses `target.callable`, exactly like the official Foundry hosted-agent
example
([`langgraph-foundry-hosted`](https://github.com/responsibleai/ASSERT/tree/main/examples/langgraph-foundry-hosted)).
`target.py` follows that example's `auto_trace.py`: `httpx` to the deployed
`/responses` endpoint, with `DefaultAzureCredential` for a short-lived Entra
token on the `https://ai.azure.com/.default` scope. No key, no `azd` subprocess,
no token written to a log or a file.

It adds one thing the official example does not need. HelpdeskBot's proof is the
tool trajectory, not the last sentence, so the target reconstructs tool calls and
results from the Responses `output` array and emits them as OpenInference spans.
`target.trace` in `eval_config.yaml` hands those spans to the judge, which is why
the judge can score tool order, arguments, and ACS interventions. Evidence
references are hashed before they reach a span, so a signed token never lands in
an evaluation artifact.

## Why a pinned commit

`requirements.txt` installs ASSERT from a specific commit,
`054797fe9e5b0b204de47e2f32ece6956e0fbf0d`. The only PyPI release is
`assert-ai 0.1.0`, and it predates both the official Foundry hosted-agent example
and the `azure-aad` extra this suite uses for keyless judge and tester calls.
`main` would install a different package next week, so a commit is the only
reproducible option here. Re-pin deliberately when you want newer behavior.

## Target modes

| `ASSERT_TARGET_MODE` | What runs | Cost |
| --- | --- | --- |
| `local` (default) | The guarded agent in this process | Model calls only |
| `hosted` | The deployed Hosted Agent over `/responses` | Live agent and tools |

Hosted mode is opt-in because every ASSERT turn executes the agent's real tools.
Point it only at a fixture-backed or sandbox deployment. HelpdeskBot's tools are
deterministic fictional fixtures, which is what makes a live run safe here.

Local mode replays conversation history as Agent Framework messages but builds a
fresh in-process agent for each callable invocation. Use it for development; use
hosted mode for results you intend to quote. The migrated local runtime needs
Linux/WSL, Python 3.13, and ACS's Regorus wheel; the HTTP-only hosted target does
not require installing the native ACS runtime on the evaluation machine.

`FOUNDRY_AGENT_SESSION_ID` optionally binds requests to an existing hosted
session. Create that session for the exact candidate version; the current
Foundry API pins a version through the session, not by inventing a version
segment in the normal protocol endpoint. A configured but empty session ID is
an error, not permission to fall back to the endpoint's latest version.
When the variable is absent, the existing endpoint-routing behavior is preserved.

A hosted session selects sandbox compute and version affinity. It is not
conversation history. ASSERT still replays each case's explicit history in the
request body; evidence remains scoped to the individual governed invocation.

## Run it

```bash
python -m pip install -r evaluation/assert_suite/requirements.txt
az login

# Evaluator credentials (judge, tester, systematize). Separate from the agent.
export AZURE_API_BASE="https://your-resource.openai.azure.com"
export AZURE_API_VERSION="2025-04-01-preview"
export ASSERT_AZURE_USE_AAD=1

# Create a session bound to the deployed candidate version.
azd ai agent sessions create helpdeskbot "$(azd env get-value AGENT_HELPDESKBOT_VERSION)"

# Target: use the real protocol endpoint printed by `azd deploy`.
export ASSERT_TARGET_MODE=hosted
export FOUNDRY_AGENT_ENDPOINT="$(azd env get-value AGENT_HELPDESKBOT_RESPONSES_ENDPOINT)"
export FOUNDRY_AGENT_SESSION_ID="<agent_session_id returned by sessions create>"

# One live turn to prove endpoint, token, and parsing agree before a full run.
python -m evaluation.assert_suite.smoke

assert-ai run --config evaluation/assert_suite/eval_config.yaml \
  --override "artifacts_root=$(pwd)/evaluation/assert_suite/results" \
  --force-stage inference --strict
```

Record the session's version with the results. Hosted Agent deployments are
immutable, and unpinned routing silently mixes candidate and baseline results.
Use a separate result directory or run ID for each candidate; explicitly rerun
inference so cached trajectories cannot masquerade as a new deployment check.

The offline tests in `tests/test_assert_target.py` and
`tests/test_assert_smoke.py` mock the HTTP call and the token, so `python -m
pytest` exercises the target without an Azure call.

## Failure behavior

The judge uses the checked-in `taxonomy.json`, not the taxonomy synthesized by
the systematize stage. In hosted validation, a generated taxonomy incorrectly
described `locked-signin` as a no-ticket case and misclassified correct
unsupported-case refusals. The curated contract preserves both opposite fixture
outcomes and all four violation dimensions; it does not change the agent,
dataset, dimension rubrics, or failure threshold. Review generated taxonomies
before using them for new suites.

The sandbox calibration still produced a false positive and missed two
synthetic negative controls with the configured judge model. The curated
taxonomy is not a claim of reliable judgment; see the
[recorded results](../../docs/acs-0.4-validation.md). Do not use this LLM score
alone as the automatic merge gate.

Exit code zero means the pipeline completed, not that every score passed.
Inspect every row's `judge_status` and `verdict.dimensions`. Preserve failed
runs and original trajectories. A judge-only replay under a corrected taxonomy
must be labeled as a replay, not as fresh hosted inference, and should include
separate, explicitly synthetic negative controls for the four SAFE dimensions.

The target raises instead of returning a plausible-looking string. An invalid
target mode, a missing `FOUNDRY_AGENT_ENDPOINT`, an explicitly empty session
binding, a non-2xx response, any
Responses status other than `completed`, a tool call or result without its
matching pair, or a response with no assistant text all fail the run. A silent
fallback would turn a broken deployment into a passing evaluation, which is the
one outcome an evaluation must never produce.
