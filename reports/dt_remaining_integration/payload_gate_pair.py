"""Change only the payload pointing limit and compare sealed whole-spacecraft runs."""
import argparse
import hashlib
import json
from pathlib import Path
from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle

FIELDS = ["time_s", "adcs.pointing_error_deg", "payload.active", "payload.generated_bps",
          "eps.pdu.payload_enabled", "thermal.safe", "comm.command_permitted"]


def evaluate(source, destination, limit):
    spec = json.loads(source.read_bytes())
    spec["mission"]["required_couplings"] = ["adcs_pointing_to_payload_gate"]
    spec["parameters"]["values"]["payload_max_pointing_error_deg"] = limit
    spec["model"]["config"]["payload_max_pointing_error_deg"] = limit
    plan = prepare_run(spec, output_root=destination, run_id=f"limit_{limit}")
    result = execute_prepared_run(plan.bundle_root, expected_plan_sha256=plan.execution_plan_sha256,
                                  hard_timeout=True, max_attempts=1)
    root = Path(plan.bundle_root)
    assert verify_run_bundle(root)["ok"]
    file = root/"results/telemetry.jsonl"
    return dict(limit_deg=limit, validation=result.validation.result.value,
                manifest_sha256=hashlib.sha256((root/"bundle_manifest.json").read_bytes()).hexdigest(),
                telemetry_sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
                rows=[{k:row[k] for k in FIELDS} for row in map(json.loads,file.read_bytes().splitlines())])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("source","destination","output"):
        parser.add_argument(name,type=Path)
    args = parser.parse_args()
    blocked, allowed = (evaluate(args.source,args.destination,v) for v in (20,175))
    matches = []
    for left,right in zip(blocked["rows"], allowed["rows"],strict=True):
        assert left["time_s"] == right["time_s"]
        if (left["time_s"] > 0 and all(r["eps.pdu.payload_enabled"] == r["thermal.safe"] == 1 and
                20 < r["adcs.pointing_error_deg"] < 175 for r in (left,right))):
            matches.append(left["payload.active"] == left["payload.generated_bps"] == 0 and
                           right["payload.active"] == 1 and right["payload.generated_bps"] > 0)
    report = dict(candidate="d6fec67",compared_samples=len(matches), responding_samples=sum(matches),
                  restrictive=blocked,permissive=allowed)
    args.output.write_text(json.dumps(report,indent=2)+"\n",encoding="utf8")
    assert matches and all(matches)
    print(len(matches),blocked["validation"], allowed["validation"])
