"""Seal one four-effect orbit, or independently audit its geometric eclipse and SRP."""
import argparse
import json
import math
from pathlib import Path

import numpy as np
from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle


def simulate(source, root, step):
    task = json.loads(source.read_bytes())
    task["simulation"].update(duration_s=5400., step_s=step, sample_s=step)
    plan = prepare_run(task, output_root=root, run_id="observed-eclipse")
    result = execute_prepared_run(plan.bundle_root, expected_plan_sha256=plan.execution_plan_sha256,
                                  hard_timeout=True, max_attempts=1)
    assert result.validation.result.value == "PASS"


def visible(a, b, d):
    # Angular radii of the Sun and Earth, and separation of the disk centers.
    if d >= a + b:
        return 1.
    if d <= abs(b - a):
        return max(0., 1. - (b/a)**2)
    x = (d*d+a*a-b*b)/(2*d)
    y = math.sqrt(max(0., a*a-x*x))
    overlap = a*a*math.atan2(y,x) + b*b*math.atan2(y,d-x) - d*y
    return 1.-overlap/(math.pi*a*a)


def audit(bundle):
    assert verify_run_bundle(bundle)["ok"]
    summary = json.loads((bundle/"results/summary.json").read_bytes())
    params = summary["solar_radiation_pressure"]
    read_step = json.loads((bundle/"input/task_spec.json").read_bytes())["simulation"]["step_s"]
    groups = {}
    transition = []
    previous_position = None
    max_input_geometry_error = 0.
    with (bundle/"results/telemetry.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            r = json.loads(line)
            def vector(stem):
                return np.array([r[f"orbit.{stem}_{axis}"] for axis in "xyz"])
            spacecraft = vector("position_m")
            sun = vector("sun_position_m")-spacecraft
            distance = float(np.linalg.norm(sun))
            a = math.asin(695000000./distance)
            b = math.asin(6378136.6/np.linalg.norm(spacecraft))
            sep = math.atan2(np.linalg.norm(np.cross(sun, -spacecraft)), sun@(-spacecraft))
            light = visible(a,b,sep)
            group = "sun" if light == 1. else "umbra" if light == 0. else "partial"
            native = r["orbit.eclipse_factor"]
            if previous_position is not None:
                previous_sun = vector("sun_position_m") - previous_position
                angle = math.atan2(np.linalg.norm(np.cross(previous_sun,-previous_position)),
                                   previous_sun @ (-previous_position))
                lagged = visible(math.asin(695000000./np.linalg.norm(previous_sun)),
                                 math.asin(6378136.6/np.linalg.norm(previous_position)), angle)
                max_input_geometry_error = max(max_input_geometry_error, abs(lagged-native))
            previous_position = spacecraft
            scale = (1361./299792458.)*params["reference_area_m2"]*params["coefficient_reflection"]*(149597870693./distance)**2
            force = vector("srp_force_n")
            geom_error = float(np.linalg.norm(force+scale*light*sun/distance))
            conditional_error = float(np.linalg.norm(force+scale*native*sun/distance))
            entry = groups.setdefault(group, dict(samples=0,max_light_error=0.,max_force_error_n=0.,max_force_error_native_light_n=0.))
            entry["samples"] += 1
            for key, error in (("max_light_error",abs(light-native)),("max_force_error_n",geom_error),
                               ("max_force_error_native_light_n",conditional_error)):
                entry[key] = max(entry[key], error)
            if 0 < light < 1 or 0 < native < 1:
                transition.append(dict(t=r["time_s"], geometric=light, recorded=native, force_n=float(np.linalg.norm(force))))
    assert set(groups) == {"sun","partial","umbra"} and sum(v["samples"] for v in groups.values()) == 1 + round(5400/read_step)
    return dict(step_s=read_step,max_previous_position_light_error=max_input_geometry_error,groups=groups,transition=transition,weather=summary["orbital_environment"]["space_weather"]["sha256"],
                manifest=json.loads((bundle/"SEALED.json").read_bytes())["bundle_manifest_sha256"])


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root",type=Path)
    p.add_argument("--source",type=Path)
    p.add_argument("--step",type=float,choices=(1.,2.),default=2.)
    args=p.parse_args()
    if args.source:
        simulate(args.source,args.root,args.step)
    result = audit(args.root/"observed-eclipse")
    (args.root/"geometry.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print(result["groups"], result["transition"])
