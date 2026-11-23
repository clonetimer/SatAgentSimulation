"""Compare spherical Earth-limb geometry with native star-tracker validity."""
import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation
from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle

RADIUS_M = 6371000.0
HALF_FOV_DEG = 10.0


def measure(source, root, pitch, enable):
    task = json.loads(source.read_bytes())
    for values in (task["parameters"]["values"], task["model"]["config"]):
        values["star_tracker_mount_pitch_deg"] = pitch
        if enable:
            values["star_tracker_earth_occlusion_half_fov_deg"] = HALF_FOV_DEG
    plan = prepare_run(task, output_root=root, run_id=f"pitch_{pitch}")
    result = execute_prepared_run(plan.bundle_root, expected_plan_sha256=plan.execution_plan_sha256,
                                  hard_timeout=True, max_attempts=1)
    bundle = Path(plan.bundle_root)
    assert verify_run_bundle(bundle)["ok"]
    path = bundle / "results/telemetry.jsonl"
    samples = []
    for line in path.read_bytes().splitlines():
        row = json.loads(line)
        pos = np.array([row[f"orbit.position_m_{a}"] for a in "xyz"])
        nadir = -pos / np.linalg.norm(pos)
        limb = math.degrees(math.asin(RADIUS_M / np.linalg.norm(pos)))
        # Scalar-first Basilisk Euler parameters; transpose of passive C_BN is
        # the active body-to-inertial rotation returned by SciPy.
        q = [row[f"attitude.truth.quaternion_BN_q{i}"] for i in (1, 2, 3, 0)]
        inertial_from_body = Rotation.from_quat(q)
        cfg = task["parameters"]["values"]
        angles = [cfg.get(f"star_tracker_mount_{a}_deg", 0.0) for a in ("yaw", "pitch", "roll")]
        inertial_axis = (inertial_from_body * Rotation.from_euler("ZYX", angles, degrees=True)).apply([0, 0, 1])
        sep = math.degrees(math.acos(np.clip(nadir @ inertial_axis, -1, 1)))
        label = "fully_obscured" if sep + HALF_FOV_DEG < limb else (
            "clear" if sep - HALF_FOV_DEG > limb else "partial")
        samples.append(dict(time_s=row["time_s"], limb_radius_deg=limb, separation_deg=sep,
                            classification=label, native_valid=row["adcs.star_tracker.valid"]))
    return dict(pitch_deg=pitch, run_validation=result.validation.result.value, samples=samples,
                telemetry_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                task_sha256=hashlib.sha256((bundle/"input/task_spec.json").read_bytes()).hexdigest(),
                manifest_sha256=hashlib.sha256((bundle/"bundle_manifest.json").read_bytes()).hexdigest())


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for arg in ("source", "root", "output"):
        p.add_argument(arg, type=Path)
    p.add_argument("--enable", action="store_true")
    a = p.parse_args()
    cases = [measure(a.source, a.root.resolve(), pitch, a.enable) for pitch in (-80, 80)]
    report = dict(earth_radius_m=RADIUS_M, assumed_half_fov_deg=HALF_FOV_DEG,
                  assumed_boresight_case=[0, 0, 1], cases=cases)
    a.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
    for case in cases:
        print(case["pitch_deg"], [(s["classification"],s["native_valid"]) for s in case["samples"][1:]])
        expected = "fully_obscured" if case["pitch_deg"] < 0 else "clear"
        assert len(case["samples"]) == 16
        assert all(s["classification"] == expected and s["native_valid"] == int(not a.enable or expected == "clear")
                   for s in case["samples"][1:])
