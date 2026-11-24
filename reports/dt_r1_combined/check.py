"""Run against the combined #229/#230/#231 source and check actual UI and API."""
import argparse
import importlib.util
import json
from pathlib import Path

from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle


def check(checkout, root, chrome, burn):
    source = checkout / "reports/dt_storage_balance/task.json"
    task = json.loads(source.read_bytes())
    task["simulation"].update(step_s=0.25, sample_s=2.0)
    task["parameters"]["values"].update(propulsion_enabled=burn, propulsion_burn_start_s=5.0,
                                        propulsion_burn_on_time_s=4.0)
    plan = prepare_run(task, output_root=root, run_id="combined-burn" if burn else "combined-control")
    executed = execute_prepared_run(plan.bundle_root, expected_plan_sha256=plan.execution_plan_sha256,
                                    max_attempts=1, hard_timeout=True)
    bundle = Path(plan.bundle_root)
    assert executed.validation.result.value == "PASS" and verify_run_bundle(bundle)["ok"]
    physical = executed.validation.physical_validation
    expected_missing = [] if burn else ["fuel_monotonicity"]
    assert physical["checks_missing"] == expected_missing
    assert all(physical["check_results"][key]["status"] == "PASS" for key in
               ("orbit_altitude_sanity", "quaternion_norm", "data_conservation"))
    if burn:
        assert physical["check_results"]["fuel_monotonicity"]["status"] == "PASS"
        assert executed.trace_rows[0]["propulsion.fuel_mass_kg"] > executed.trace_rows[-1]["propulsion.fuel_mass_kg"]
    spec = importlib.util.spec_from_file_location("browser_audit", checkout / "reports/dt_r1_model_scope/audit.py")
    browser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(browser)
    observed = browser.audit(bundle.resolve(), chrome)
    scope = observed["api_model_scope"]
    assert observed["report_api_scope"] == scope and scope["summary"] == "仅分析用途"
    assert scope["summary"] in observed["card_collapsed"]
    assert all(v in observed["report_boundary"] and v in observed["card_expanded"] for v in scope["fields"].values())
    assert scope["fields"]["本次未评估的物理检查"] == ("清单为空" if burn else "fuel_monotonicity")
    return dict(run_id=bundle.name, manifest_sha256=json.loads((bundle/"SEALED.json").read_bytes())["bundle_manifest_sha256"],
                validation=executed.validation.result.value, checks_run=physical["checks_run"],
                missing=physical["checks_missing"], warning_count=physical["warning_count"],
                checks={k:v["status"] for k,v in physical["check_results"].items()},
                rows=len(executed.trace_rows), scope=scope, overview=observed["card_collapsed"],
                browser=observed["browser"], sealed_report_sha256=observed["sealed_report_sha256"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("chrome")
    args = parser.parse_args()
    results = [check(args.checkout, args.output, args.chrome, burn) for burn in (False, True)]
    (args.output / "combined.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
