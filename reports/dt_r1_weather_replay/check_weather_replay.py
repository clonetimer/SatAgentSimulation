"""Check space-weather identity against a verified historical Run before replay."""
import argparse
import hashlib
import json
import os
from pathlib import Path

from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replay(source, weather, dest):
    if not verify_run_bundle(source)["ok"]:
        raise ValueError("Historical bundle failed integrity verification")
    summary = json.loads((source / "results/summary.json").read_bytes())
    expected = summary["orbital_environment"]["space_weather"]["sha256"]
    actual = digest(weather)
    if expected != actual:
        raise ValueError(f"Space weather differs: expected {expected}, found {actual}")
    os.environ["SAT_SIM_SPACE_WEATHER_PATH"] = str(weather.resolve())
    task = json.loads((source / "input/task_spec.json").read_bytes())
    plan = prepare_run(task, output_root=dest, run_id=source.name)
    outcome = execute_prepared_run(plan.bundle_root, expected_plan_sha256=plan.execution_plan_sha256, hard_timeout=True, max_attempts=1)
    target = Path(plan.bundle_root)
    if outcome.validation.result.value != "PASS" or not verify_run_bundle(target)["ok"]:
        raise ValueError("Replay or bundle verification failed")
    repeated = json.loads((target / "results/summary.json").read_bytes())
    if repeated["orbital_environment"]["space_weather"]["sha256"] != expected:
        raise ValueError("Runtime weather differs from the historical asset")
    for name in ["results/telemetry.jsonl", "validation/validation_outcome.json"]:
        if digest(source / name) != digest(target / name):
            raise ValueError(f"Replay diverged in {name}")
    print("PASS", expected, target)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "weather", "dest"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    replay(args.source, args.weather, args.dest)
