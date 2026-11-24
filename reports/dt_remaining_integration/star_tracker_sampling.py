"""Run one fixed dropout event under two telemetry sample rates."""
import argparse
import hashlib
import json
from pathlib import Path

from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle


def analyze(original, destination, sample_s, repeated=False):
    spec = json.loads(original.read_bytes())
    spec["simulation"].update(step_s=0.2, sample_s=sample_s)
    event, = spec["events"]["faults"]
    assert event["effect"] == "adcs_star_tracker_dropout"
    event.update(start_s=4.3, end_s=4.7)
    if repeated:
        first = dict(event, id="first_sampled", start_s=1., end_s=2.)
        spec["events"]["faults"] = [first, event]
    name = "two_windows" if repeated else ("dense" if sample_s < 1 else "sparse")
    plan = prepare_run(spec, output_root=destination, run_id=name)
    result = execute_prepared_run(plan.bundle_root, expected_plan_sha256=plan.execution_plan_sha256,
                                  hard_timeout=True, max_attempts=1)
    root = Path(plan.bundle_root)
    evidence = result.validation.injection_evidence
    active = [r for r in result.trace_rows if 4.3 <= r["time_s"] <= 4.7]
    assert verify_run_bundle(root)["ok"]
    data = {"sample_s":sample_s, "frames":len(result.trace_rows), "validation":result.validation.result.value,
            "reason":result.validation.reason_code, "injections":[e.model_dump(mode="json") for e in evidence],
            "window_samples":[[r["time_s"],r["adcs.star_tracker.valid"],r["adcs.fusion.source_mode"]] for r in active],
            "seal_sha256":hashlib.sha256((root/"bundle_manifest.json").read_bytes()).hexdigest()}
    return data


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("spec",type=Path)
    p.add_argument("destination",type=Path)
    p.add_argument("output",type=Path)
    p.add_argument("--repeated", action="store_true", help="Include a sampled 1-2 s event before the unsampled event")
    args=p.parse_args()
    assert not args.destination.exists(), "use a fresh workspace"
    rates = (1.0,) if args.repeated else (0.2, 1.0)
    results=[analyze(args.spec,args.destination,s,args.repeated) for s in rates]
    args.output.write_text(json.dumps(results,indent=2)+"\n",encoding="utf-8")
    for r in results:
        print(r["sample_s"],r["validation"],r["reason"],[(e["event_id"], e["effect_result"]) for e in r["injections"]],r["window_samples"])
