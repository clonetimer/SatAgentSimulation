import json
import sys
from pathlib import Path

from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle


def run(source, dest, name, capacity=None, *, coarse=False):
    task = json.loads(source.read_bytes())
    if name == "active-link":
        # Independent nominal RF fixture used by test_runtime_evidence_status_thermal_rf.
        task["simulation"].update(duration_s=3.0, step_s=0.1)
        task["parameters"]["values"] = {"payload_max_pointing_error_deg": 180.0}
    task["simulation"]["sample_s"] = (10 * task["simulation"]["step_s"] if coarse
                                      else task["simulation"]["step_s"])
    if capacity is not None:
        task["parameters"]["values"]["storage_capacity_bits"] = capacity
    plan = prepare_run(task, output_root=dest, run_id=name)
    record = execute_prepared_run(plan.bundle_root, expected_plan_sha256=plan.execution_plan_sha256,
                                  hard_timeout=True, max_attempts=1)
    assert verify_run_bundle(plan.bundle_root)["ok"]
    print(name, record.validation.result.value, flush=True)
    assert record.validation.result.value == "PASS"


if __name__ == "__main__":
    source, dest = map(Path, sys.argv[1:])
    run(source, dest, "full-rate")
    run(source, dest, "limited-storage", 11000000)
    run(source, dest, "active-link")
