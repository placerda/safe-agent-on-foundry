# ACS 0.4 compatibility decision

Status: **controlled integration redesign authorized on 2026-09-14**.
The original drop-in migration was blocked by the verified API gaps below.
The author subsequently approved redesigning the integration, not reducing the
four SAFE guarantees. Deployment remains gated on the redesigned implementation
passing its regression, failure, concurrency, and Responses checks.

## Authorized redesign

The approved changes to the original implementation approach are:

- Use ACS 0.4's bundled Regorus engine, retaining the independent OPA baseline.
- Perform one policy-guarded mandatory handoff pass before the native final
  output gate. A terminal output deny is never caught and converted to success.
- Place fatal runtime/post-tool classification at a host-owned execution
  boundary, retaining original ACS records and reasons. Tool scheduling may be
  serialized if required to prevent further protected callbacks after failure.
- Give each invocation independent native hook and evidence state. The hosting
  integration may change internally, but must demonstrate correct Responses
  history, multi-turn behavior, and buffered output.

These are explicit integration changes rather than claims that the original
drop-in probes passed. Private framework patches, a second competing policy
executor, model-authored evidence, and fail-open fallbacks remain prohibited.

## Implemented boundary

[`SafeAgent`](../src/helpdeskbot/host_boundary.py) is an ordinary framework
`Agent` subclass, not a `SupportsAgentRun` proxy. It supplies a fresh
`AgentContextBuilder`, `SafeEmitter`, and complete native middleware bundle
through the public per-run `middleware` parameter. The complete bundle comes
first, followed by application-only `HostHandoff`. Default
`ResponsesHostServer` server-managed history remains in use.

`SafeEmitter` specializes the public `InterceptionEmitter.emit` method, calls
the inherited enforcing implementation, and registers one `AcsInterceptor`.
The two framework-specific ACS middleware classes and their `run_tool` /
direct output-evaluation paths are removed. No private framework methods or
alternative policy executor are needed.

Invocation authority exists before the startup event and remains bound through
shutdown. Only the host populates the trusted
`safe.example/host` snapshot extension. A lock serializes each complete
pre-tool/callback/post-tool bracket, and fatal state is latched immediately.
An expected pre-tool policy deny remains recoverable without executing the
callback. Runtime failures and post-tool denies stop the invocation while
retaining the original native ACS record.

Diagnostic evidence is signed with both invocation and tool-call identity.
It remains pending until native post-tool approval; only then can it authorize
the next diagnostic step. Host remediation uses the same native pre/post-tool
path as model-issued calls. Bounded handoff occurs before the sole native output
gate, and no terminal output denial is converted to success. Streaming transport
receives only the buffered approved result after the invocation completes.

The first full migrated regression run passed 143 tests and all deterministic
SAFE demonstration verdicts with real ACS/Regorus, including actual Responses
HTTP/SSE and multi-turn handling. Final validation also checks that repaired
handoff retains all diagnostic and ticket call/result pairs and uses a dependency
resolution matching the normal hosted installer. These final gates and real
hosted evaluation are distinct from the historical compatibility probes below.

## Policy engine

Use Regorus for the authorized ACS 0.4 redesign, rather than inventing an OPA
adapter or maintaining a custom ACS wheel. The original OPA implementation
remains independently reproducible. Deployment of the redesign is separately
authorized in the existing sandbox project; no infrastructure change is needed.

