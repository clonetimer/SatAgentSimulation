"""Replay a GGM03S task with propulsion off/on and compare native outputs."""
import json
import math
import sys
from pathlib import Path

from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle
from sat_sim.validation.physical_checks import evaluate_physical_validation


def check(source, output, burn, step=0.5):
    task = json.loads(source.read_bytes())
    task["simulation"].update(step_s=step, sample_s=step)
    task["parameters"]["values"].update(propulsion_enabled=burn, propulsion_burn_start_s=5.0,
                                        propulsion_burn_on_time_s=4.0)
    plan = prepare_run(task, output_root=output, run_id=f"burn-{step}" if burn else "control")
    result = execute_prepared_run(plan.bundle_root, expected_plan_sha256=plan.execution_plan_sha256,
                                  max_attempts=1, hard_timeout=True)
    bundle = Path(plan.bundle_root)
    assert result.validation.result.value == "PASS" and verify_run_bundle(bundle)["ok"]
    rows = [json.loads(line) for line in (bundle / "results/telemetry.jsonl").read_bytes().splitlines()]
    env = result.summary["orbital_environment"]
    gravity = env["gravity_source"]
    checks = result.validation.physical_validation["check_results"]
    heights = [math.hypot(*(row[f"orbit.position_m_{axis}"] for axis in "xyz")) - gravity["reference_radius_m"]
               for row in rows]
    delta = max(abs(height - row["orbit.altitude_m"]) for row, height in zip(rows, heights, strict=True))
    assert delta < 1e-7 and checks["orbit_altitude_sanity"]["valid_row_count"] == len(rows)
    assert checks["orbit_altitude_sanity"]["status"] == "PASS"
    data = dict(samples=len(rows), height_min_m=min(heights), height_max_m=max(heights),
                max_recalculation_error_m=delta, radius_m=gravity["reference_radius_m"],
                definition=env["altitude_definition"], asset_sha256=gravity["sha256"],
                altitude=checks["orbit_altitude_sanity"], fuel=checks["fuel_monotonicity"])
    if burn:
        mass = [r["propulsion.fuel_mass_kg"] for r in rows]
        thrust = [r["propulsion.total_thrust_n"] for r in rows]
        assert max(thrust) > 0 and mass[0] > mass[-1] >= 0
        assert all(b <= a + 1e-9 for a, b in zip(mass, mass[1:]))
        assert checks["fuel_monotonicity"]["status"] == "PASS"
        data.update(initial_mass_kg=mass[0], final_mass_kg=mass[-1], peak_thrust_n=max(thrust),
                    positive_thrust_samples=sum(t > 0 for t in thrust),
                    sampled_impulse_ns=sum((a+b)*step/2 for a,b in zip(thrust, thrust[1:])),
                    consumed_mass_kg=mass[0]-mass[-1], step_s=step)
    else:
        assert not checks["fuel_monotonicity"]["evaluated"] and all("propulsion.fuel_mass_kg" not in r for r in rows)
    return data


if __name__ == "__main__":
    source, output = map(Path, sys.argv[1:])
    summary = {name: check(source, output, burn) for name, burn in (("control", False), ("burn", True))}
    for step in (0.25, 0.125):
        summary[f"burn-{step}"] = check(source, output, True, step)
    for row, name in (({"orbit.altitude_m": 99999}, "orbit_altitude_sanity"),
                      ({"propulsion.fuel_mass_kg": 2}, "fuel_monotonicity")):
        rows = [{"propulsion.fuel_mass_kg": 1}, row]
        assert evaluate_physical_validation(rows, checks=[name])["status"] == "fail"
    (output / "comparison.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
