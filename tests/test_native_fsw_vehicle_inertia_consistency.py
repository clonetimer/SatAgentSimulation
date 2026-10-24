"""Physical L4/L5: Basilisk MRP FSW mass properties must match its actual hub.

A registered spacecraft inertia is not complete when the official dynamics
uses it but mrpFeedback still reads the former fixed 100/80/60 matrix.
Tests run the genuine unified closed-loop Basilisk graph without any proxy.
"""
from __future__ import annotations

import numpy as np
import pytest


def _matrix(raw):
    return np.asarray(raw, dtype=float).reshape(3, 3)


def test_real_fsw_vehicle_config_tracks_hub_inertia_and_center_of_mass(monkeypatch):
    pytest.importorskip("Basilisk")
    from Basilisk.simulation import spacecraft
    from Basilisk.fswAlgorithms import mrpFeedback
    from Basilisk.architecture import messaging
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    real_sc, real_fsw = spacecraft.Spacecraft, mrpFeedback.mrpFeedback
    real_vehicle_msg = messaging.VehicleConfigMsg
    sc_objects, fsw_objects, vehicle_messages = [], [], []

    def make_sc():
        obj = real_sc()
        sc_objects.append(obj)
        return obj

    def make_controller():
        obj = real_fsw()
        fsw_objects.append(obj)
        return obj

    def make_vehicle_message():
        msg = real_vehicle_msg()
        vehicle_messages.append(msg)
        return msg

    monkeypatch.setattr(spacecraft, "Spacecraft", make_sc)
    monkeypatch.setattr(mrpFeedback, "mrpFeedback", make_controller)
    monkeypatch.setattr(messaging, "VehicleConfigMsg", make_vehicle_message)
    adapter = get_adapter_for_capability(WHOLE_ID)

    def run(values):
        result = adapter.run(_short_spec(WHOLE_ID, duration_s=18., values={
            "initial_pointing_error_deg": 25., **values,
        }))
        assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
        assert "spacecraft" in result.metadata["model_source_boundary"]["basilisk_official_native"]
        assert "mrp_feedback" in result.metadata["model_source_boundary"]["basilisk_official_native"]
        assert len(sc_objects) == len(fsw_objects) == len(vehicle_messages)
        hub = sc_objects[-1].hub
        # mrpFeedback is a C module in Basilisk 2.11: its vehConfigInMsg is
        # a VehicleConfigMsg_C, NOT a callable Python reader. Capture the
        # actual Python-origin message written to and subscribed by FSW.
        vehicle = vehicle_messages[-1].read()
        actual_hub_inertia = _matrix(hub.IHubPntBc_B)
        actual_fsw_inertia = _matrix(vehicle.ISCPntB_B)
        actual_hub_com = np.asarray(hub.r_BcB_B, dtype=float).reshape(3)
        actual_fsw_com = np.asarray(vehicle.CoM_B, dtype=float).reshape(3)
        assert actual_fsw_inertia == pytest.approx(actual_hub_inertia, abs=1e-10)
        assert actual_fsw_com == pytest.approx(actual_hub_com, abs=1e-12)
        assert float(vehicle.massSC) == pytest.approx(float(hub.mHub))
        # C mrpFeedback retains its reset-time inertia in its public C array;
        # compare that to the exact payload and to the real official hub.
        loaded_fsw_inertia = _matrix(fsw_objects[-1].ISCPntB_B)
        assert loaded_fsw_inertia == pytest.approx(actual_fsw_inertia, abs=1e-10)
        assert len(result.trace_rows) >= 5
        return result.trace_rows, actual_fsw_inertia, actual_fsw_com

    baseline, old_i, old_com = run({})
    assert old_i == pytest.approx(np.diag([100., 80., 60.]), abs=1e-10)
    assert old_com == pytest.approx([0., 0., 0.], abs=1e-12)

    changed, new_i, new_com = run({
        "spacecraft_mass_kg": 140.,
        "spacecraft_inertia_xx_kg_m2": 210.,
        "spacecraft_inertia_yy_kg_m2": 155.,
        "spacecraft_inertia_zz_kg_m2": 120.,
    })
    assert new_i == pytest.approx(np.diag([210., 155., 120.]), abs=1e-10)
    assert new_com == pytest.approx([0., 0., 0.], abs=1e-12)
    assert [row["time_s"] for row in baseline] == [row["time_s"] for row in changed]
    # A changed configuration must also be reflected in actual closed-loop
    # FSW wheel torque output, not just the controller's vehicle input.
    torque_channels = [f"adcs.rw.command_torque_nm_{axis}" for axis in range(3)]
    deltas = [
        max(abs(float(a[key]) - float(b[key])) for key in torque_channels)
        for a, b in zip(baseline[1:], changed[1:])
    ]
    assert max(deltas) > 1e-7, deltas

    relocated, relocated_i, relocated_com = run({
        "rw_geometry_mode": "per_wheel",
        "rw_mount_b_x_m_0": .25,
        "rw_mount_b_y_m_0": -.2,
        "rw_mount_b_z_m_0": .1,
    })
    assert len(relocated) == len(baseline)
    assert np.max(np.abs(relocated_i - old_i)) > 1e-3
    assert abs(relocated_i[0, 1]) > 1e-6
    assert np.max(np.abs(relocated_com)) > 1e-6
    # The FSW must receive the *relocated* center-of-mass inertia, not a
    # flattened inertia about body origin that adds mass twice.
    assert np.min(np.linalg.eigvalsh(relocated_i)) > 0.


def test_fsw_inertia_input_is_not_a_hardcoded_nominal_literal():
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "src/sat_sim/bsk_engine/unified_native.py"
    code = path.read_text(encoding="utf-8")
    assert "vehicle.ISCPntB_B = hub_inertia_com_b.reshape(-1).tolist()" in code
    assert "vehicle.CoM_B = hub_com_b.tolist()" in code
    assert "vehicle.ISCPntB_B = [100.0, 0.0" not in code
