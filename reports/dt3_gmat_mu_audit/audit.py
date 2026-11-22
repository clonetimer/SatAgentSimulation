"""Infer two-body mu from variations of the eccentricity vector in archived states."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np


def estimate(path, receipt):
    identity = hashlib.sha256(path.read_bytes()).hexdigest()
    assert identity == receipt["normalized_csv_sha256"]
    with path.open(encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == receipt["rows"] == 91
    r = np.array([[float(row[k]) for k in ("x_m", "y_m", "z_m")] for row in rows])
    v = np.array([[float(row[k]) for k in ("vx_m_s", "vy_m_s", "vz_m_s")] for row in rows])
    raw = path.with_name(path.name.replace("_state.csv", "_raw.txt"))
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == receipt["report_sha256"]
    source_states = np.loadtxt(raw, delimiter=",")[:, 1:] * 1000
    np.testing.assert_array_equal(source_states, np.column_stack((r, v)))
    h = np.cross(r, v)
    y = np.cross(v, h)
    x = r / np.linalg.norm(r, axis=1)[:, None]
    # v x h = mu * unit(r) + a constant vector for a Kepler orbit.
    # Remove the unknown constant before least squares. Input units are SI.
    x -= x.mean(axis=0)
    y -= y.mean(axis=0)
    fit = np.sum(x*y) / np.sum(x*x)
    return dict(source_sha256=identity, rows=len(rows), fitted_km3_s2=float(fit/1e9),
                max_vector_residual_m3_s2=float(np.linalg.norm(y-fit*x, axis=1).max()))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = {}
    for case, stem in [("nominal", "gmat"), ("controlled", "gmat_mu_control")]:
        receipt = json.loads((args.artifact / (stem+"_execution.json")).read_text())
        result[case] = estimate(args.artifact / (stem+"_state.csv"), receipt)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(result)
