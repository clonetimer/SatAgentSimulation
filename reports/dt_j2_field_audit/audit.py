"""Compare the native degree-two zonal field with the gradient of its potential."""
import csv
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
from Basilisk.simulation import gravityEffector


def compare(path):
    with path.open() as stream:
        header, *coefficients = csv.reader(stream)
    radius, mu = map(float, header[:2])
    c20 = next(float(row[2]) for row in coefficients if tuple(map(int, row[:2])) == (2, 0))
    j2 = -math.sqrt(5) * c20
    body = gravityEffector.GravBodyData()
    body.useSphericalHarmonicsGravityModel(str(path.resolve()), 2)
    model = body.gravityModel
    model.initializeParameters()
    rows = []
    for altitude in (300_000, 600_000, 1_200_000):
        r = radius + altitude
        for latitude, longitude in ((0, 0), (0, 90), (35, 120), (-60, -40), (90, 0), (-90, 0)):
            lat, lon = np.radians([latitude, longitude])
            unit = np.array([np.cos(lat)*np.cos(lon), np.cos(lat)*np.sin(lon), np.sin(lat)])
            position = unit * r
            central = -mu / r**2 * unit
            factor = 1.5 * mu / r**2 * j2 * (radius / r)**2
            perturbation = factor * unit * (5 * unit[2]**2 - np.array([1, 1, 3]))
            native = np.asarray(model.computeField(position.tolist())).reshape(3)
            residual = native - (central + perturbation)
            rows.append(dict(altitude_m=altitude, latitude_deg=latitude, longitude_deg=longitude,
                             analytical_m_s2=(central + perturbation).tolist(), native_m_s2=native.tolist(),
                             j2_norm_m_s2=float(np.linalg.norm(perturbation)),
                             residual_norm_m_s2=float(np.linalg.norm(residual))))
    return dict(asset_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), mu_m3_s2=mu,
                radius_m=radius, normalized_c20=c20, j2=j2, points=rows)


if __name__ == "__main__":
    result = compare(Path(sys.argv[1]))
    maximum = max(row["residual_norm_m_s2"] for row in result["points"])
    print("maximum acceleration residual (m/s^2)", maximum)
    Path(sys.argv[2]).write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    if maximum > 1e-12:
        raise SystemExit("Numerical identity check failed")
