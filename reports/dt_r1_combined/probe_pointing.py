"""Compare a free response and two tasks with a declared terminal error limit."""
import json
import sys
from pathlib import Path

from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle


def measure(source, root, name, gains, limit):
    task = json.loads(source.read_bytes())
    task["simulation"].update(step_s=0.25, sample_s=2)
    task["parameters"]["values"].update(controller_k=gains[0], controller_p=gains[1])
    if limit is not None:
        task["model"]["validation"]["acceptance_assertions"] = [{
            "id": "terminal_pointing", "metric": "final_pointing_error_deg",
            "operator": "<=", "value": limit, "required": True,
        }]
    plan = prepare_run(task, output_root=root, run_id=name)
    result = execute_prepared_run(plan.bundle_root, expected_plan_sha256=plan.execution_plan_sha256,
                                  max_attempts=1, hard_timeout=True)
    bundle = Path(plan.bundle_root)
    assert verify_run_bundle(bundle)["ok"]
    summary = json.loads((bundle / "results/summary.json").read_bytes())
    assertions = json.loads((bundle / "results/assertions.json").read_bytes())
    rows = [json.loads(line) for line in (bundle / "results/telemetry.jsonl").read_bytes().splitlines()]
    assert abs(rows[-1]["time_s"] - 30) < 1e-9 and len(rows) == 16
    assert float(rows[-1]["adcs.pointing_error_deg"]) == summary["final_pointing_error_deg"]
    return dict(run_id=name, gains=gains, limit_deg=limit,
                samples=[[r["time_s"], r["adcs.pointing_error_deg"]] for r in rows],
                assertions=assertions, mission_status=summary["mission_status"],
                validation=result.validation.result.value,
                issues=result.validation.physical_validation["issues"],
                seal=json.loads((bundle / "SEALED.json").read_bytes())["bundle_manifest_sha256"])


if __name__ == "__main__":
    source, root = map(Path, sys.argv[1:])
    cases = [("free", (0, 0), None), ("required-open", (0, 0), 8), ("required-closed", (3.5, 30), 8)]
    results = [measure(source, root, *case) for case in cases]
    (root / "pointing.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    assert results[0]["samples"] == results[1]["samples"]
    assert [r["assertions"]["status"] for r in results] == ["NOT_CONFIGURED", "FAIL", "PASS"]
    assert [r["validation"] for r in results] == ["PASS", "FAIL", "PASS"]
