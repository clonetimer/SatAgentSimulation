"""Compare the project's native WMM model at fixed NOAA geodetic test points."""
import argparse
import hashlib
import json
from pathlib import Path

import Basilisk
import numpy as np
from Basilisk.architecture import messaging
from Basilisk.utilities import SimulationBaseClass, macros
from sat_sim.bsk_engine.orbital_environment import NativeOrbitalEnvironment

SOURCE = "https://www.ncei.noaa.gov/sites/g/files/anmtlf171/files/2025-02/WMM2025testvalues.pdf"
# WGS84 height (km), geodetic latitude/longitude (degrees), north/east/down (nT).
REFERENCE = {
    "2025-01-01T00:00:00Z": [
        (0, 80, 0, 6521.6, 145.9, 54791.5), (0, 0, 120, 39677.8, -109.6, -10580.2),
        (0, -80, 240, 6117.5, 15751.9, -52022.5), (100, 80, 0, 6216, 92.4, 52598.8),
        (100, 0, 120, 37688.6, -96.2, -10152.1), (100, -80, 240, 5907.6, 14780.3, -49540.7),
    ],
    "2027-07-02T12:00:00Z": [
        (0, 80, 0, 6500.8, 294.5, 54869.4), (0, 0, 120, 39701.6, -167.4, -10381.8),
        (0, -80, 240, 6200.7, 15730.3, -51783.7), (100, 80, 0, 6196.7, 233.8, 52670.5),
        (100, 0, 120, 37711.5, -148.7, -9969.8), (100, -80, 240, 5984, 14760.1, -49317.7),
    ],
}


def evaluate(epoch, height, latitude, longitude, rotation_deg):
    phi, lam = np.radians([latitude, longitude])
    c, s = np.cos(phi), np.sin(phi)
    cl, sl = np.cos(lam), np.sin(lam)
    f = 1 / 298.257223563
    e2 = f * (2 - f)
    n = 6378137 / np.sqrt(1 - e2 * s**2)
    h = height * 1000
    position = [(n+h)*c*cl, (n+h)*c*sl, (n*(1-e2)+h)*s]
    ned = np.array([[-s*cl, -s*sl, c], [-sl, cl, 0], [-c*cl, -c*sl, -s]])
    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("process")
    process.addTask(sim.CreateNewTask("field", macros.sec2nano(1)))
    earth = messaging.SpicePlanetStateMsgPayload()
    angle = np.radians(rotation_deg)
    ca, sa = np.cos(angle), np.sin(angle)
    fixed_from_inertial = np.array([[ca, sa, 0], [-sa, ca, 0], [0, 0, 1]])
    earth.J20002Pfix = fixed_from_inertial.tolist()
    planet = messaging.SpicePlanetStateMsg().write(earth)
    state = messaging.SCStatesMsgPayload()
    state.r_BN_N = (fixed_from_inertial.T @ position).tolist()
    sc = messaging.SCStatesMsg().write(state)
    env = NativeOrbitalEnvironment({"environment_profile":"spice_wmm", "epoch_utc":epoch}, 1)
    model = env.magnetic_model(planet)
    model.addSpacecraftToModel(sc)
    sim.AddModelToTask("field", model)
    sim.InitializeSimulation()
    sim.ConfigureStopTime(0)
    sim.ExecuteSimulation()
    field = np.asarray(model.envOutMsgs[0].read().magField_N)
    return ned @ fixed_from_inertial @ field * 1e9, Path(model.wmmDataFullPath)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    rows = []
    for epoch, points in REFERENCE.items():
        for height, lat, lon, *expected in points:
            for rotation in (0, 73):
                actual, cof = evaluate(epoch, height, lat, lon, rotation)
                rows.append(dict(epoch=epoch, height_km=height, latitude_deg=lat, longitude_deg=lon,
                                 fixed_frame_rotation_deg=rotation, reference_nT=expected,
                                 native_nT=actual.tolist(), residual_nT=(actual - expected).tolist()))
    maximum = max(abs(v) for row in rows for v in row["residual_nT"])
    result = dict(source=SOURCE, basilisk=Basilisk.__version__, coefficient_sha256=hashlib.sha256(cof.read_bytes()).hexdigest(),
                  cases=rows, max_abs_component_error_nT=maximum, component_tolerance_nT=0.1, passed=maximum <= 0.1)
    args.output.write_text(json.dumps(result, indent=2)+"\n", encoding="utf8")
    print(maximum, result["passed"])
    raise SystemExit(0 if result["passed"] else 1)
