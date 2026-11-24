"""Paired native drag-area perturbation and osculating two-body energy diagnostics."""
import argparse
import hashlib
import json
import math
from pathlib import Path

from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle


def measure(source, destination, area):
    spec = json.loads(source.read_bytes())
    spec["simulation"]["duration_s"] = 180
    for values in (spec["parameters"]["values"], spec["model"]["config"]):
        values["drag_area_m2"] = area
    plan = prepare_run(spec, output_root=destination.resolve(), run_id=f"area_{area}")
    result = execute_prepared_run(plan.bundle_root, expected_plan_sha256=plan.execution_plan_sha256,
                                  hard_timeout=True, max_attempts=1)
    bundle = Path(plan.bundle_root)
    assert verify_run_bundle(bundle)["ok"]
    summary = json.loads((bundle / "results/summary.json").read_bytes())
    def find_mu(node):
        if isinstance(node, dict):
            if "body_mu_m3_s2" in node:
                yield node["body_mu_m3_s2"]
            for value in node.values():
                yield from find_mu(value)
    mu, = set(find_mu(summary))
    telemetry = (bundle / "results/telemetry.jsonl").read_bytes()
    rows = []
    for line in telemetry.splitlines():
        row = json.loads(line)
        position = [row[f"orbit.position_m_{axis}"] for axis in "xyz"]
        velocity = [row[f"orbit.velocity_m_s_{axis}"] for axis in "xyz"]
        energy = sum(v*v for v in velocity)/2 - mu/math.hypot(*position)
        rows.append(dict(time_s=row["time_s"], position_m=position, velocity_m_s=velocity,
                         energy_j_kg=energy, semimajor_axis_m=-mu/(2*energy),
                         drag_force_n=row["orbit.drag_force_norm_n"],
                         drag_work_rate_relative_w=row["orbit.drag_force_relative_velocity_power_w"],
                         density=row["orbit.drag_input_density_kg_m3"], speed_rel_m_s=row["orbit.drag_relative_speed_m_s"]))
    return dict(area_m2=area, mu=mu, validation=result.validation.result.value, rows=rows,
                telemetry_sha256=hashlib.sha256(telemetry).hexdigest(),
                manifest_sha256=hashlib.sha256((bundle/"bundle_manifest.json").read_bytes()).hexdigest())


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    for key in ("source","destination","output"):
        p.add_argument(key,type=Path)
    args=p.parse_args()
    a,b=(measure(args.source,args.destination,area) for area in (1,2))
    aligned=list(zip(a["rows"],b["rows"],strict=True))
    assert len(aligned)==91 and all(l["time_s"]==r["time_s"] for l,r in aligned)
    assert all(aligned[0][0][k]==aligned[0][1][k] for k in ("position_m","velocity_m_s"))
    for l,r in aligned[1:]:
        assert l["drag_work_rate_relative_w"]<0 and r["drag_work_rate_relative_w"]<0
        assert r["drag_force_n"]>l["drag_force_n"]>0
        assert r["energy_j_kg"]<l["energy_j_kg"] and r["semimajor_axis_m"]<l["semimajor_axis_m"]
    l,r=aligned[-1]
    stats=dict(final_delta_energy_j_kg=r["energy_j_kg"]-l["energy_j_kg"],
               final_delta_semimajor_axis_m=r["semimajor_axis_m"]-l["semimajor_axis_m"],
               final_separation_m=math.dist(l["position_m"],r["position_m"]))
    args.output.write_text(json.dumps(dict(summary=stats,small=a,large=b),indent=2)+"\n", encoding="utf8")
    print(stats)