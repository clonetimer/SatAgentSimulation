"""Audit a sealed TaskSpec replay and a changed-seed control in fresh workers."""
import argparse
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle
from sat_sim.scenario_templates import instantiate_scenario_template


def digest(data):
    return hashlib.sha256(data).hexdigest()


def execute(task, root):
    prepared = prepare_run(task, output_root=root, run_id="r1-replay")
    result = execute_prepared_run(prepared.bundle_root, expected_plan_sha256=prepared.execution_plan_sha256,
                                  max_attempts=1, hard_timeout=True)
    bundle = Path(prepared.bundle_root)
    assert result.run_record.status.value == "SUCCEEDED"
    assert result.validation.result.value == "PASS"
    assert verify_run_bundle(bundle)["ok"] and (bundle/"SEALED.json").is_file()
    trace = bundle / "results/telemetry.jsonl"
    rows = [json.loads(line) for line in trace.read_bytes().splitlines()]
    assert len(rows) == 11
    return bundle, rows, dict(status="SUCCEEDED", validation="PASS", sealed=True,
                              plan_sha256=prepared.execution_plan_sha256,
                              spec_sha256=digest((bundle/"input/task_spec.json").read_bytes()),
                              telemetry_sha256=digest(trace.read_bytes()), rows=len(rows),
                              first_row_fields=len(rows[0]))


def audit(root):
    spec = instantiate_scenario_template("whole_spacecraft_unified_native", task_id="dt_r1_replay")
    spec["simulation"].update(duration_s=20., step_s=.2, sample_s=2.)
    spec["assurance"]["allow_proxy"] = False
    spec["parameters"]["values"].update(simulation_seed=811, rw_command_deadband_nm=.01,
        rw_motor_response_time_constant_s=.25, imu_gyro_quantization_lsb_rad_s=1e-5,
        imu_gyro_bias_random_walk_std_rad_s_sqrt_s=2e-4)
    first, source_rows, source = execute(spec, root/"original")
    spec_path = first/"input/task_spec.json"
    restored = json.loads(spec_path.read_bytes())
    _, replay_rows, replay = execute(restored, root/"replay")
    assert source_rows == replay_rows and source["telemetry_sha256"] == replay["telemetry_sha256"]
    assert source["spec_sha256"] == replay["spec_sha256"]
    restored["parameters"]["values"]["simulation_seed"] = 812
    _, control_rows, control = execute(restored, root/"control")
    assert [r["time_s"] for r in source_rows] == [r["time_s"] for r in control_rows]
    key = "adcs.sensor.gyro_bias_walk_rad_s_x"
    delta = max(abs(a[key]-b[key]) for a,b in zip(source_rows, control_rows, strict=True))
    assert delta > 0 and control["telemetry_sha256"] != source["telemetry_sha256"]
    assert verify_run_bundle(first)["ok"]
    environment = json.loads((first/"runtime/environment.json").read_bytes())
    return dict(original=source, replay=replay, changed_seed=control, max_changed_walk_rad_s=delta,
                environment=dict(python=environment["python"]["version"], platform=environment["platform"],
                                 assurance=environment["assurance"]),
                dependencies=json.loads((first/"runtime/dependency_versions.json").read_bytes()))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--keep-bundles", type=Path, help="New directory for three complete local Run Bundles")
    args = parser.parse_args()
    if args.keep_bundles:
        args.keep_bundles.mkdir(parents=True, exist_ok=False)
        result = audit(args.keep_bundles)
    else:
        with TemporaryDirectory() as tmp:
            result = audit(Path(tmp))
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print({k:v for k,v in result.items() if k not in ("environment","dependencies")})
