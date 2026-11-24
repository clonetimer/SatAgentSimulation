"""Compare an unmodified reference Run Bundle to the instrumented dense run."""
import argparse
import hashlib
import json
import math
from pathlib import Path

from sat_sim.run_bundle import verify_run_bundle


def compare(reference, candidate):
    for bundle in (reference, candidate):
        if not verify_run_bundle(bundle)["ok"]:
            raise ValueError(f"Invalid bundle: {bundle}")
    hashes = {}
    for name in ("input/task_spec.json", "validation/validation_outcome.json"):
        original = (reference / name).read_bytes()
        if (candidate / name).read_bytes() != original:
            raise ValueError(f"Changed artifact: {name}")
        hashes[name] = hashlib.sha256(original).hexdigest()
    step = json.loads((reference / "input/task_spec.json").read_bytes())["simulation"]["step_s"]
    env = json.loads((candidate / "results/summary.json").read_bytes())["orbital_environment"]
    scale = -0.5 * env["drag_area_m2"] * env["drag_coefficient"]
    count = values = 0
    max_error = 0.
    previous = None
    name = "results/telemetry.jsonl"
    with (reference / name).open("rb") as left, (candidate / name).open("rb") as right:
        for before, after in zip(left, right, strict=True):
            original, row = json.loads(before), json.loads(after)
            density = row.pop("orbit.drag_input_density_kg_m3")
            source_t = row.pop("orbit.drag_density_source_time_s")
            if original != row:
                raise ValueError(f"Changed original telemetry at {row['time_s']} s")
            if previous is None:
                if row["time_s"] != 0. or density != 0.:
                    raise ValueError("Unexpected initialization")
            elif (abs(row["time_s"] - previous["time_s"] - step) > 1e-8
                  or abs(source_t - previous["time_s"]) > 1e-8
                  or density != previous["environment.atmosphere_density_kg_m3"]):
                raise ValueError(f"Density source mismatch at {row['time_s']} s")
            velocity = tuple(row[f"orbit.drag_relative_velocity_b_m_s_{a}"] for a in "xyz")
            force = tuple(row[f"orbit.drag_force_b_{a}"] for a in "xyz")
            predicted = [scale * density * math.hypot(*velocity) * v for v in velocity]
            max_error = max(max_error, math.dist(force, predicted))
            values += len(row)
            count += 1
            previous = row
    if count < 2 or max_error > 1e-15:
        raise ValueError(f"Force reconstruction outside numerical check: {count=}, {max_error=}")
    return {"frames": count, "unchanged_original_field_values": values, "max_vector_error_n": max_error,
            "unchanged_file_sha256": hashes, "weather_sha256": env["space_weather"]["sha256"],
            "manifest_sha256": [json.loads((p / "SEALED.json").read_bytes())["bundle_manifest_sha256"]
                                for p in (reference, candidate)]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    print(json.dumps(compare(args.reference, args.candidate), indent=2))
