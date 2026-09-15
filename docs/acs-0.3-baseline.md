# Preserved ACS 0.3 tutorial

The original [SAFE article](https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/build-a-safe-agent-on-microsoft-foundry/4547570)
describes the ACS 0.3 integration, not the native Agent Hooks integration.
Its immutable source baseline is
[`f9d2a55954d447554907686d59135489c393e826`](https://github.com/placerda/safe-agent-on-foundry/tree/f9d2a55954d447554907686d59135489c393e826).
The annotated `acs-0.3-baseline` tag points to that commit. Use the full commit
in published source links; a tag alone does not repair links to `main`.

[Article link corrections](article-baseline-links.md) inventory every repository
link in the published article, including the displaced middleware line ranges.
Publishing these corrections remains the author's responsibility.

## Verified environment

| Component | Baseline verification |
| --- | --- |
| Platform | Linux x86_64 |
| Python | 3.13.14 |
| Container | `python:3.13-slim` |
| Container digest | `sha256:9662417aace5ae7b8e2609cce472b72a8958e134ba372808abe9cc1a0c0125e6` |
| ACS package | `agent-control-specification==0.3.1b0` |
| Agent Framework core | `agent-framework-core==1.17.0` |
| Foundry client | `agent-framework-foundry==1.10.4` |
| Foundry hosting | `agent-framework-foundry-hosting==1.0.0b260730` |
| OPA | 1.18.2, Linux static binary |
| OPA SHA256 | `9903e5125ac281104f2c4b7371d10cc3b74a98933743fcbfc174f9bf0ab20de8` |

The complete tested dependency snapshot is
[`baseline-requirements.txt`](baseline-requirements.txt). It includes development
dependencies and is for reproducing this baseline only. It is not the migrated
agent's dependency set, a production security recommendation, or a claim that
all historical CI runs used these transitive versions.

The snapshot was resolved afresh and then verified against the immutable source.
The [original successful CI run](https://github.com/placerda/safe-agent-on-foundry/actions/runs/31743806398)
used Agent Framework core 1.13.0. The fresh verification used 1.17.0. Both facts
are distinct from the direct dependency pins in the original source.

## Reproduce without changing a working checkout

Copy `docs/baseline-requirements.txt` from this checkout to a separate location
before checking out the historical source: the historical commit intentionally
does not contain this newer preservation document or snapshot.

Use a disposable Linux Python 3.13 environment. Native ACS 0.3 installation on
Windows may require a Rust/MSVC build toolchain; a Linux container avoids that
unverified native-build path. The following commands are for a Linux shell:

```bash
git clone --branch acs-0.3-baseline \
  https://github.com/placerda/safe-agent-on-foundry safe-agent-acs03
cd safe-agent-acs03
test "$(git rev-parse HEAD)" = f9d2a55954d447554907686d59135489c393e826
python -m venv .venv
. .venv/bin/activate
python -m pip install --pre --only-binary=:all: \
  -r ../baseline-requirements.txt
python scripts/prepare_opa.py
chmod +x src/helpdeskbot/opa
export PATH="$PWD/src/helpdeskbot:$PATH"
python -m pip check
BASELINE_TEST_ROOT="$(mktemp -d)"
python -m pytest --basetemp "$BASELINE_TEST_ROOT/pytest"
python scripts/show_safe_controls.py
```

Place the copied snapshot at `../baseline-requirements.txt` for these commands.
The original `prepare_opa.py` downloads the pinned binary and verifies its
checksum. Do not disable TLS verification to work around network restrictions.
If the container cannot download packages, stage the same Linux wheels through
an approved package connection on the host and mount that directory read-only.

An advisory check of the snapshot reported
[CVE-2025-71176](https://github.com/advisories/GHSA-6w46-j5rx-g56g) in the historical
`pytest==8.4.2` development dependency, fixed in 9.0.3. The preserved snapshot does
not silently upgrade it. Use an isolated environment and the explicit private
`--basetemp` parent above, not a predictable directory in a shared temporary root.
Keep the test directory outside the checkout: configuration-discovery fixtures
must not inherit the repository's `azure.yaml` through their parent directories.
Candidate development dependencies must be audited separately.

## Observed baseline behavior

The immutable source passed **116 tests**, `pip check`, and the deterministic
SAFE verdict demonstration in the environment above.

| Scenario | Observed verdict |
| --- | --- |
| Out-of-scope authority | `deny: scope_boundary`; callback not executed |
| Unanchored ticket | `deny: unanchored_decision`; callback not executed |
| Invalid diagnostic order | `deny: flow_integrity_violation`; callback not executed |
| Local remediation available | `deny: local_remediation_available`; callback not executed |
| Valid mandatory handoff | Allowed; callback executed |
| Output missing required handoff | `deny: missing_escalation_ticket` |
| Output after completed handoff | Allowed |

These are offline baseline results, not evidence of a hosted migration or a
successful Azure evaluation. Preserve both deliberate instruction modes and all
four SAFE dimensions when comparing a candidate against this baseline.
