"""Read native gravity binding before and after whole-spacecraft initialization."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

from Basilisk.utilities import SimulationBaseClass
from sat_sim.bsk_engine.orbital_environment import NativeOrbitalEnvironment
from sat_sim.bsk_engine.unified_native import UnifiedNativeRuntime, UnifiedRuntimeConfig, WHOLE_UNIFIED_CAPABILITY_ID


def run(choice):
    states = {}
    earth = None
    configure = NativeOrbitalEnvironment.configure_gravity
    initialize = SimulationBaseClass.SimBaseClass.InitializeSimulation

    def capture():
        model = earth.gravityModel
        return dict(body_mu=earth.mu, body_radius=earth.radEquator,
                    model_mu=model.muBody, model_radius=model.radEquator, degree=model.maxDeg)

    def observe_configuration(env, factory, body, spacecraft, add):
        nonlocal earth
        configure(env, factory, body, spacecraft, add)
        earth = body
        states["after_configuration"] = capture()
        states["file_header"] = list(map(float, env.gravity_path.open().readline().split(",")[:2]))

    def observe_initialization(sim, *args, **kwargs):
        outcome = initialize(sim, *args, **kwargs)
        states["after_native_initialization"] = capture()
        return outcome

    config = UnifiedRuntimeConfig(capability_id=WHOLE_UNIFIED_CAPABILITY_ID,
        duration_s=10.0, step_s=0.2, sample_s=1.0, adcs_only=False,
        values={"environment_profile":"spice_wmm", "earth_gravity_model":choice}, events=())
    with patch.object(NativeOrbitalEnvironment, "configure_gravity", observe_configuration), patch.object(
        SimulationBaseClass.SimBaseClass, "InitializeSimulation", observe_initialization
    ):
        result = UnifiedNativeRuntime(config).run()
    states["telemetry_rows"] = len(result.trace_rows)
    states["runtime_message_read_errors"] = result.summary["runtime_message_read_error_count"]
    return states


if __name__ == "__main__":
    data = {choice: run(choice) for choice in ("legacy_j2_only", "ggm03s_degree4")}
    print(json.dumps(data, indent=2))
    Path(sys.argv[1]).write_text(json.dumps(data, indent=2) + "\n")
