"""Recompute body-frame cannonball drag from sealed dense telemetry."""
import argparse
import json
import math
from pathlib import Path

from sat_sim.run_bundle import verify_run_bundle


def audit(bundle):
    if not verify_run_bundle(bundle)["ok"]:
        raise ValueError(f"Invalid Run Bundle: {bundle}")
    summary = json.loads((bundle / "results/summary.json").read_bytes())
    env = summary["orbital_environment"]
    factor = -0.5 * env["drag_area_m2"] * env["drag_coefficient"]
    step = json.loads((bundle / "input/task_spec.json").read_bytes())["simulation"]["step_s"]
    maxima = {"same_frame_n": 0.0, "previous_step_n": 0.0}
    previous = None
    count = 0
    with (bundle / "results/telemetry.jsonl").open("rb") as stream:
        for line in stream:
            row = json.loads(line)
            density = row["environment.atmosphere_density_kg_m3"]
            force = tuple(row[f"orbit.drag_force_b_{axis}"] for axis in "xyz")
            velocity = tuple(row[f"orbit.drag_relative_velocity_b_m_s_{axis}"] for axis in "xyz")
            speed = math.hypot(*velocity)
            if previous is None:
                first = {"time_s": row["time_s"], "density_kg_m3": density, "force_n": math.hypot(*force)}
            else:
                if abs(row["time_s"] - previous["time_s"] - step) > 1e-8:
                    raise ValueError("Expected telemetry at every dynamics step")
                for key, source in (("same_frame_n", density), ("previous_step_n", previous["density"])):
                    expected = [factor * source * speed * v for v in velocity]
                    maxima[key] = max(maxima[key], math.dist(expected, force))
            previous = {"time_s": row["time_s"], "density": density}
            count += 1
    if count < 2:
        raise ValueError("Expected positive-time samples")
    return {"step_s": step, "rows": count, "first_row": first, "max_vector_error_after_start": maxima,
            "sealed_manifest_sha256": json.loads((bundle / "SEALED.json").read_bytes())["bundle_manifest_sha256"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundles", nargs="+", type=Path)
    args = parser.parse_args()
    print(json.dumps([audit(bundle) for bundle in args.bundles], indent=2, allow_nan=False))
