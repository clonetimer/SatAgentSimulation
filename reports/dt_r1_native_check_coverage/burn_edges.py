"""Compare sealed tank-state increments with endpoint flow quadrature."""
import json
import math
import sys
from pathlib import Path

from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle


def run(source, root, name, dt, start, duration):
    task = json.loads(source.read_bytes())
    task["simulation"].update(step_s=dt, sample_s=dt)
    task["parameters"]["values"].update(propulsion_enabled=True, propulsion_burn_start_s=start,
                                        propulsion_burn_on_time_s=duration)
    plan = prepare_run(task, output_root=root, run_id=name)
    result = execute_prepared_run(plan.bundle_root, expected_plan_sha256=plan.execution_plan_sha256,
                                  max_attempts=1, hard_timeout=True)
    assert result.validation.result.value == "PASS"
    return Path(plan.bundle_root)


def measure(bundle):
    assert verify_run_bundle(bundle)["ok"]
    rows = [json.loads(line) for line in (bundle / "results/telemetry.jsonl").read_bytes().splitlines()]
    values = json.loads((bundle / "input/task_spec.json").read_bytes())["parameters"]["values"]
    dt = rows[1]["time_s"] - rows[0]["time_s"]
    flow = "propulsion.fuel_mass_dot_kg_s"
    mass = "propulsion.fuel_mass_kg"
    edges = []
    integrated = []
    interior = []
    peak = max(-r[flow] for r in rows)
    for left, right in zip(rows, rows[1:]):
        h = right["time_s"] - left["time_s"]
        assert math.isclose(h, dt, rel_tol=0, abs_tol=1e-12)
        actual = left[mass] - right[mass]
        trap = -0.5 * h * (left[flow] + right[flow])
        integrated.append(trap)
        if left[flow] != right[flow]:
            edges.append(dict(start_s=left["time_s"], end_s=right["time_s"], left_flow_kg_s=-left[flow],
                              right_flow_kg_s=-right[flow], tank_loss_kg=actual, trapezoid_kg=trap,
                              effective_full_flow_fraction=actual/(h*peak)))
        else:
            interior.append(abs(actual-trap))
    loss = rows[0][mass] - rows[-1][mass]
    assert len(edges) == 2 and max(interior) < 1e-12
    return dict(step_s=dt, requested_duration_s=values["propulsion_burn_on_time_s"],
                command_first_seen_s=next(r["time_s"] for r in rows if r["propulsion.burn_command_on_time_s"] > 0),
                peak_flow_kg_s=peak, tank_loss_kg=loss, flow_trapezoid_kg=math.fsum(integrated),
                excess_over_trapezoid_kg=loss-math.fsum(integrated), edges=edges,
                max_non_edge_difference_kg=max(interior))


if __name__ == "__main__":
    source, prior, root = map(Path, sys.argv[1:])
    results = {f"prior-{h}":measure(prior / f"burn-{h}") for h in (0.5, 0.25, 0.125)}
    # Predictions for grid-aligned, rectangular commands fixed from the three prior runs.
    # Probe an off-grid shutdown separately without assuming the same edge weights.
    for name, h, duration in (("finer", 0.0625, 4.0), ("longer", 0.25, 8.0), ("off-grid", 0.25, 4.125)):
        data = measure(run(source, root, name, h, 5.0, duration))
        results[name] = data
        if name != "off-grid":
            predicted = results["prior-0.5"]["peak_flow_kg_s"] * (duration + h/3)
            data["predicted_loss_kg"] = predicted
            assert abs(predicted-data["tank_loss_kg"]) < 1e-12
    (root / "edges.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
