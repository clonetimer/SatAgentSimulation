"""Record battery capacity, stored energy and SOC across a bounded capacity fault."""
import argparse
import json
from pathlib import Path

from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle


def run(source, root, name, ratio):
    spec = json.loads(source.read_bytes())
    spec["simulation"].update(step_s=.25, sample_s=.25)
    if ratio < 1:
        spec["events"]["faults"] = [dict(id="capacity_window", target="whole_spacecraft",
            effect="eps_battery_capacity_loss", start_s=8., end_s=16., magnitude=1-ratio,
            parameters={"remaining_capacity_ratio": ratio})]
        spec["model"]["target"]["mode"] = "fault"
    plan = prepare_run(spec, output_root=root, run_id=name)
    execute_prepared_run(plan.bundle_root, expected_plan_sha256=plan.execution_plan_sha256,
                         max_attempts=1, hard_timeout=True)
    bundle = Path(plan.bundle_root)
    assert verify_run_bundle(bundle)["ok"]
    rows = [json.loads(line) for line in (bundle/"results/telemetry.jsonl").read_bytes().splitlines()]
    assert len(rows) == 121
    events = json.loads((bundle/"results/events.json").read_bytes())
    assert events["declared_count"] == int(ratio < 1)
    records = [[round(r["time_s"], 9), r["eps.battery_capacity_j"], r["eps.battery_energy_j"],
                r["eps.battery_soc"]] for r in rows]
    return dict(run=name, ratio=ratio, rows=records, declaration=events["declared"],
                manifest=json.loads((bundle/"SEALED.json").read_bytes())["bundle_manifest_sha256"],
                validation=json.loads((bundle/"validation/validation_outcome.json").read_bytes())["result"])



def verify(cases):
    healthy, moderate, severe = cases
    for case in cases:
        for baseline, sample in zip(healthy["rows"], case["rows"], strict=True):
            t, capacity, energy, soc = sample
            assert baseline[0] == t
            active = 8 <= t <= 16
            assert capacity == 576000 * (case["ratio"] if active else 1.)
            assert abs(energy / capacity - soc) < 1e-12
            if t < 8 or case is moderate and t > 16:
                assert sample == baseline
            if active and case is moderate:
                assert energy == baseline[2]
            if active and case is severe:
                assert soc == 1. and energy == capacity
    assert severe["rows"][-1][2] < healthy["rows"][-1][2]

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    cases = [run(args.spec, args.root, name, ratio) for name, ratio in
             (("healthy", 1.), ("capacity75", .75), ("capacity50", .5))]
    verify(cases)
    (args.root/"cases.json").write_text(json.dumps(cases, indent=2)+"\n", encoding="utf-8")
    for case in cases:
        for row in case["rows"]:
            if row[0] in (7.75,8,8.25,16,16.25,16.5,30):
                print(case["run"], case["validation"], row)
