"""Replay the archived point-mass benchmark with the NAIF universal-variable propagator."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path

import numpy as np
import spiceypy as spice


def read_state(root, name, digest):
    data = (root / name).read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(f"Unexpected hash for {name}")
    if name.endswith(".txt"):
        values = np.loadtxt(io.BytesIO(data), delimiter=",")
        return values[:, 0], values[:, 1:] * 1000
    rows = list(csv.DictReader(data.decode().splitlines()))
    columns = ("x_m", "y_m", "z_m", "vx_m_s", "vy_m_s", "vz_m_s")
    return np.array([float(r["time_s"]) for r in rows]), np.array([[float(r[k]) for k in columns] for r in rows])


def stats(errors):
    position = np.linalg.norm(errors[:, :3], axis=1)
    velocity = np.linalg.norm(errors[:, 3:], axis=1)
    return dict(max_position_m=float(position.max()), max_velocity_m_s=float(velocity.max()),
                final_residual_si=errors[-1].tolist())


def audit(root):
    config = json.loads((root / "mu_control_diagnostic.json").read_bytes())
    inputs = [
        ("basilisk_state.csv", config["basilisk_state_sha256"]),
        ("gmat_raw.txt", config["original_gmat_report_sha256"]),
        ("gmat_mu_control_raw.txt", config["matched_mu_report_sha256"]),
    ]
    common_mu = config["mu_actual_basilisk_m3_s2"]
    documented_mu = config["default_gmat_mu_from_independent_documentation_not_runtime_measurement_km3_s2"] * 1e9
    results = []
    nominal = np.arange(91) * 60.
    initial = None
    for name, digest in inputs:
        times, states = read_state(root, name, digest)
        assert states.shape == (91, 6) and np.all(np.diff(times) > 0)
        if initial is None:
            initial = states[0].copy()
        np.testing.assert_array_equal(states[0], initial)
        cases = {}
        for mu_label, mu in (("basilisk", common_mu), ("gmat_documented", documented_mu)):
            for grid, seconds in (("nominal", nominal), ("reported", times)):
                reference = np.array([spice.prop2b(mu, initial, dt) for dt in seconds])
                cases[f"{mu_label}_{grid}"] = stats(states - reference)
        results.append(dict(file=name, sha256=digest, rows=len(times),
                            max_clock_offset_s=float(max(abs(times-nominal))), comparisons=cases))
    return dict(toolkit=spice.tkvrsn("TOOLKIT"), spiceypy=spice.__version__, numpy=np.__version__,
                common_initial_state_si=initial.tolist(), basilisk_mu_m3_s2=common_mu,
                gmat_documented_mu_m3_s2=documented_mu, cases=results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = audit(args.archive)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for case in result["cases"]:
        print(case["file"])
        for label, row in case["comparisons"].items():
            print(label, row["max_position_m"], row["max_velocity_m_s"])
