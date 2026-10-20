"""Basilisk native state-manager checkpoint pilot; no claim of restart recovery."""
from __future__ import annotations

import copy
import pytest

from sat_sim.interactive.native_state_pilot import (
    capture_state_subset, restore_state_subset_in_place, validate_state_subset,
)


def _make_native_graph():
    pytest.importorskip("Basilisk")
    from Basilisk.simulation import spacecraft, reactionWheelStateEffector
    from Basilisk.utilities import SimulationBaseClass, simIncludeRW, macros

    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("p")
    process.addTask(sim.CreateNewTask("t", macros.sec2nano(0.2)))
    sc = spacecraft.Spacecraft()
    sc.hub.mHub = 100.0
    sc.hub.IHubPntBc_B = [[100., 0., 0.], [0., 80., 0.], [0., 0., 60.]]
    sc.hub.r_CN_NInit = [7_000_000.0, 0.0, 0.0]
    sc.hub.v_CN_NInit = [0.0, 7_500.0, 0.0]
    sc.hub.sigma_BNInit = [0.02, 0.0, 0.0]
    sc.hub.omega_BN_BInit = [0.01, 0.02, 0.01]
    sim.AddModelToTask("t", sc, None, 100)
    wheels = reactionWheelStateEffector.ReactionWheelStateEffector()
    wheel_factory = simIncludeRW.rwFactory()
    for i, axis in enumerate(([1, 0, 0], [0, 1, 0], [0, 0, 1])):
        wheel_factory.create(
            "Honeywell_HR16", list(axis), maxMomentum=50.0,
            Omega=100.0 * (i + 1), u_max=0.2, label=f"RW{i + 1}",
        )
    wheel_factory.addToSpacecraft("reaction_wheels", wheels, sc)
    sim.AddModelToTask("t", wheels, None, 99)
    sim.InitializeSimulation()
    return sim, sc


def test_basilisk_in_place_state_subset_roundtrip_matches_uninterrupted_graph() -> None:
    from Basilisk.utilities import macros
    import numpy as np

    baseline, original_sc = _make_native_graph()
    resumed_in_same_graph, candidate_sc = _make_native_graph()
    for sim in (baseline, resumed_in_same_graph):
        sim.ConfigureStopTime(macros.sec2nano(0.8))
        sim.ExecuteSimulation()
    snapshot = capture_state_subset(candidate_sc, expected_rw_count=3)
    validate_state_subset(snapshot, expected_rw_count=3)
    assert not snapshot["resume_supported"]
    assert not snapshot["scheduler_time_restored"]

    native_state = candidate_sc.dynManager.getStateObject("hubPosition")
    original_position = native_state.getState()
    native_state.setState(np.array([
        [original_position[0][0] + 1000.],
        [original_position[1][0]],
        [original_position[2][0]],
    ]))
    assert capture_state_subset(candidate_sc)["vectors"]["hub_position_m"] != snapshot["vectors"]["hub_position_m"]
    restore_state_subset_in_place(candidate_sc, snapshot, expected_rw_count=3)
    assert capture_state_subset(candidate_sc)["vectors"] == snapshot["vectors"]
    for sim in (baseline, resumed_in_same_graph):
        sim.ConfigureStopTime(macros.sec2nano(1.6))
        sim.ExecuteSimulation()
    before = capture_state_subset(original_sc, expected_rw_count=3)
    after = capture_state_subset(candidate_sc, expected_rw_count=3)
    for key in before["vectors"]:
        assert after["vectors"][key] == pytest.approx(
            before["vectors"][key], rel=1e-13, abs=1e-10
        )


def test_basilisk_pilot_rejects_wrong_wheel_count_and_tampered_resume_claim() -> None:
    from Basilisk.utilities import macros

    sim, sc = _make_native_graph()
    sim.ConfigureStopTime(macros.sec2nano(0.8))
    sim.ExecuteSimulation()
    snapshot = capture_state_subset(sc, expected_rw_count=3)
    with pytest.raises(ValueError, match="WHEEL_COUNT_MISMATCH"):
        restore_state_subset_in_place(sc, snapshot, expected_rw_count=4)
    forged = {**snapshot, "resume_supported": True}
    with pytest.raises(ValueError, match="HASH_INVALID"):
        validate_state_subset(forged)
    malformed = copy.deepcopy(snapshot)
    malformed["vectors"]["hub_velocity_m_s"] = [1.0, 2.0]
    with pytest.raises(ValueError, match="HASH_INVALID"):
        validate_state_subset(malformed)
    assert capture_state_subset(sc, expected_rw_count=3)["vectors"] == snapshot["vectors"]
