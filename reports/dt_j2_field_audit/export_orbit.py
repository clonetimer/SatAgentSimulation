"""Export native J2 whole-spacecraft orbit samples and initialized parameters."""
import csv
import json
import sys
from pathlib import Path
from Basilisk.architecture import astroConstants
from sat_sim.bsk_engine.unified_native import UnifiedNativeRuntime, UnifiedRuntimeConfig, WHOLE_UNIFIED_CAPABILITY_ID

out = Path(sys.argv[1])
out.mkdir(exist_ok=True)
for step in (5., 2.):
    config = UnifiedRuntimeConfig(capability_id=WHOLE_UNIFIED_CAPABILITY_ID, duration_s=5400.,
        step_s=step, sample_s=60., adcs_only=False, events=(),
        values=dict(environment_profile="spice_wmm", controller_k=0., controller_p=0.,
                    epoch_utc="2026-01-01T00:00:00Z"))
    result = UnifiedNativeRuntime(config).run()
    keys = [f"orbit.{field}_{axis}" for field in ("position_m", "velocity_m_s") for axis in "xyz"]
    with (out / f"orbit_{int(step)}s.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["time_s", *keys])
        for row in result.trace_rows:
            writer.writerow([row["time_s"], *(row[k] for k in keys)])
    env = result.summary["orbital_environment"]
    metadata = dict(environment=env, sun_mu=astroConstants.MU_SUN*1e9, moon_mu=astroConstants.MU_MOON*1e9)
    (out / "native_environment.json").write_text(json.dumps(metadata, indent=2))
