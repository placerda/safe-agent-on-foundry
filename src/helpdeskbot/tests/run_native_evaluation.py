"""Run hosted-target evaluations serially; never substitute captured answers.

The deployment has capacity 10. One sample per run prevents the service from
starting all diagnostic conversations simultaneously. Every result is retained.
"""
import argparse
import json
from pathlib import Path
import time

def redact(value):
    """Redact evidence even when a tool result is an encoded JSON string."""
    from evaluation.assert_suite.target import _safe_value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return value
        if isinstance(decoded, (dict, list)):
            return json.dumps(redact(decoded))
        return value
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key == "evidence_reference" or key.endswith("_evidence_reference"):
                result[key] = (item if isinstance(item, str) and item.startswith("token:")
                               else _safe_value({key: item})[key])
            else:
                result[key] = redact(item)
        return result
    return value


def main():
    from azure.ai.projects import AIProjectClient
    from azure.identity import AzureCliCredential

    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--agent", default="helpdeskbot")
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=6, choices=range(1, 7))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    queries = [
        json.loads(line)["query"] for line in
        Path("src/helpdeskbot/tests/queries.jsonl").read_text(encoding="utf-8").splitlines()
    ][:args.limit]
    with AzureCliCredential(tenant_id=args.tenant) as credential:
        with AIProjectClient(endpoint=args.endpoint, credential=credential) as project:
            client = project.get_openai_client()
            definition = client.evals.create(
                name=f"{args.agent}-v{args.version}-serial-native",
                data_source_config={"type": "custom", "include_sample_schema": True,
                    "item_schema": {"type": "object", "properties": {
                        "query": {"type": "string"}}, "required": ["query"]}},
                testing_criteria=[{
                    "type": "azure_ai_evaluator", "name": name,
                    "evaluator_name": f"builtin.{name}",
                    "initialization_parameters": {
                        "deployment_name": "gpt-5.4-mini", "model": "gpt-5.4-mini"},
                    "data_mapping": {"query": "{{item.query}}",
                        "response": "{{sample.output_items}}",
                        "tool_calls": "{{sample.tool_calls}}",
                        "tool_definitions": "{{sample.tool_definitions}}"},
                } for name in ("intent_resolution", "task_adherence")],
            )
            (args.output / "definition.json").write_text(
                definition.model_dump_json(indent=2), encoding="utf-8")
            for index, query in enumerate(queries):
                run = client.evals.runs.create(eval_id=definition.id,
                    name=f"serial-native-{index + 1}",
                    data_source={"type": "azure_ai_target_completions",
                        "source": {"type": "file_content", "content": [{"query": query}]},
                        "input_messages": {"type": "template", "template": [
                            {"role": "user", "content": "{{item.query}}", "type": "message"}]},
                        "target": {"type": "azure_ai_agent", "name": args.agent,
                            "version": args.version, "tool_descriptions": []}})
                print(json.dumps({"eval_id": definition.id, "run_id": run.id}), flush=True)
                deadline = time.monotonic() + 1200
                while run.status in ("queued", "in_progress"):
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"Native evaluation {run.id} timed out")
                    time.sleep(20)
                    run = client.evals.runs.retrieve(eval_id=definition.id, run_id=run.id)
                (args.output / f"run-{index + 1}.json").write_text(
                    run.model_dump_json(indent=2), encoding="utf-8")
                items = list(client.evals.runs.output_items.list(
                    eval_id=definition.id, run_id=run.id))
                # Results may contain host evidence. Redact recursively before storage.
                safe = redact([item.model_dump(mode="json") for item in items])
                (args.output / f"items-{index + 1}.json").write_text(
                    json.dumps(safe, indent=2), encoding="utf-8")
                from evaluation.assert_suite.trajectory import from_native, validate
                import re
                if run.status != "completed" or len(safe) != 1 or run.result_counts.errored:
                    raise RuntimeError("Native evaluation did not complete one sample")
                steps, answer = from_native(safe[0]["sample"])
                case = re.search(r"DEMO_CASE:\s*([a-z-]+)", query).group(1)
                findings = validate(steps, case)
                (args.output / f"trajectory-{index + 1}.json").write_text(json.dumps({
                    "native_sample": True, "assistant_text_present": bool(answer),
                    "tool_count": len(steps), "findings": findings,
                    "signature_verification": False,
                }, indent=2), encoding="utf-8")
                if any(findings.values()):
                    raise RuntimeError("Native trajectory violates deterministic contract")
                print(json.dumps({"sample": index + 1, "status": run.status,
                    "counts": run.result_counts.model_dump()}), flush=True)
                if index + 1 < len(queries):
                    time.sleep(45)


if __name__ == "__main__":
    main()
