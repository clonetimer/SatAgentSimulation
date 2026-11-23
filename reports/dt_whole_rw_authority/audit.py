"""Measure native wheel-event application and release from sealed whole-spacecraft runs."""
import argparse
import json
from pathlib import Path

from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle
from sat_sim.scenario_templates import instantiate_scenario_template


CASES = {
    "healthy": None,
    "loss50": ("degradations", "adcs_rw_torque_authority_loss", {"torque_scale": .5}),
    "loss75": ("degradations", "adcs_rw_torque_authority_loss", {"torque_scale": .25}),
}
START, END = 5.05, 14.95


def run(root):
    traces, evidence = {}, {}
    for name, event in CASES.items():
        template = "whole_spacecraft_unified_native"
        spec = instantiate_scenario_template(template, task_id="dt_wheel_timing")
        spec["simulation"].update(duration_s=20., step_s=.2, sample_s=.2)
        spec["parameters"]["values"].update(initial_pointing_error_deg=45., controller_p=60., simulation_seed=1701)
        spec["events"] = {"faults": [], "degradations": [], "constraints": []}
        if event:
            category, effect, params = event
            spec["events"][category].append(dict(id="wheel_zero_event", effect=effect, target="whole_spacecraft",
                start_s=START, end_s=END, parameters=dict(wheel_index=0, **params)))
        plan = prepare_run(spec, output_root=root, run_id=name)
        result = execute_prepared_run(plan.bundle_root, expected_plan_sha256=plan.execution_plan_sha256,
                                      hard_timeout=True, max_attempts=1)
        assert verify_run_bundle(plan.bundle_root)["ok"]
        assert result.validation.result.value == "PASS", result.validation
        traces[name] = result.trace_rows
        evidence[name] = {"template": template, "validation":result.validation.result.value, "rows":len(result.trace_rows)}
    for name, rows in traces.items():
        if name == "healthy":
            continue
        reference = traces["healthy"]
        assert [r["time_s"] for r in rows] == [r["time_s"] for r in reference]
        ratios = [r["adcs.rw.effective_torque_ratio_0"] for r in rows]
        changed = [i for i, ratio in enumerate(ratios) if abs(ratio-1.) > 1e-10]
        first, last = min(changed), max(changed)
        expected = {"loss50":.5,"loss75":.25}[name]
        assert changed == list(range(first,last+1))
        assert all(abs(ratios[i]-expected)<1e-10 for i in changed)
        assert all(abs(r[f"adcs.rw.effective_torque_ratio_{w}"]-1)<1e-10 for r in rows for w in (1,2))
        fields = ["adcs.rw.actual_applied_torque_nm_0", "adcs.rw.speed_rad_s_0", "attitude.truth.pointing_error_deg", "eps.loads.adcs_power_w", "eps.battery_energy_j"]
        def snapshot(i):
            return {"time_s":rows[i]["time_s"], "limit_ratio":ratios[i],
                    **{key:{"event":rows[i][key], "healthy":reference[i][key]} for key in fields}}
        evidence[name].update(requested_s=[START,END], transition_samples=[snapshot(i) for i in
                              (first-1,first,last,last+1,len(rows)-1)],
                              first_difference_s={key:next((r["time_s"] for r,h in zip(rows,reference)
                              if abs(r[key]-h[key])>1e-10),None) for key in fields})
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(run(args.root), indent=2, allow_nan=False)+"\n")
