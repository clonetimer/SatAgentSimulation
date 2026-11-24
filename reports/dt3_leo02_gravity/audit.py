"""LEO-02 internal gravity truncation comparison against higher-degree GGM03S."""
import argparse
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
from sat_sim.bsk_engine.gravity_harmonics_asset import GRAVITY_CHOICES
from sat_sim.bsk_engine.unified_native import UnifiedNativeRuntime, UnifiedRuntimeConfig, WHOLE_UNIFIED_CAPABILITY_ID


def run(degree, step):
    label = f"ggm03s_degree{degree}"
    # Add reference-only labels to this isolated experiment's in-process map.
    with patch.dict(GRAVITY_CHOICES, {label:degree}):
        result = UnifiedNativeRuntime(UnifiedRuntimeConfig(
            capability_id=WHOLE_UNIFIED_CAPABILITY_ID, duration_s=1800., step_s=step,
            sample_s=20., adcs_only=False, events=(), values=dict(
                earth_gravity_model=label, environment_profile="spice_wmm",
                epoch_utc="2026-01-01T00:00:00Z", orbit_radius_m=7000000.,
                eccentricity=.001, inclination_deg=52., raan_deg=25., arg_perigee_deg=45.,
                controller_k=0., controller_p=0.))).run()
    assert result.summary["orbital_environment"]["gravity_degree"] == degree
    assert result.summary["runtime_message_read_error_count"] == 0
    np.testing.assert_allclose([row["time_s"] for row in result.trace_rows],
                               np.arange(0,1801,20), rtol=0, atol=1e-9)
    states = np.array([[row[f"orbit.{field}_{axis}"] for field in ("position_m","velocity_m_s")
                        for axis in "xyz"] for row in result.trace_rows])
    asset = result.summary["orbital_environment"]["gravity_source"]["sha256"]
    return states, asset


def residual(source, ref):
    np.testing.assert_allclose(source[0], ref[0], rtol=0, atol=1e-8)
    pos, vel = ref[:,:3], ref[:,3:]
    radial = pos / np.linalg.norm(pos, axis=1)[:,None]
    normal = np.cross(pos,vel)
    normal /= np.linalg.norm(normal,axis=1)[:,None]
    basis = np.stack((radial,np.cross(normal,radial),normal),axis=1)
    diff = source-ref
    return {label:dict(end_rtn=(basis[-1] @ vector[-1]).tolist(),
                       max_norm=float(np.linalg.norm(vector,axis=1).max()))
            for label,vector in [("position_m",diff[:,:3]),("velocity_m_s",diff[:,3:])]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    states, assets = {}, set()
    for degree,step in [(2,5),(3,5),(4,5),(8,5),(16,5),(32,5),(64,5),(32,2.5),(32,1.25)]:
        states[degree,step], h = run(degree,step)
        assets.add(h)
    assert len(assets) == 1
    report = dict(asset_sha256=assets.pop(), samples=91,
        same_step_vs_degree32={str(d):residual(states[d,5],states[32,5]) for d in (2,3,4,8,16)},
        same_step_vs_degree64={str(d):residual(states[d,5],states[64,5]) for d in (2,3,4,8,16,32)},
        reference_steps={f"{a}_vs_{b}":residual(states[32,a],states[32,b]) for a,b in [(5,2.5),(2.5,1.25)]})
    import csv
    with args.output.with_suffix(".csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["degree_order", "step_s", "time_s", "x_m", "y_m", "z_m", "vx_m_s", "vy_m_s", "vz_m_s"])
        for (degree, step), state in states.items():
            for i, values in enumerate(state):
                writer.writerow([degree, step, i*20, *values])
    args.output.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report))