The published `agent-control-spec==0.4.0a3` Python binding does not expose an
OPA selector. At upstream commit
[`5ea8c0fba7849a8134675efbb49ac337ee9e53b3`](https://github.com/responsibleai/agent-control-spec/tree/5ea8c0fba7849a8134675efbb49ac337ee9e53b3):

- [`engine/Cargo.toml`](https://github.com/responsibleai/agent-control-spec/blob/5ea8c0fba7849a8134675efbb49ac337ee9e53b3/engine/Cargo.toml)
  enables Regorus and Cedar by default.
- [`sdk/python/Cargo.toml`](https://github.com/responsibleai/agent-control-spec/blob/5ea8c0fba7849a8134675efbb49ac337ee9e53b3/sdk/python/Cargo.toml)
  retains those defaults and does not enable the OPA-only configuration.
- [`BindingPolicyDispatcher`](https://github.com/responsibleai/agent-control-spec/blob/5ea8c0fba7849a8134675efbb49ac337ee9e53b3/engine/src/dispatchers/binding.rs)
  selects OPA only when `rego` is disabled and `opa` is enabled.
- [`AcsInterceptor`](https://github.com/responsibleai/agent-control-spec/blob/5ea8c0fba7849a8134675efbb49ac337ee9e53b3/sdk/python/agent_control_spec/__init__.py)
  exposes a generic custom dispatcher hook, not a supported Python OPA adapter.

Rust OPA support therefore does not establish support in the published Python
wheel. Preserve the [ACS 0.3 + OPA baseline](acs-0.3-baseline.md) independently.
Regorus adoption requires equivalent SAFE verdicts, denial precedence, and
failure behavior; an import test is not that proof.

## Executed compatibility probe

This exact combination imported, passed `pip check`, and executed real
`AcsInterceptor` plus native Agent Framework hook scenarios:

| Distribution | Verified version |
| --- | --- |
| `agent-control-spec` | `0.4.0a3` |
| `agent-hooks-sdk` | `0.1.0a5` |
| `agent-framework-core` | `1.17.0` |
| `agent-framework-foundry` | `1.12.0` |
| `agent-framework-foundry-hosting` | `1.0.0b260903` |

The execution environment was Linux x86_64, Python 3.13.14, glibc 2.41.
ACS's published wheel requires a compatible manylinux 2.34 environment.
Windows source installation failed without the MSVC linker; Windows native
runtime compatibility is not established.

The initial probe used ACS's built-in literal test policies, not migrated SAFE
Rego. It registered one complete native middleware bundle with an explicitly
supplied emitter and builder. Its eight observed points were:
`agent_startup`, `input`, `pre_model_call`, `post_model_call`, `pre_tool_call`,
`post_tool_call`, `output`, and `agent_shutdown`.

Observed results included allowed callbacks executing once, pre-tool denial
executing no callback, output denial returning no answer, and host-originated
handoff guarded by the same emitter. The candidate runtime dependency resolution
had no known advisories in the performed `pip-audit` check; that result does not
cover dependencies added later or establish behavioral safety.

ACS is alpha, and
[Python Agent Hooks is experimental](https://learn.microsoft.com/en-us/agent-framework/agents/agent-hooks?pivots=programming-language-python).
Do not present these pins as stable production dependencies.

## Historical drop-in blockers

The unmodified combination is **not a drop-in SAFE replacement**:

- A genuine ACS evaluation failure becomes a `runtime_error:*` deny. Native
  tool mediation treats it as recoverable and can permit a final model answer.
- Ordinary post-tool denial also permits model recovery. The denied payload is
  hidden, but existing SAFE assertions require fatal propagation.
- Evidence produced inside a protected callback precedes the native post-tool
  verdict. Publishing it as accepted at that point would trust a denied result.
- Inner middleware state does not automatically exist at startup/input or
  remain bound at final output/shutdown.
- A host repair pass before the output check is not proof of the planned
  initial output verdict, bounded remediation, and output re-evaluation.

The redesign must address these through public native-hook and host boundaries.
Do not weaken tests, mask errors as success, fabricate host-error
attribution, rely on swallowed record-sink exceptions for enforcement, or retain
a competing legacy policy executor.

### Completed second-stage probes

Fourteen additional probe assertions executed with real Regorus, real
`AcsInterceptor`, the complete native bundle, and a deterministic model client.
They demonstrated representative SAFE reason codes, zero callbacks for protected
pre-tool denials, pending evidence isolation across overlapping runs, and zero
streaming updates after selected denials. These are passing *probe assertions*,
including assertions reproducing unsafe or unsupported behavior, not a passing
migration regression suite.

The remaining negative probes established three concrete blockers:

| Contract | Executed observation | Required supported capability |
| --- | --- | --- |
| Immediate fatal propagation | A synchronized sibling callback executes after another call's native post-tool denial, before a next-model host guard raises. Preventing the final answer does not restore tool-batch cancellation semantics. | A public tool-verdict disposition hook that raises `MiddlewareFailure` at the native boundary, retaining the original `InterceptionBlocked` record and reason. |
| Per-invocation state with unchanged hosted history | A per-request `SupportsAgentRun` wrapper is rejected by default `ResponsesHostServer` history ownership. Switching to `history_source="agent"` changes that contract. | A public run-scoped emitter/builder factory for a normal Agent, or a hosting factory preserving server-managed history. |
| Initial output evaluation, repair, then re-evaluation | Same-emitter bounded repair before the first native output check works. Native terminal output denial instead aborts without a supported repair/re-submit callback. | A bounded preterminal candidate-output remediation hook followed by fresh native evaluation, or explicit acceptance of a changed repair contract. |

The relevant installed APIs are Agent Framework's
`create_agent_hooks_middleware_from_emitter`, native output emission in
`agent_framework/_agent_hooks.py`, and the default history validation in
`agent_framework_foundry_hosting/_responses.py`.
No private API patch, second policy executor, approval resolver, or fabricated
host-error verdict was introduced to bypass these limitations.

These partial results alone did not justify replacing the existing middleware.
They motivated the controlled redesign authorized above. The implementation
must close the same safety boundaries through its new design; full regression,
Responses HTTP/SSE, final migrated article replacements, and hosted evaluation
remain release gates.

## Hosted validation

Before any deployment, inspect the existing endpoint's actual routing rules.
An endpoint selecting `@latest` can send existing traffic to a newly created
version even if tests later specify a version explicitly. In that case, use an
independently named candidate in the same authorized project or obtain explicit
approval for a routing change. Never treat creating a new version as proof of
isolation.

The author separately authorized deletion of the old `helpdeskbot` deployment.
Deletion was verified with a subsequent 404; the project, model, and resource
group were preserved. The later redesign authorization includes recreating and
validating the application in that same project, after local gates pass. This
does not authorize provisioning infrastructure, changing RBAC, publishing the
article, or merging into `main`.
