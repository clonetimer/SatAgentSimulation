"""Replay supplied sealed scenarios and compare complete trace and acceptance outputs."""
import argparse
import hashlib
import json
from pathlib import Path

from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle


def hash_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replay(source, output):
    assert verify_run_bundle(source)["ok"], source
    spec = json.loads((source / "input/task_spec.json").read_bytes())
    plan = prepare_run(spec, output_root=output, run_id=source.name)
    execute_prepared_run(plan.bundle_root, expected_plan_sha256=plan.execution_plan_sha256,
                         max_attempts=1, hard_timeout=True)
    copy = Path(plan.bundle_root)
    assert verify_run_bundle(copy)["ok"], copy
    paths = ("input/task_spec.json", "results/telemetry.jsonl", "results/assertions.json",
             "validation/validation_outcome.json")
    hashes = {}
    for path in paths:
        original, repeated = hash_file(source / path), hash_file(copy / path)
        assert original == repeated, (source.name, path, original, repeated)
        hashes[path] = original
    report = (copy / "results/report.html").read_text(encoding="utf-8")
    state = json.loads((copy / paths[-1]).read_bytes())["result"]
    assert state in {"PASS", "FAIL"}
    expected_text = {"PASS": "通过", "FAIL": "未通过"}[state]
    assert "<span>运行状态</span><strong>已完成</strong>" in report
    assert f"<span>验证结果</span><strong>{expected_text}</strong>" in report
    return dict(case=source.name, validation=state, identical_files=hashes,
                source_manifest=hash_file(source / "bundle_manifest.json"),
                replay_manifest=hash_file(copy / "bundle_manifest.json"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("bundles", type=Path, nargs="+")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    verified = [replay(path.resolve(), args.output.resolve()) for path in args.bundles]
    (args.output / "replay.json").write_text(json.dumps(verified, indent=2) + "\n", encoding="utf-8")
