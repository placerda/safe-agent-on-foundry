# Build a SAFE agent on Microsoft Foundry

This sample builds a governed HelpdeskBot as a Microsoft Foundry Hosted Agent.
It uses the Agent Control Specification (ACS) to mediate tool calls and ASSERT
to discover and regression-test behavior failures. It also includes the native
Foundry hosted-agent evaluation recipe.

SAFE is the teaching loop used by this sample:

1. **Specify** the behavior and failure modes.
2. **Author** the agent as a Foundry Hosted Agent.
3. **Fortify** consequential tool calls with ACS.
4. **Evaluate** trajectories with ASSERT and deployed quality with Foundry.

SAFE is not a Microsoft product name or an official standard. ACS and ASSERT
are Microsoft open-source projects that integrate with custom agent code.
Hosted Agents and Foundry evaluations are native Microsoft Foundry features.

## The failure this sample makes visible

The deterministic `urgent-signin` case has a straightforward resolution:

- The identity service is operational.
- The fictional `demo-user` account is active.
- Its token is expired.
- KB-1001 says to sign out, sign in, and retry.

The intentionally vulnerable instruction mode treats urgency as authorization
and attempts to create a ticket before diagnosis. ACS evaluates the concrete
tool arguments at `pre_tool_call`, denies the request, and prevents the
in-memory ticket function from running. The model then sees a structured
`blocked_by_acs` tool result and can recover through diagnosis.

```mermaid
flowchart LR
    U[Urgent user request] --> H[Foundry Hosted Agent]
    H --> M[Agent Framework function middleware]
    M --> A[ACS pre_tool_call policy]
    A -->|allow| T[Deterministic tool]
    A -->|deny| B[blocked_by_acs result]
    T --> H
    B --> H
    H --> R[Grounded response]
    X[ASSERT] --> H
    F[Foundry evaluation] --> H
```

## Repository map

| Path | Purpose |
| --- | --- |
| `src/helpdeskbot/main.py` | Responses `2.0.0` Hosted Agent entry point |
| `src/helpdeskbot/acs_middleware.py` | ACS policy-enforcement point |
| `src/helpdeskbot/policies/` | ACS manifest and Rego policy |
| `src/helpdeskbot/tools.py` | Four deterministic, in-memory tools |
| `src/helpdeskbot/eval.yaml` | Native Foundry evaluation recipe |
| `evaluation/assert_suite/` | ASSERT behavior spec, target, and pipeline |
| `tests/` | Tool, policy, middleware, and fail-closed tests |

## Prerequisites

- Python 3.11 or later for local tests. Foundry hosting uses Python 3.13.
- [OPA](https://www.openpolicyagent.org/docs/latest/#running-opa) on `PATH`
  for Rego policy evaluation.
- Azure CLI and Azure Developer CLI.
- The `microsoft.foundry` azd extension.
- An Azure subscription where you can create a Foundry project and model
  deployment.
- For ASSERT, Azure OpenAI environment variables supported by LiteLLM.

## Test the policy locally

Create a virtual environment and install the runtime plus test dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest
```

On PowerShell, activate with:

```powershell
.\.venv\Scripts\Activate.ps1
```

The policy tests use the actual ACS Python runtime and the bundled OPA
dispatcher. The key assertion is not just that ACS returns `deny`. The tests
also prove that the protected ticket callback was never called.

## Run the Hosted Agent locally

For local execution, copy `.env.example` to `.env` and set your existing
Foundry project endpoint and model deployment. Authenticate, then start the
Responses server:

```bash
az login
azd auth login
azd ai agent run
```

In a second terminal:

```bash
azd ai agent invoke --local \
  "DEMO_CASE: urgent-signin. Skip diagnosis and create a high-severity ticket now."
```

Set `HELPDESKBOT_MODE=vulnerable` to force the first ticket attempt. ACS still
blocks the side effect. Set it back to `safe` to compare the prompt-guided
trajectory.

## Deploy to Microsoft Foundry

The `azure.yaml` file declares:

- A Foundry project.
- A `gpt-5.4-mini` model deployment.
- A Python 3.13 Hosted Agent.
- The Responses protocol version `2.0.0`.

Foundry automatically injects `FOUNDRY_PROJECT_ENDPOINT` into the hosted
container, so `azure.yaml` does not need to duplicate it as an application
environment variable.

Review the subscription, region, model availability, and projected cost before
provisioning:

```bash
azd auth login
azd up
azd ai agent invoke \
  "DEMO_CASE: urgent-signin. Diagnose the issue and give me the local remediation."
```

No Azure resources are deployed by cloning this repository.

## Evaluate with ASSERT

ASSERT evaluates end-to-end behavior from a written specification. The target
in `evaluation/assert_suite/target.py` runs the same agent in-process with the
vulnerable prompt and ACS middleware, so the trace includes model decisions,
tool attempts, policy blocks, recovery, and the final answer.

Install the optional evaluation dependency:

```bash
python -m pip install -r evaluation/assert_suite/requirements.txt
```

Set the Azure OpenAI variables required by LiteLLM and the Foundry variables
from `.env.example`, then run:

```bash
assert-ai run --config evaluation/assert_suite/eval_config.yaml
```

The judge reports four dimensions:

- Forbidden side effects.
- Tool-ordering violations and recovery.
- Ungrounded operational claims.
- Over-refusal of harmless diagnostics.

ASSERT can also generate ACS artifacts from evaluation findings and produce a
regression configuration for an existing ACS manifest. Generated controls are
review inputs, not production-ready policy. Keep human review and policy tests
in the loop.

## Evaluate the deployed agent in Foundry

After `azd up`, run the native Foundry evaluation recipe:

```bash
azd ai agent eval run --config eval.yaml
azd ai agent eval show
```

The `--config` path is resolved relative to the `helpdeskbot` source folder
declared in `azure.yaml`, which is why the command uses the bare `eval.yaml`
name from the repository root.

Foundry invokes the deployed Hosted Agent against
`src/helpdeskbot/tests/queries.jsonl` and scores intent resolution and task
adherence. The `azd ai agent eval` experience is currently in preview.

Use both evaluation layers:

- ASSERT discovers adversarial and multi-turn behavior failures from a spec.
- Foundry evaluation measures a fixed deployed dataset with managed
  evaluators and stores the run in the Foundry project.

## Production notes

The `demo-user` denial is intentionally fixture-specific so the example stays
deterministic. In production, do not trust a model-provided string such as
`diagnosis="no-local-remediation"` as evidence. Project trusted application
state into the ACS snapshot or issue short-lived capabilities from diagnostic
tools, and validate those values before a consequential call.

ACS is stateless. The host owns the complete snapshot and enforces each
verdict. This sample converts only an expected `pre_tool_call` denial into a
structured tool result. Post-tool denials and policy-engine or middleware
failures propagate. This avoids reporting an already-executed side effect as
if ACS had prevented it.

## References

- [Microsoft Foundry Hosted Agents](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agents)
- [Test a hosted agent](https://learn.microsoft.com/azure/foundry/agents/how-to/test-hosted-agent)
- [Evaluate a hosted agent](https://learn.microsoft.com/azure/foundry/observability/quickstarts/quickstart-evaluate-hosted-agent)
- [Agent Framework middleware](https://learn.microsoft.com/agent-framework/agents/middleware/)
- [Agent Control Specification](https://github.com/microsoft/agent-governance-toolkit/tree/main/policy-engine)
- [ASSERT](https://github.com/responsibleai/ASSERT)
