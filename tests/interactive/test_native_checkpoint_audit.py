"""Native checkpoint coverage auditing: real outputs but no false restore claim."""
from __future__ import annotations

import json

import pytest

from sat_sim.interactive.native_checkpoint_audit import (
    MANDATORY_STATE_OWNERS, capture_observations, create_coverage_report,
    verify_coverage_report,
)


class _Message:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return self.payload


class _Object:
    def __init__(self, **values):
        self.__dict__.update(values)


def test_native_coverage_report_is_hash_attested_and_fail_closed() -> None:
    obj = _Object(
        scStateOutMsg=_Message(_Object(
            r_BN_N=[7000000.0, 0.0, 0.0],
            v_BN_N=[0.0, 7500.0, 0.0],
            sigma_BN=[0.1, 0.0, 0.0],
            omega_BN_B=[0.01, 0.02, 0.03],
        ))
    )
    obs, unavailable = capture_observations(
        spacecraft=obj,
        reference=_Object(sigma_R0N=[0.1, 0.0, 0.0]),
        controller=_Object(K=3.5, P=24.0, Ki=-1),
        battery=_Object(
            batPowerOutMsg=_Message(_Object(storageLevel=600000)),
            storageCapacity=800000,
        ),
        storage=_Object(
            storageUnitDataOutMsg=_Message(_Object(storageLevel=42)),
            storageCapacity=100,
        ),
    )
    assert not unavailable
    assert obs["spacecraft"]["position_m"][0] == 7000000.0
    assert obs["battery"]["stored_energy_j"] == 600000
    report = create_coverage_report(
        sim_time_s=5.0, observations=obs,
        unavailable=unavailable, native_worker_ident=1234,
    )
    verify_coverage_report(report)
    assert report["resume_supported"] is False
    assert report["native_checkpoint_complete"] is False
    assert report["fully_restorable_owner_count"] == 0
    assert [e["owner"] for e in report["required_state_owners"]] == list(MANDATORY_STATE_OWNERS)
    json.dumps(report)
    modified = dict(report)
    modified["resume_supported"] = True
    with pytest.raises(ValueError, match="NATIVE_CHECKPOINT_AUDIT_DIGEST_MISMATCH"):
        verify_coverage_report(modified)


def test_unavailable_native_message_cannot_be_fabricated() -> None:
    obs, unavailable = capture_observations(
        spacecraft=_Object(), reference=_Object(sigma_R0N=[0, 0, 0]),
        controller=_Object(K=1.0, P=1.0, Ki=0.0),
    )
    assert "spacecraft" not in obs
    assert "spacecraft" in unavailable
    report = create_coverage_report(
        sim_time_s=1.0, observations=obs, unavailable=unavailable,
        native_worker_ident=123,
    )
    verify_coverage_report(report)
    assert not report["resume_supported"]
    with pytest.raises(ValueError, match="NATIVE_CHECKPOINT_REQUIRES_ADVANCED_SIMULATION"):
        create_coverage_report(
            sim_time_s=0.0, observations=obs, unavailable=unavailable,
            native_worker_ident=123,
        )


def test_live_whole_spacecraft_native_audit_tracks_real_dynamic_state() -> None:
    pytest.importorskip("Basilisk")
    from sat_sim.bsk_engine.unified_native import UnifiedRuntimeConfig
    from sat_sim.interactive.unified_runtime import UnifiedPersistentRuntime
    from sat_sim.interactive.models import ActorRole, Telecommand

    config = UnifiedRuntimeConfig(
        capability_id="whole_spacecraft.unified_native.v1",
        duration_s=0.5, step_s=0.2, sample_s=0.2,
        adcs_only=False, values={}, events=(),
        interactive_unbounded=True,
    )
    runtime = UnifiedPersistentRuntime(config, continuous=True)
    runtime.prepare()
    try:
        with pytest.raises(RuntimeError, match="requires a prepared and advanced simulation"):
            runtime.audit_native_checkpoint_coverage()
        runtime.advance_to(0.8)
        before = runtime.audit_native_checkpoint_coverage()
        assert before["sim_time_s"] == pytest.approx(0.8)
        assert before["native_worker_ident"] == runtime.worker_ident
        assert before["capture_source"] == "LIVE_BASILISK_WORKER_THREAD"
        assert runtime.current_time_s == pytest.approx(0.8)
        assert not before["resume_supported"]
        assert not before["native_checkpoint_complete"]
        assert len(before["required_state_owners"]) >= 10
        assert "spacecraft" in before["observations"], before["unavailable_observations"]
        assert len(before["observations"]["spacecraft"]["position_m"]) == 3

        runtime.apply_command(Telecommand(
            command_id="native-state-audit-target",
            session_id="native-audit",
            session_revision=0, operation="adcs.target.set",
            target="subsystem.adcs",
            parameters={"sigma_rn": [0.4, 0.0, 0.0]},
            actor_id="test-operator",
            actor_role=ActorRole.OPERATOR,
            execute_at_sim_time_s=0.8,
        ))
        runtime.advance_to(1.6)
        after = runtime.audit_native_checkpoint_coverage()
        verify_coverage_report(after)
        assert runtime.current_time_s == pytest.approx(1.6)
        assert after["sim_time_s"] == pytest.approx(1.6)
        assert after["native_worker_ident"] == before["native_worker_ident"]
        assert after["observations"]["spacecraft"]["position_m"] != before["observations"]["spacecraft"]["position_m"]
        assert after["observations"]["adcs_reference"]["sigma_R0N"][0] == pytest.approx(0.4)
        assert not after["resume_supported"]
        summary = runtime.finalize()
        assert summary["persistent_instance"]
        assert summary["summary"]["execution_segment_count"] == 2
    finally:
        if runtime._thread is not None and runtime._thread.is_alive():
            runtime.abort()
