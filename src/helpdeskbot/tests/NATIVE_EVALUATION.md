# Paced native hosted-target evaluation

Run from the repository root with `PYTHONPATH` including that root. Use the
official `azure-ai-projects==2.3.0`, `openai==3.8.0`, `azure-identity` and `httpx`
packages. Authentication uses the existing Azure CLI account; no token is
printed or persisted.

```powershell
$env:PYTHONPATH = (Get-Location).Path
uv run --with azure-ai-projects==2.3.0 --with openai==3.8.0 --with azure-identity --with httpx python src\helpdeskbot\tests\run_native_evaluation.py --endpoint <project-endpoint> --tenant <tenant-id> --version <deployed-version> --output src\helpdeskbot\.foundry\results\<new-run>
```

This executes the six unchanged `queries.jsonl` queries using the **native
`azure_ai_target_completions` / `azure_ai_agent` source**, not captured answers.
Each query gets its own run; only one run is active, and there is a 45-second
gap after completion before the next starts. This accommodates the existing
capacity-10 model without increasing capacity or retrying failed samples.
The output directory must be new, preserving old failed evaluations.

The runner stores the real native role/content transcript, redacts evidence
references (including encoded JSON), and checks complete tool pairs and final
assistant text. The deterministic fixture/trajectory findings are separate
from generic intent-resolution and task-adherence judgments. Forbidden user
requests may legitimately fail these generic quality metrics.

To recheck saved native results without invoking the deployment or a judge:

```powershell
python src\helpdeskbot\tests\verify_native_results.py src\helpdeskbot\.foundry\results\<run>
```

Missing/empty native samples, missing final text, unpaired tool events, missing
files and unexpected deterministic findings fail loudly. A successful
deterministic check is not a claim of cryptographic verification from the
redacted transcript or of successful generic quality scores.

The deployed instance must have `Monitoring Metrics Publisher` on its existing
Application Insights component. Agent recreation changes the instance
principal; an assignment to an old principal does not authorize the new one.
Verify the current version's instance identity and exact component scope,
obtain authorization before any IAM change, and confirm ingested policy spans.
Do not grant resource-group roles or switch to key-based telemetry to work
around a publishing 403. Avoid printing raw `azd ai agent show` output because
the response includes environment-variable values.
