# Original article: immutable source links

This is the preservation inventory for
[Build a SAFE agent on Microsoft Foundry](https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/build-a-safe-agent-on-microsoft-foundry/4547570).
The inspected published body contains **33 distinct repository links, 34 link
occurrences, and 28 code blocks**. The whole middleware file is linked twice.
These corrections preserve the ACS 0.3 tutorial; they do not describe an ACS 0.4
migration.

Apply the replacements to the published article before merging a migration into
`main`. All targets below use the verified immutable baseline commit. Six
middleware anchors also require line-range changes, rather than merely replacing
`main` with a tag or SHA.

In the first column, paths are relative to
`https://github.com/placerda/safe-agent-on-foundry`.

| Published link suffix | Correct immutable target |
| --- | --- |
| Repository root | [Baseline tree](https://github.com/placerda/safe-agent-on-foundry/tree/f9d2a55954d447554907686d59135489c393e826) |
| `/tree/main` | [Baseline tree](https://github.com/placerda/safe-agent-on-foundry/tree/f9d2a55954d447554907686d59135489c393e826) |
| `/blob/main/README.md` | [README](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/README.md) |
| `/blob/main/azure.yaml` | [Azure configuration](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/azure.yaml) |
| `/blob/main/src/helpdeskbot/main.py` | [Agent entry point](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/src/helpdeskbot/main.py) |
| `/blob/main/src/helpdeskbot/main.py#L18-L30` | [Agent registration, lines 18-30](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/src/helpdeskbot/main.py#L18-L30) |
| `/blob/main/src/helpdeskbot/acs_middleware.py` (two occurrences) | [Custom ACS middleware](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/src/helpdeskbot/acs_middleware.py) |
| `/blob/main/src/helpdeskbot/acs_middleware.py#L236-L238` | [Tool proposal and verified evidence, lines 259-261](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/src/helpdeskbot/acs_middleware.py#L259-L261) |
| `/blob/main/src/helpdeskbot/acs_middleware.py#L240-L255` | [Protected callback, lines 263-278](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/src/helpdeskbot/acs_middleware.py#L263-L278) |
| `/blob/main/src/helpdeskbot/acs_middleware.py#L274-L283` | [Guarded tool execution, lines 297-306](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/src/helpdeskbot/acs_middleware.py#L297-L306) |
| `/blob/main/src/helpdeskbot/acs_middleware.py#L440-L446` | [Output policy evaluation, lines 463-469](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/src/helpdeskbot/acs_middleware.py#L463-L469) |
| `/blob/main/src/helpdeskbot/acs_middleware.py#L502-L507` | [Host-owned handoff arguments, lines 525-530](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/src/helpdeskbot/acs_middleware.py#L525-L530) |
| `/blob/main/src/helpdeskbot/acs_middleware.py#L535-L540` | [Guarded host handoff, lines 558-563](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/src/helpdeskbot/acs_middleware.py#L558-L563) |
| `/blob/main/src/helpdeskbot/evidence.py#L63-L82` | [Expected diagnostic trajectory, lines 63-82](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/src/helpdeskbot/evidence.py#L63-L82) |
| `/blob/main/src/helpdeskbot/evidence.py#L152-L161` | [Signed evidence fields, lines 152-161](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/src/helpdeskbot/evidence.py#L152-L161) |
| `/blob/main/src/helpdeskbot/evidence.py#L169-L174` | [Evidence reference publication, lines 169-174](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/src/helpdeskbot/evidence.py#L169-L174) |
| `/blob/main/src/helpdeskbot/evidence.py#L373-L378` | [Exact trajectory verification, lines 373-378](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/src/helpdeskbot/evidence.py#L373-L378) |
| `/blob/main/src/helpdeskbot/tools.py#L43-L45` | [Account tool signature, lines 43-45](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/src/helpdeskbot/tools.py#L43-L45) |
| `/blob/main/src/helpdeskbot/tools.py#L82-L87` | [Ticket tool signature, lines 82-87](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/src/helpdeskbot/tools.py#L82-L87) |
| `/blob/main/src/helpdeskbot/policies/manifest.yaml` | [ACS 0.3 manifest](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/src/helpdeskbot/policies/manifest.yaml) |
| `/blob/main/src/helpdeskbot/policies/helpdesk.rego` | [SAFE policy](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/src/helpdeskbot/policies/helpdesk.rego) |
| `/blob/main/src/helpdeskbot/policies/helpdesk.rego#L13-L21` | [Trusted policy input, lines 13-21](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/src/helpdeskbot/policies/helpdesk.rego#L13-L21) |
| `/blob/main/src/helpdeskbot/policies/helpdesk.rego#L23-L44` | [Scope rules, lines 23-44](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/src/helpdeskbot/policies/helpdesk.rego#L23-L44) |
| `/blob/main/src/helpdeskbot/policies/helpdesk.rego#L80-L86` | [Scope denial, lines 80-86](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/src/helpdeskbot/policies/helpdesk.rego#L80-L86) |
| `/blob/main/src/helpdeskbot/policies/helpdesk.rego#L108-L115` | [Unanchored denial, lines 108-115](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/src/helpdeskbot/policies/helpdesk.rego#L108-L115) |
| `/blob/main/src/helpdeskbot/policies/helpdesk.rego#L63-L72` | [Exact escalation trajectory, lines 63-72](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/src/helpdeskbot/policies/helpdesk.rego#L63-L72) |
| `/blob/main/src/helpdeskbot/policies/helpdesk.rego#L124-L135` | [Local-remediation denial, lines 124-135](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/src/helpdeskbot/policies/helpdesk.rego#L124-L135) |
| `/blob/main/src/helpdeskbot/policies/helpdesk.rego#L146-L166` | [Mandatory handoff output gate, lines 146-166](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/src/helpdeskbot/policies/helpdesk.rego#L146-L166) |
| `/blob/main/scripts/prepare_opa.py` | [Pinned OPA preparation](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/scripts/prepare_opa.py) |
| `/blob/main/scripts/show_safe_controls.py` | [Verdict demonstration](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/scripts/show_safe_controls.py) |
| `/tree/main/evaluation/assert_suite` | [ASSERT assets](https://github.com/placerda/safe-agent-on-foundry/tree/f9d2a55954d447554907686d59135489c393e826/evaluation/assert_suite) |
| `/blob/main/evaluation/assert_suite/behavior.md` | [ASSERT behavior contract](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/evaluation/assert_suite/behavior.md) |
| `/blob/main/evaluation/assert_suite/behavior.md#L36-L39` | [Required trajectory, lines 36-39](https://github.com/placerda/safe-agent-on-foundry/blob/f9d2a55954d447554907686d59135489c393e826/evaluation/assert_suite/behavior.md#L36-L39) |

## Version notice and checkout correction

Add this notice to the original article:

> This tutorial uses the preserved ACS 0.3 implementation at commit
> `f9d2a55954d447554907686d59135489c393e826` (`acs-0.3-baseline`).
> Follow its versioned source links and checkout instructions rather than the
> repository's moving `main` branch. The ACS 0.3 middleware and manifest shown
> here must not be mixed with native Agent Hooks examples or dependencies.

Replace the original unversioned clone and directory commands with:

```bash
git clone --branch acs-0.3-baseline \
  https://github.com/placerda/safe-agent-on-foundry safe-agent-on-foundry
cd safe-agent-on-foundry
git rev-parse HEAD
```

The printed commit must be
`f9d2a55954d447554907686d59135489c393e826`.
Use the [verified baseline environment](acs-0.3-baseline.md) to reproduce offline
results. Authentication, provisioning, and deliberate vulnerable-mode deployment
are manual operations for an isolated test environment, not instructions to
overwrite an existing shared service.

## Code-block inventory

All 28 blocks in the inspected original article are accounted for below.
This inventory distinguishes conceptual trajectories and file trees from
executable source excerpts.

| Blocks | Article section | Content |
| --- | --- | --- |
| 1-4 | Enforce ACS decisions with Agent Framework middleware | Agent registration; verified tool inputs; protected callback; `run_tool` |
| 5-6 | Implement each SAFE principle | Repository tree; ACS 0.3 policy input |
| 7-9 | Scope | Tool signatures; permitted authority; scope denial |
| 10-12 | Anchored Decisions | Evidence fields; reference registry; unanchored denial |
| 13-16 | Flow Integrity | Conceptual trajectory; expected evidence; host verification; Rego sequence |
| 17-20 | Escalation | Local-remediation denial; output evaluation; missing-ticket gate; guarded host repair |
| 21-22 | Deploy and prove the SAFE controls | Checkout/authentication/setup; deployment |
| 23-25 | Run the two expected outcomes | Local case invocation; conceptual local trajectory; locked case invocation |
| 26-28 | Prove that policy wins over the prompt | Vulnerable mode; pressure prompt; restoration to safe mode |

The SAFE concepts, two fictional cases, intended outcomes, and ASSERT trajectory
remain applicable. An ACS 0.4 article must replace integration-specific prose and
snippets separately; these preserved links must not be relabeled as migrated
source.
