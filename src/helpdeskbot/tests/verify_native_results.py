"""Fail closed on missing native trajectories; keep quality scores separate."""
import argparse
import json
from pathlib import Path
import re

from evaluation.assert_suite.trajectory import from_native, validate
from run_native_evaluation import redact


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--expected", type=int, default=6)
    args = parser.parse_args()
    reports = []
    for index in range(1, args.expected + 1):
        path = args.directory / f"items-{index}.json"
        items = redact(json.loads(path.read_text(encoding="utf-8")))
        path.write_text(json.dumps(items, indent=2), encoding="utf-8")
        run = json.loads((args.directory / f"run-{index}.json").read_text(encoding="utf-8"))
        if run["status"] != "completed" or len(items) != 1 or run["result_counts"]["errored"]:
            raise ValueError("Incomplete native run")
        query = run["data_source"]["source"]["content"][0]["query"]
        steps, text = from_native(items[0]["sample"])
        case = re.search(r"DEMO_CASE:\s*([a-z-]+)", query).group(1)
        findings = validate(steps, case)
        reports.append({"eval_id": run["eval_id"], "run_id": run["id"],
                        "tool_count": len(steps), "assistant_text_present": bool(text),
                        "findings": findings, "generic_quality_counts": run["result_counts"]})
    report = {"source": "native_hosted_target_sample", "signature_verification": False,
              "items": reports}
    (args.directory / "trajectory-proof.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"complete_native_samples": len(reports),
                      "deterministic_passed": sum(not any(r["findings"].values()) for r in reports)}))
    if any(any(r["findings"].values()) for r in reports):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
