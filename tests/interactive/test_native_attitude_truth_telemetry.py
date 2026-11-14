"""Real Basilisk spacecraft state recorder contract for P9-H attitude truth."""
import math

import pytest

Basilisk = pytest.importorskip("Basilisk")
from Basilisk.simulation import spacecraft
from Basilisk.utilities import RigidBodyKinematics as rbk
from Basilisk.utilities import SimulationBaseClass, macros

from sat_sim.bsk_engine.unified_native import _attitude_truth_from_mrp


def test_real_basilisk_sc_state_recorder_exports_sigma_bn_truth():
    sigma_initial = [0.1, -0.2, 0.05]
    simulation = SimulationBaseClass.SimBaseClass()
    process = simulation.CreateNewProcess("p9h_attitude_process")
    process.addTask(simulation.CreateNewTask("p9h_attitude_task", macros.sec2nano(0.1)))

    vehicle = spacecraft.Spacecraft()
    vehicle.ModelTag = "p9h_truth_spacecraft"
    vehicle.hub.sigma_BNInit = sigma_initial
    recorder = vehicle.scStateOutMsg.recorder(macros.sec2nano(0.1))
    simulation.AddModelToTask("p9h_attitude_task", vehicle)
    simulation.AddModelToTask("p9h_attitude_task", recorder)

    simulation.InitializeSimulation()
    simulation.ConfigureStopTime(macros.sec2nano(0.2))
    simulation.ExecuteSimulation()

    recorded = recorder.sigma_BN
    assert len(recorded) >= 2
    sigma = [float(value) for value in recorded[-1]]
    assert all(math.isfinite(value) for value in sigma)
    assert sigma == pytest.approx(sigma_initial, abs=1e-12)

    fields = _attitude_truth_from_mrp(sigma)
    actual = [fields[f"attitude.truth.quaternion_BN_q{i}"] for i in range(4)]
    official = [float(value) for value in rbk.MRP2EP(sigma)]
    assert actual == pytest.approx(official, abs=1e-12)
    assert math.sqrt(sum(value * value for value in actual)) == pytest.approx(1.0, abs=1e-12)
    assert fields["attitude.truth.valid"] == 1
