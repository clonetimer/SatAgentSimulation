"""Isolate Sun/Moon gravity at fixed J2000 states using the project gravity setup."""
import argparse
import json
from pathlib import Path

import Basilisk
import numpy as np
from Basilisk.simulation import gravityEffector, spacecraft
from Basilisk.utilities import SimulationBaseClass, macros, simIncludeGravBody
from sat_sim.bsk_engine.orbital_environment import NativeOrbitalEnvironment

LIMIT = 1e-12  # m/s^2; numerical isolation check at a fixed state
POSITIONS = ([7000000., 0., 0.], [0., -7100000., 0.], [3000000., 2000000., 6100000.])
GROUPS = {"earth": ("earth",), "sun": ("earth","sun"), "moon": ("earth","moon"),
          "both": ("earth","sun","moon")}


def compare(epoch):
    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("gravity")
    process.addTask(sim.CreateNewTask("native", macros.sec2nano(1)))
    with NativeOrbitalEnvironment(dict(environment_profile="spice_wmm", epoch_utc=epoch), 1) as env:
        factory = simIncludeGravBody.gravBodyFactory()
        factory.createEarth().isCentralBody = True
        full = spacecraft.Spacecraft()

        def attach(model, name, source, priority):
            sim.AddModelToTask("native", model, priority)

        env.configure_gravity(factory, factory.gravBodies["earth"], full, attach)
        cases = {}
        for key, names in GROUPS.items():
            sc = full if key == "both" else spacecraft.Spacecraft()
            if key != "both":
                sc.gravField.setGravBodies(gravityEffector.GravBodyVector([factory.gravBodies[n] for n in names]))
            sc.ModelTag = key
            sc.hub.mHub = 100.
            sc.hub.IHubPntBc_B = np.eye(3).tolist()
            sc.hub.r_CN_NInit = POSITIONS[0]
            sc.hub.v_CN_NInit = [0., 7500., 0.]
            sim.AddModelToTask("native", sc)
            cases[key] = sc
        sim.InitializeSimulation()
        sim.ConfigureStopTime(0)
        sim.ExecuteSimulation()
        indices = list(factory.gravBodies)
        body_pos = {n: np.array(env.spice.planetStateOutMsgs[indices.index(n)].read().PositionVector)
                    for n in ("earth", "sun", "moon")}
        assert np.linalg.norm(body_pos["earth"]) < 1e-3
        rows = []
        for position in POSITIONS:
            acc = {}
            for name, sc in cases.items():
                sc.gravField.computeGravityField(position, [0.,7500.,0.])
                acc[name] = np.array(sc.dynManager.getPropertyReference(sc.gravField.vehicleGravityPropName)).reshape(3)
            expected = {}
            for name in ("sun", "moon"):
                r = body_pos[name] - body_pos["earth"]
                delta = r - np.asarray(position)
                expected[name] = factory.gravBodies[name].mu * (delta / np.linalg.norm(delta)**3 - r / np.linalg.norm(r)**3)
            expected["both"] = expected["sun"] + expected["moon"]
            for name, reference in expected.items():
                actual = acc[name] - acc["earth"]
                rows.append(dict(group=name, position_m=position, native_delta_m_s2=actual.tolist(),
                                 reference_m_s2=reference.tolist(), residual_norm_m_s2=float(np.linalg.norm(actual-reference))))
        return dict(epoch_utc=epoch, positions_j2000_m={k:v.tolist() for k,v in body_pos.items()},
                    mu_m3_s2={k:factory.gravBodies[k].mu for k in ("sun","moon")},
                    kernels={Path(a["path"]).name:a["sha256"] for a in env.assets["spice"]}, rows=rows)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("output", type=Path)
    args = p.parse_args()
    epochs = [compare(e) for e in ("2025-03-20T12:00:00Z","2026-01-01T00:00:00Z","2027-09-23T12:00:00Z")]
    maximum = max(row["residual_norm_m_s2"] for e in epochs for row in e["rows"])
    args.output.write_text(json.dumps(dict(candidate="d6fec67",basilisk=Basilisk.__version__, epochs=epochs,
                                          tolerance_m_s2=LIMIT,max_residual_m_s2=maximum,passed=maximum<=LIMIT),
                                      indent=2)+"\n",encoding="utf8")
    print(maximum)
    raise SystemExit(0 if maximum<=LIMIT else 1)
