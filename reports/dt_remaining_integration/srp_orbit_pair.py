"""Use the sealed LEO-03 small-area run as a control for a single SRP area change."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle


def vector(row, prefix):
    return np.array([row[f"{prefix}_{axis}"] for axis in "xyz"])


def extract(root):
    assert verify_run_bundle(root)["ok"]
    raw = (root/"results/telemetry.jsonl").read_bytes()
    return list(map(json.loads, raw.splitlines())), {
        "telemetry_sha256": hashlib.sha256(raw).hexdigest(),
        "manifest_sha256": hashlib.sha256((root/"bundle_manifest.json").read_bytes()).hexdigest(),
        "validation": json.loads((root/"run_record.json").read_bytes())["validation_result"],
    }


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    for k in ("baseline","destination","output"):
        p.add_argument(k,type=Path)
    args=p.parse_args()
    base, base_identity=extract(args.baseline)
    task=json.loads((args.baseline/"input/task_spec.json").read_bytes())
    values=task["parameters"]["values"]
    assert values["srp_reference_area_m2"] == 20 and values["srp_model"] == "cannonball"
    values["srp_reference_area_m2"]=40
    if "srp_reference_area_m2" in task["model"]["config"]:
        task["model"]["config"]["srp_reference_area_m2"]=40
    plan=prepare_run(task,output_root=args.destination.resolve(),run_id="area_40")
    execute_prepared_run(plan.bundle_root,expected_plan_sha256=plan.execution_plan_sha256,
                         hard_timeout=True,max_attempts=1)
    changed, identity=extract(Path(plan.bundle_root))
    samples=[]
    for l,r in zip(base,changed,strict=True):
        assert l["time_s"]==r["time_s"]
        separation=vector(r,"orbit.position_m")-vector(l,"orbit.position_m")
        dv=vector(r,"orbit.velocity_m_s")-vector(l,"orbit.velocity_m_s")
        force=vector(l,"orbit.srp_force_n")
        sun_ray=vector(l,"orbit.position_m")-vector(l,"orbit.sun_position_m")
        f=np.linalg.norm(force)
        cosine=float(force@sun_ray/(f*np.linalg.norm(sun_ray))) if f else None
        double_force=r["orbit.srp_force_norm_n"]
        if l["orbit.eclipse_factor"]==0:
            assert f==double_force==0
        else:
            assert cosine>0.999999 and abs(double_force/f-2)<0.001
        samples.append(dict(time_s=l["time_s"],illumination=l["orbit.eclipse_factor"],
                            force_20_n=f,force_40_n=double_force,away_from_sun_cosine=cosine,
                            position_delta_m=separation.tolist(),velocity_delta_m_s=dv.tolist()))
    assert len(samples)==91 and any(0<s["illumination"]<1 for s in samples)
    assert samples[0]["illumination"]==1 and samples[-1]["illumination"]==0
    assert np.linalg.norm(samples[0]["position_delta_m"])==np.linalg.norm(samples[0]["velocity_delta_m_s"])==0
    final=np.linalg.norm(samples[-1]["position_delta_m"])
    assert final>0 and np.dot(samples[-1]["position_delta_m"],vector(base[0],"orbit.srp_force_n"))>0
    args.output.write_text(json.dumps(dict(baseline=base_identity,changed=identity,
        final_separation_m=final,samples=samples),indent=2)+"\n",encoding="utf8")
    print(final)