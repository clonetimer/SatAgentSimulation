"""Replay a sealed four-effect historical run from relocated model assets."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def assets(root):
    env = json.loads((root / "results/summary.json").read_bytes())["orbital_environment"]
    refs = env["assets"]
    mag = refs["igrf14"]
    weather = env["space_weather"]
    members = [(Path(item["path"]), item["sha256"]) for item in refs["spice"] + [refs["gravity_full"], weather]]
    members += [(Path(mag[key+"_path"]), mag[key+"_sha256"]) for key in ("source", "cof")]
    members += [(Path(weather["path"]).with_name("space_weather_manifest.json"), weather["provenance_manifest_sha256"])]
    return {path.name: (path.resolve(), sha) for path, sha in members}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "asset_root", "weather", "destination", "output"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    assert verify_run_bundle(args.source)["ok"]
    historical = assets(args.source)
    expected_paths = {}
    for name, (original, expected) in historical.items():
        if name == args.weather.name or name == "space_weather_manifest.json":
            candidate = args.weather.parent / name
        else:
            candidate = args.asset_root / original.parent.name / name
        candidate = candidate.resolve()
        if candidate == original or not candidate.is_file() or digest(candidate) != expected:
            raise ValueError(f"Historical asset missing or different: {name}")
        expected_paths[name] = candidate
    os.environ["SAT_SIM_ASSET_ROOT"] = str(args.asset_root.resolve())
    os.environ["SAT_SIM_SPACE_WEATHER_PATH"] = str(args.weather.resolve())
    task = json.loads((args.source/"input/task_spec.json").read_bytes())
    plan = prepare_run(task, output_root=args.destination.resolve(), run_id="relocated")
    outcome = execute_prepared_run(plan.bundle_root, expected_plan_sha256=plan.execution_plan_sha256,
                                   hard_timeout=True, max_attempts=1)
    replay = Path(plan.bundle_root)
    assert verify_run_bundle(replay)["ok"] and outcome.validation.result.value == "PASS"
    repeated = assets(replay)
    assert historical.keys() == repeated.keys() and len(repeated) == 9
    verified = {}
    for name, (actual_path, sha) in repeated.items():
        original, expected = historical[name]
        assert actual_path == expected_paths[name] and actual_path != original
        assert digest(actual_path) == sha == expected
        verified[name] = sha
    comparisons = {}
    for name in ("input/task_spec.json","results/telemetry.jsonl","validation/validation_outcome.json"):
        before, after = (digest(root/name) for root in (args.source,replay))
        comparisons[name] = dict(historical_sha256=before, replay_sha256=after, identical=before==after)
    report = dict(assets=verified, comparisons=comparisons,
                  frames=len((replay/"results/telemetry.jsonl").read_bytes().splitlines()),
                  historical_manifest=digest(args.source/"bundle_manifest.json"),
                  replay_manifest=digest(replay/"bundle_manifest.json"))
    args.output.write_text(json.dumps(report,indent=2)+"\n",encoding="utf8")
    print(json.dumps(comparisons))
    assert all(item["identical"] for item in comparisons.values())
