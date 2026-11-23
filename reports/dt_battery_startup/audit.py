"""Exercise the initial feedback and the official post-load battery output."""
import csv
import json
import sys
from pathlib import Path

from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle


def run(source, root, name, soc):
    task = json.loads(source.read_bytes())
    task["simulation"].update(duration_s=6., step_s=0.25, sample_s=0.25)
    task["parameters"]["values"].update(initial_soc=soc, propulsion_enabled=True, propulsion_burn_start_s=0.,
                                        propulsion_burn_on_time_s=2., battery_heater_setpoint_k=305.)
    task["outputs"]["telemetry_streams"] = [
        dict(stream_id="battery_verify", sample_s=1., format="csv", fields=["eps.battery_soc"])]
    p = prepare_run(task, output_root=root, run_id=name)
    result = execute_prepared_run(p.bundle_root, expected_plan_sha256=p.execution_plan_sha256,
                                  hard_timeout=True, max_attempts=1)
    bundle = Path(p.bundle_root)
    assert verify_run_bundle(bundle)["ok"] and result.validation.result.value == "PASS", result.run_record
    rows = result.trace_rows
    first = rows[0]
    assert first["time_s"] == 0 and first["eps.battery_capacity_j"] == 576000
    assert first["eps.battery_soc"] == soc and first["eps.battery_energy_j"] == 576000*soc
    assert abs(first["eps.net_power_w"]) > 1  # initial feedback seed has zero net power
    assert bool(first["eps.pdu.heater_enabled"]) == (soc >= 0.30)
    assert not first["propulsion.burn_permitted"]  # reset interlock, gate confirms at tick zero
    assert bool(rows[1]["propulsion.burn_permitted"]) == (soc >= 0.30)
    heater = max(r["eps.loads.heater_power_w"] for r in rows)
    thrust = max(r["propulsion.total_thrust_n"] for r in rows)
    assert (heater > 0) == (soc >= 0.30) and (thrust > 0) == (soc >= 0.30)
    errors = [abs(b["eps.battery_energy_j"] - a["eps.battery_energy_j"]
                  - (b["time_s"] - a["time_s"])*b["eps.net_power_w"]) for a,b in zip(rows, rows[1:])]
    assert max(errors) < 1e-7
    stream_path = next((bundle/"results/dataset").rglob("battery_verify.csv"))
    with stream_path.open(encoding="utf-8-sig", newline="") as f:
        stream = list(csv.DictReader(f))
    by_time = {r["time_s"]:r["eps.battery_soc"] for r in rows}
    assert len(stream) == 7
    assert all(abs(float(r["eps.battery_soc"])-by_time[float(r["time_s"])]) < 1e-12 for r in stream)
    check = result.validation.physical_validation["check_results"]["soc_bounds"]
    assert check["status"] == ("WARNING" if soc < 0.02 else "PASS")
    return dict(soc=soc, initial_energy_j=first["eps.battery_energy_j"], capacity_j=first["eps.battery_capacity_j"],
                first_net_w=first["eps.net_power_w"], peak_heater_w=heater, peak_thrust_n=thrust,
                max_energy_balance_error_j=max(errors), main_rows=len(rows), grouped_rows=len(stream),
                soc_check=check["status"], status=result.validation.result.value)


if __name__ == "__main__":
    source, root = map(Path, sys.argv[1:])
    evidence = [run(source, root, name, soc) for name, soc in (("nominal", .62), ("low", .10), ("near-empty", .01))]
    (root/"result.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
