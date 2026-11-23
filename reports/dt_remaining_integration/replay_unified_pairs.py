"""Rebuild the one-factor SYS-01 cases from the eleven-target task, using one candidate."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from sat_sim.capability_registry import get_capability
from sat_sim.run_bundle import execute_prepared_run, prepare_run
from unified_power_pairs import extract


def cases():
    contact = dict(payload_max_pointing_error_deg=175, comm_max_pointing_error_deg=175,
                   solar_normal_b_x=-1, battery_heater_setpoint_k=310)
    overhead = {**contact, "initial_true_anomaly_rad": 0.0}
    day = {**contact, "initial_true_anomaly_rad": 2.88373272115305}
    return {
        "original": {},
        "burn": {"propulsion_enabled": True},
        "delayed": {"propulsion_enabled": True, "propulsion_burn_start_s": 25.0},
        "contact": contact,
        "blocked": day,
        "illum": {**day, "solar_normal_b_x": 1},
        "overhead": overhead,
        "lowcontact": {**overhead, "initial_soc": 0.10},
        "offaxis": {**overhead, "spacecraft_antenna_orientation_b": [0, 0, 0]},
        "coolcontact": {**overhead, "payload_power_w": 300},
        "hotcontact": {**overhead, "payload_power_w": 300, "initial_payload_temp_k": 340},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "destination", "output"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    original = json.loads(args.source.read_bytes())
    contract = get_capability(original["model"]["capability_id"]).data
    original["mission"]["required_couplings"] = contract["integration_contract"]["couplings"]["supported"]
    report = {}
    for name, params in cases().items():
        spec = deepcopy(original)
        for config in (spec["parameters"]["values"], spec["model"]["config"]):
            config.update(params)
        plan = prepare_run(spec, output_root=args.destination.resolve(), run_id=name)
        result = execute_prepared_run(plan.bundle_root, expected_plan_sha256=plan.execution_plan_sha256,
                                     hard_timeout=True, max_attempts=1)
        case = extract(Path(plan.bundle_root))
        case["validation"] = result.validation.result.value
        case["parameter_changes_from_original"] = params
        report[name] = case
        print(name, case["validation"], flush=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")


if __name__ == "__main__":
    main()
