"""Independent Basilisk-native sensor graph probes for P9-N2.

These are bounded engineering probes, not production TaskSpec adapters yet.
Each sensor receives an explicit truth/environment message and records the
official Basilisk sensor output. No whole-spacecraft run is sliced or reused.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class NativeStarTrackerTrace:
    times_s: tuple[float, ...]
    q_inrtl_to_case: tuple[tuple[float, float, float, float], ...]
    omega_inrtl_to_case_rad_s: tuple[tuple[float, float, float], ...]
    truth_sigma_bn: tuple[float, float, float]
    truth_omega_bn_b_rad_s: tuple[float, float, float]
    model_source: str = "Basilisk.simulation.starTracker.StarTracker"
    input_source: str = "explicit_SCStatesMsg_truth_fixture"
    claim_scope: str = "official_star_tracker_measurement_from_prescribed_spacecraft_truth_without_optical_star_catalog"


def _finite_vector(values: Sequence[float], size: int, name: str) -> tuple[float, ...]:
    if not isinstance(values, (list, tuple)) or len(values) != size:
        raise ValueError(f"{name}_INVALID")
    result = tuple(float(value) for value in values)
    if not all(math.isfinite(value) for value in result):
        raise ValueError(f"{name}_NONFINITE")
    return result


def run_native_star_tracker_probe(
    *,
    duration_s: float = 2.0,
    sample_s: float = 0.1,
    truth_sigma_bn: Sequence[float] = (0.05, -0.02, 0.01),
    truth_omega_bn_b_rad_s: Sequence[float] = (0.01, -0.005, 0.002),
    noise_std_rad: float = 0.0,
    random_walk_std_rad_s: float = 0.0,
    seed: int = 0,
    dcm_cb: Sequence[float] = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
) -> NativeStarTrackerTrace:
    """Run the upstream StarTracker against an explicit SCStates truth message.

    This graph deliberately does not claim optical scene, star-catalog,
    centroiding, blinding, or FOV simulation. It isolates the official
    Basilisk attitude measurement model and its recorder output.
    """
    if type(duration_s) not in (int, float) or not math.isfinite(float(duration_s)) or not 0.1 <= float(duration_s) <= 120.0:
        raise ValueError("NATIVE_STAR_TRACKER_DURATION_INVALID")
    if type(sample_s) not in (int, float) or not math.isfinite(float(sample_s)) or not 0.001 <= float(sample_s) <= 1.0:
        raise ValueError("NATIVE_STAR_TRACKER_SAMPLE_INVALID")
    duration = float(duration_s)
    sample = float(sample_s)
    if sample > duration or duration / sample > 20000:
        raise ValueError("NATIVE_STAR_TRACKER_GRID_INVALID")
    sigma = _finite_vector(truth_sigma_bn, 3, "NATIVE_STAR_TRACKER_TRUTH_SIGMA")
    omega = _finite_vector(truth_omega_bn_rad_s, 3, "NATIVE_STAR_TRACKER_TRUTH_OMEGA")
    dcm = _finite_vector(dcm_cb, 9, "NATIVE_STAR_TRACKER_DCM")
    if type(seed) is not int or not 0 <= seed <= 2147483647:
        raise ValueError("NATIVE_STAR_TRACKER_SEED_INVALID")
    if type(noise_std_rad) not in (int, float) or not math.isfinite(float(noise_std_rad)) or not 0.0 <= float(noise_std_rad) <= 0.1:
        raise ValueError("NATIVE_STAR_TRACKER_NOISE_INVALID")
    if type(random_walk_std_rad_s) not in (int, float) or not math.isfinite(float(random_walk_std_rad_s)) or not 0.0 <= float(random_walk_std_rad_s) <= 0.1:
        raise ValueError("NATIVE_STAR_TRACKER_WALK_INVALID")

    # Import only after validating inputs. Missing Basilisk must fail closed.
    from Basilisk.architecture import messaging
    from Basilisk.utilities import SimulationBaseClass, macros
    from components.star_tracker.builder import build_star_tracker

    truth = messaging.SCStatesMsgPayload()
    truth.sigma_BN = list(sigma)
    truth.omega_BN_B = list(omega)
    truth.r_BN_N = [7_000_000.0, 0.0, 0.0]
    truth.v_BN_N = [0.0, 7_500.0, 0.0]
    truth.omegaDot_BN_B = [0.0, 0.0, 0.0]
    truth.nonConservativeAccelpntB_B = [0.0, 0.0, 0.0]
    truth_msg = messaging.SCStatesMsg().write(truth)

    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("P9N2StarTrackerProcess")
    process.addTask(sim.CreateNewTask("P9N2StarTrackerTask", macros.sec2nano(sample)))
    sensor = build_star_tracker(
        model_tag="P9N2OfficialStarTracker",
        sc_state_msg=truth_msg,
        sigma_sigma_c=float(noise_std_rad),
        sigma_omega_c_rad_s=float(random_walk_std_rad_s),
        dcm_cb=dcm,
    )
    if noise_std_rad > 0.0 or random_walk_std_rad_s > 0.0:
        sensor.RNGSeed = seed
    sim.AddModelToTask("P9N2StarTrackerTask", sensor, None, 10)
    recorder = sensor.sensorOutMsg.recorder(macros.sec2nano(sample))
    sim.AddModelToTask("P9N2StarTrackerTask", recorder, None, 5)
    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(duration))
    sim.ExecuteSimulation()

    times = tuple(float(value) * 1.0e-9 for value in recorder.times())
    quaternions = tuple(tuple(float(value) for value in row) for row in recorder.qInrtl2Case)
    angular_rates = tuple(tuple(float(value) for value in row) for row in recorder.omegaInrtl2Case)
    if not times or len(times) != len(quaternions) or len(times) != len(angular_rates):
        raise RuntimeError("NATIVE_STAR_TRACKER_RECORDER_MISSING")
    if any(len(row) != 4 for row in quaternions) or any(len(row) != 3 for row in angular_rates):
        raise RuntimeError("NATIVE_STAR_TRACKER_RECORDER_SHAPE_INVALID")
    flat = [value for row in (*quaternions, *angular_rates) for value in row]
    if not all(math.isfinite(value) for value in flat):
        raise RuntimeError("NATIVE_STAR_TRACKER_RECORDER_NONFINITE")
    for quaternion in quaternions:
        norm = math.sqrt(sum(value * value for value in quaternion))
        if not 0.99 <= norm <= 1.01:
            raise RuntimeError("NATIVE_STAR_TRACKER_QUATERNION_NOT_NORMALIZED")

    return NativeStarTrackerTrace(
        times_s=times,
        q_inrtl_to_case=quaternions,
        omega_inrtl_to_case_rad_s=angular_rates,
        truth_sigma_bn=sigma,
        truth_omega_bn_b_rad_s=omega,
    )
