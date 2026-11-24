"""Request all registry-supported whole-spacecraft couplings in a real sealed run."""
import argparse
import hashlib
import json
from pathlib import Path
from sat_sim.capability_registry import get_capability
from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("input",type=Path)
    parser.add_argument("destination",type=Path)
    parser.add_argument("output",type=Path)
    args=parser.parse_args()
    spec=json.loads(args.input.read_bytes())
    contract=get_capability(spec["model"]["capability_id"]).data
    ids=contract["integration_contract"]["couplings"]["supported"]
    spec["mission"]["required_couplings"]=ids
    plan=prepare_run(spec,output_root=args.destination,run_id="required_couplings")
    result=execute_prepared_run(plan.bundle_root,expected_plan_sha256=plan.execution_plan_sha256,
                                hard_timeout=True,max_attempts=1)
    root=Path(plan.bundle_root)
    health=json.loads((root/"results/summary.json").read_bytes())
    def locate(d):
        if not isinstance(d,dict):
            return None
        if "causal_requirement_evidence" in d:
            return d["causal_requirement_evidence"]
        return next((v for x in d.values() if (v:=locate(x)) is not None),None)
    evidence=locate(health)
    assert verify_run_bundle(root)["ok"]
    assert sorted(x["coupling_id"] for x in evidence["requirements"])==sorted(ids)
    data=dict(input_sha256=hashlib.sha256((root/"input/task_spec.json").read_bytes()).hexdigest(),
              telemetry_sha256=hashlib.sha256((root/"results/telemetry.jsonl").read_bytes()).hexdigest(),
              manifest_sha256=hashlib.sha256((root/"bundle_manifest.json").read_bytes()).hexdigest(),
              frames=len(result.trace_rows),validation=result.validation.result.value,
              coupling_evidence=evidence,seal_verified=True)
    args.output.write_text(json.dumps(data,indent=2)+"\n",encoding="utf8")
    for item in evidence["requirements"]:
        print(item["coupling_id"],item["status"])

