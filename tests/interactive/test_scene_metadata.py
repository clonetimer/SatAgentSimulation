from __future__ import annotations

from types import SimpleNamespace

from sat_sim.bsk_engine.orbital_environment import DEFAULT_EPOCH
from sat_sim.interactive.scene_metadata import realtime_scene_metadata


def test_realtime_scene_metadata_uses_the_same_explicit_epoch_as_native_orbit_runtime():
    spec = SimpleNamespace(
        capability_id="whole_spacecraft.unified_native.v1",
        task_spec={
            "simulation": {"time_system": "UTC"},
            "parameters": {"values": {"epoch_utc": "2026-02-03T04:05:06Z"}},
        },
    )
    metadata = realtime_scene_metadata(spec)
    assert metadata == {
        "epoch_utc": "2026-02-03T04:05:06Z",
        "time_system": "UTC",
        "orbit_position_frame": "J2000_INERTIAL",
    }


def test_realtime_scene_metadata_uses_native_runtime_default_epoch_without_guessing():
    spec = SimpleNamespace(
        capability_id="whole_spacecraft.unified_native.v1",
        task_spec={"simulation": {}, "parameters": {"values": {}}},
    )
    metadata = realtime_scene_metadata(spec)
    assert metadata["epoch_utc"] == DEFAULT_EPOCH
    assert metadata["orbit_position_frame"] == "J2000_INERTIAL"


def test_realtime_scene_metadata_does_not_claim_orbit_frame_for_unsupported_capabilities():
    spec = SimpleNamespace(
        capability_id="subsystem.eps_unified_native.v1",
        task_spec={"simulation": {"time_system": "TAI"}, "parameters": {"values": {}}},
    )
    metadata = realtime_scene_metadata(spec)
    assert metadata["time_system"] == "TAI"
    assert metadata["orbit_position_frame"] is None
