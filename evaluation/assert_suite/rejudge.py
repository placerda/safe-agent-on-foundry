"""Auditable judge-only adapter to pinned ASSERT's exported core API.

Uses the same config parser, prompt, transcript formatter, schema and judgment
function as ASSERT's CLI. Unlike the CLI score artifact, preserves raw answers.
No verdict is inferred, repaired or replaced by this adapter.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import logging
from pathlib import Path
import shutil


def contract_from_config(config_path):
    from assert_ai.config import load_config, parse_pipeline_config
    from assert_ai.core.io import load_prompt_text
    from assert_ai.core.judge import build_judge_contract

    raw = load_config(config_path)
    pipeline = parse_pipeline_config(raw)
    judge = pipeline.evaluation.judge
    taxonomy_path = config_path.parent / raw["pipeline"]["judge"]["taxonomy_path"]
    taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    contract = build_judge_contract(
        template=load_prompt_text("judge_system.md"),
        policy_raw=taxonomy,
        judge_dimensions=judge.dimensions,
        disabled_dimensions=judge.disabled_dimensions,
        schema_name="transcript_judgment",
    )
    return judge, taxonomy, contract


@contextmanager
def provider_audit(output_path):
    import litellm
    from litellm.integrations.custom_logger import CustomLogger

    class Audit(CustomLogger):
        async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
            response = response_obj.model_dump()
            # Whitelist response text and metadata; never serialize kwargs,
            # headers, credentials, or hidden reasoning content.
            record = {
                "model": response.get("model"),
                "usage": response.get("usage"),
                "choices": [
                    {"finish_reason": choice.get("finish_reason"),
                     "content": choice.get("message", {}).get("content")}
                    for choice in response.get("choices", [])
                ],
            }
            with (output_path / "provider_responses.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record) + "\n")

    callback = Audit()
    litellm.callbacks.append(callback)
    model_logger = logging.getLogger("assert_ai.core.model_client")
    previous_level = model_logger.level
    model_logger.setLevel(logging.DEBUG)
    handler = logging.FileHandler(output_path / "model_calls.log", encoding="utf-8")
    model_logger.addHandler(handler)
    try:
        yield
    finally:
        litellm.callbacks.remove(callback)
        model_logger.removeHandler(handler)
        model_logger.setLevel(previous_level)
        handler.close()


async def replay(config_path, input_path, output_path, pause=60):
    from assert_ai.core.judge import run_transcript_judge
    from assert_ai.core.transcript import Transcript, TranscriptEvent, TranscriptMetadata

    judge, taxonomy, contract = contract_from_config(config_path)
    if judge.n != 1:
        raise ValueError("Audited replay requires judge.n=1; use separate repeated runs")
    rows = [json.loads(line) for line in input_path.read_text(encoding="utf-8").splitlines()]
    if not rows or any(not row.get("events") for row in rows):
        raise ValueError("Every replay row must contain captured events")
    output_path.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(input_path, output_path / "inference_set.jsonl")
    shutil.copyfile(config_path, output_path / "config.yaml")
    (output_path / "taxonomy.json").write_text(json.dumps(taxonomy, indent=2), encoding="utf-8")
    (output_path / "system_prompt.txt").write_text(contract["system_prompt"], encoding="utf-8")
    (output_path / "request_settings.json").write_text(
        json.dumps({"model": asdict(judge.model), "n": judge.n,
                    "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
                    "response_schema": contract["response_schema"]}, indent=2), encoding="utf-8")
    failed = False
    for index, row in enumerate(rows):
        if index:
            await asyncio.sleep(pause)
        transcript = Transcript(
            metadata=TranscriptMetadata(
                test_case_id=str(row["test_case_id"]), kind=str(row.get("type", "")),
                behavior=str(row.get("behavior", "")), target=str(row.get("target", "")),
                tester_model=str(row.get("tester_model", "")),
            ),
            events=[TranscriptEvent.model_validate(event) for event in row["events"]],
            stop_reason=row.get("stop_reason"),
        )
        xml, mapping = transcript.format_transcript_xml("target", skip_system=False)
        (output_path / f"request-{index + 1:02d}.xml").write_text(xml, encoding="utf-8")
        with provider_audit(output_path):
            result = await run_transcript_judge(
                judge_model=judge.model.name, system_prompt=contract["system_prompt"],
                user_message=f"# Transcript\n{xml}", transcript=transcript,
                index_to_message_id=mapping, policy_raw=taxonomy,
                judge_n=judge.n, judge_temperature=judge.model.temperature,
                judge_max_tokens=judge.model.max_tokens,
                reasoning_effort=judge.model.reasoning_effort,
                **{key: contract[key] for key in
                   ("score_keys", "response_schema", "not_applicable_score_keys", "dimension_scales")},
            )
        record = {"test_case_id": row["test_case_id"], **result}
        with (output_path / "scores.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\n")
        failed |= result["judge_status"] != "ok"
        print(row["test_case_id"], result["judge_status"],
              result["verdict"].get("dimensions"), flush=True)
    if failed:
        raise RuntimeError("At least one ASSERT judgment failed; inspect retained scores")


def main():
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("eval_config.yaml"))
    parser.add_argument("--pause", type=float, default=60)
    args = parser.parse_args()
    if args.pause < 0:
        parser.error("--pause must be nonnegative")
    asyncio.run(replay(args.config.resolve(), args.input.resolve(), args.output.resolve(), args.pause))


if __name__ == "__main__":
    main()
