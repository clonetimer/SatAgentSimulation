"""Run the four-effect R1 scenario and verify both post-initialization readers."""
import hashlib
import json
import sys
from pathlib import Path

from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle


def check(task_path, output_root):
    task = json.loads(task_path.read_bytes())
    plan = prepare_run(task, output_root=output_root, run_id="joint_wheel_environment")
    outcome = execute_prepared_run(plan.bundle_root, expected_plan_sha256=plan.execution_plan_sha256,
                                   hard_timeout=True, max_attempts=1)
    root = Path(plan.bundle_root)
    assert verify_run_bundle(root)["ok"] and outcome.validation.result.value == "PASS"
    rows = outcome.trace_rows
    env = outcome.summary["orbital_environment"]
    first = rows[0]
    gravity = env["initialized_earth_gravity"]
    weather = env["space_weather"]["coverage_f107_data_type_days"]
    assert gravity["harmonics_degree"] == 4 and gravity["harmonics_mu_m3_s2"] == 398600436000000.
    assert weather == {"OBS":3, "PRD":1}
    assert abs(first["eps.battery_soc"]-.62) < 1e-12 and first["eps.battery_capacity_j"] == 576000.
    injection, = outcome.validation.injection_evidence
    assert injection.effect_result.value == "PASS" and all(k.endswith("_3") for k in injection.evidence_fields_present)
    limited = [r for r in rows if abs(r["adcs.rw.effective_torque_ratio_3"]-.25)<1e-12]
    assert limited and all(abs(r[f"adcs.rw.effective_torque_ratio_{i}"]-1)<1e-12 for r in rows for i in range(3))
    assert all(r["orbit.eclipse_spacecraft_source_time_s"] <= r["time_s"]+1e-8 for r in rows)
    assert any(r["orbit.eclipse_spacecraft_geometry_valid"] for r in rows)
    return dict(rows=len(rows), initial_soc=first["eps.battery_soc"], capacity_j=first["eps.battery_capacity_j"],
                gravity=gravity, weather=weather, limited_samples=len(limited),
                evidence=injection.model_dump(mode="json"), validation="PASS", seal_verified=True,
                files={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in
                       ("input/task_spec.json","results/telemetry.jsonl","bundle_manifest.json")})


if __name__ == "__main__":
    task, output, evidence = map(Path, sys.argv[1:])
    evidence.write_text(json.dumps(check(task,output),indent=2)+"\n")
