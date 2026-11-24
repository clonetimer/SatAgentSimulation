"""Propagate the shared initial state using SPICE frames and an analytic J2 field."""
import csv
import json
import sys
from pathlib import Path

import numpy as np
import spiceypy as spice
from scipy.integrate import solve_ivp


def compare(root):
    metadata = json.loads((root / "native_environment.json").read_bytes())
    env = metadata["environment"]
    for asset in env["assets"]["spice"]:
        spice.furnsh(asset["path"])
    et = spice.str2et(env["epoch_utc"][:19])
    gravity = env["initialized_earth_gravity"]
    with Path(env["assets"]["gravity_j2"]["path"]).open() as stream:
        header, *rows = csv.reader(stream)
    j2 = -np.sqrt(5) * next(float(row[2]) for row in rows if tuple(map(int,row[:2])) == (2,0))
    tracks = {step: np.loadtxt(root / f"orbit_{step}s.csv", delimiter=",", skiprows=1) for step in (5,2)}
    time = tracks[2][:,0]
    assert np.array_equal(time, tracks[5][:,0]) and np.array_equal(tracks[2][0], tracks[5][0])
    results = {}
    for case in ("effective", "file_constants", "identity_frame"):
        mu, radius = gravity["harmonics_mu_m3_s2"], gravity["harmonics_equatorial_radius_m"]
        if case == "file_constants":
            radius, mu = map(float, header[:2])

        def derivative(t, y):
            r = y[:3]
            norm = np.linalg.norm(r)
            pole = np.array([0.,0.,1.]) if case == "identity_frame" else spice.pxform("IAU_EARTH","J2000",et+t)[:,2]
            u = r / norm
            z = np.dot(pole, u)
            acc = -mu / norm**2 * u + 1.5*mu*j2*radius**2/norm**4 * ((5*z*z-1)*u-2*z*pole)
            for name, gm in (("SUN",metadata["sun_mu"]), ("MOON",metadata["moon_mu"])):
                pos = 1000 * spice.spkpos(name,et+t,"J2000","NONE","EARTH")[0]
                diff = pos-r
                acc += gm*(diff/np.linalg.norm(diff)**3 - pos/np.linalg.norm(pos)**3)
            return np.r_[y[3:],acc]

        ref = solve_ivp(derivative,(0,time[-1]),tracks[2][0,1:],t_eval=time,method="DOP853",
                        rtol=2e-13,atol=1e-10,max_step=30.)
        assert ref.success
        result = {}
        for step, track in tracks.items():
            delta = ref.y.T-track[:,1:]
            result[str(step)] = dict(max_position_m=np.linalg.norm(delta[:,:3],axis=1).max(),
                                     max_velocity_m_s=np.linalg.norm(delta[:,3:],axis=1).max(),
                                     final_position_m=np.linalg.norm(delta[-1,:3]))
        results[case] = result
    return results


if __name__ == "__main__":
    root = Path(sys.argv[1])
    results = compare(root)
    print(json.dumps(results, indent=2))
    (root / "comparison.json").write_text(json.dumps(results, indent=2)+"\n")
