"""R8 native whole-spacecraft recovery readiness is deliberately FAIL-CLOSED.

Control-plane fixtures cannot be used as scientific physical evidence. A
separate real Basilisk test validates this matrix against a live worker.
"""
from __future__ import annotations

import copy
import json
import math
import time

import pytest
from fastapi.testclient import TestClient

from sat_sim.interactive.native_checkpoint_audit import (
    MANDATORY_STATE_OWNERS, create_coverage_report,
)
from sat_sim.interactive.native_recovery_readiness import (
    PRECONDITIONS, build_recovery_readiness, verify_recovery_readiness,
)
from test_continuous_console import _client, _body, _ControlProtocolStub


def _audit_payload(t=1.0):
    return create_coverage_report(
        sim_time_s=t,
        observations={
            "spacecraft": {"position_m": [7_000_000., 0., 0.]},
            "adcs_gains": {"K": 2.},
            "battery": {"stored_energy_j": 5000.},
        },
        unavailable={"data_storage": "native sensor missing"},
        native_worker_ident=123,
        scheduler_phase={
            "current_nanos": int(t*1e9),
            "next_task_time_nanos": int(t*1e9)+200_000_000,
            "until_next_task_nanos": 200_000_000,
            "native_clock_restore_proven": False,
            "task_queue_restore_proven": False,
            "event_queue_restore_proven": False,
        },
    )


def test_r8_all_native_owners_remain_unproven_even_with_real_observations():
    result = build_recovery_readiness(_audit_payload())
    verify_recovery_readiness(result)
    assert result["schema_version"] == "sat-native-recovery-readiness.v1"
    assert result["mandatory_owner_count"] == len(MANDATORY_STATE_OWNERS) == 12
    assert result["restorable_owner_count"] == 0
    assert result["automatic_resume_allowed"] is False
    assert result["automatic_tc_replay_allowed"] is False
    assert result["same_session_recovery_allowed"] is False
    assert result["blocking_requirements"] == list(PRECONDITIONS)
    assert [x["owner"] for x in result["owners"]] == list(MANDATORY_STATE_OWNERS)
    assert result["owners"][1]["observation_only"] is True
    assert result["owners"][6]["observation_only"] is False
    assert "data_storage" in result["observations_unavailable"]
    json.dumps(result)


@pytest.mark.parametrize("bad_field,bad_value,why", [
    ("sim_time_s", 8, "DIGEST_MISMATCH"),
    ("native_checkpoint_complete", True, "DIGEST_MISMATCH"),
    ("resume_supported", True, "DIGEST_MISMATCH"),
])
def test_r8_tampered_native_coverage_is_rejected(bad_field, bad_value, why):
    report = _audit_payload()
    report[bad_field] = bad_value
    with pytest.raises(ValueError, match=why):
        build_recovery_readiness(report)


def test_r8_owner_state_status_forgery_rejected_even_with_rehashed_coverage():
    from sat_sim.interactive.native_checkpoint_audit import verify_coverage_report
    import hashlib
    report = _audit_payload()
    report["required_state_owners"][0]["native_state_import"] = "IMPLEMENTED"
    data = {k: v for k, v in report.items() if k != "report_sha256"}
    report["report_sha256"] = hashlib.sha256(json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode()).hexdigest()
    verify_coverage_report(report)
    with pytest.raises(ValueError, match="OWNER_FALSE_PROOF"):
        build_recovery_readiness(report)


def test_r8_changed_readiness_claim_fails_signature_and_rehashed_claim():
    from sat_sim.interactive.native_recovery_readiness import _digest
    result = build_recovery_readiness(_audit_payload())
    forged = copy.deepcopy(result)
    forged["automatic_resume_allowed"] = True
    with pytest.raises(ValueError, match="DIGEST_MISMATCH"):
        verify_recovery_readiness(forged)
    forged["report_sha256"] = _digest({k: v for k, v in forged.items() if k != "report_sha256"})
    with pytest.raises(ValueError, match="UNSUPPORTED_RESTART_CLAIM"):
        verify_recovery_readiness(forged)


def test_r8_bad_native_scheduler_phase_or_source_is_rejected():
    from sat_sim.interactive.native_checkpoint_audit import create_coverage_report
    report = _audit_payload(2.)
    report["native_scheduler_phase"]["current_nanos"] = 1_000_000_000
    report["native_scheduler_phase"]["next_task_time_nanos"] = 1_200_000_000
    from sat_sim.interactive.native_recovery_readiness import _digest
    report["report_sha256"] = _digest({k: v for k, v in report.items() if k != "report_sha256"})
    with pytest.raises(ValueError, match="NATIVE_CLOCK_MISMATCH"):
        build_recovery_readiness(report)
    report = _audit_payload()
    report["capture_source"] = "CSV"
    report["report_sha256"] = _digest({k: v for k, v in report.items() if k != "report_sha256"})
    with pytest.raises(ValueError, match="REQUIRES_LIVE_NATIVE_CAPTURE"):
        build_recovery_readiness(report)


def test_r8_api_requires_advanced_paused_native_simulator(tmp_path, monkeypatch):
    class AuditRuntime(_ControlProtocolStub):
        def audit_native_checkpoint_coverage(self):
            return _audit_payload(self.current_time_s)

    monkeypatch.setattr(
        "sat_sim.interactive.api.build_persistent_runtime",
        lambda spec: (AuditRuntime(), None),
    )
    app = _client(tmp_path, monkeypatch)
    with TestClient(app) as client:
        created = client.post("/interactive/continuous/sessions", json=_body("r8-preflight"))
        assert created.status_code == 201, created.text
        url = "/interactive/continuous/sessions/r8-preflight/recovery/readiness"
        assert client.get(url).status_code == 409  # READY, not advanced/PAUSED
        assert client.post("/interactive/sessions/r8-preflight/control",
                           json={"action": "start"}).status_code == 200
        deadline = time.monotonic()+6.
        while True:
            snap = client.get("/interactive/continuous/sessions/r8-preflight/status").json()["session"]
            if snap["sim_time_s"] >= .2:
                break
            assert time.monotonic() < deadline
            time.sleep(.02)
        assert client.post("/interactive/sessions/r8-preflight/control",
                           json={"action": "pause"}).status_code == 200
        before = client.get("/interactive/continuous/sessions/r8-preflight/status").json()["session"]
        result = client.get(url)
        assert result.status_code == 200, result.text
        data = result.json()
        assert data["restart_readiness"] == "BLOCKED"
        assert data["automatic_resume_allowed"] is False
        verify_recovery_readiness(data)
        after = client.get("/interactive/continuous/sessions/r8-preflight/status").json()["session"]
        assert after["state"] == before["state"] == "PAUSED"
        assert after["sim_time_s"] == before["sim_time_s"]
        assert client.post("/interactive/sessions/r8-preflight/control",
                           json={"action": "stop"}).status_code == 200


def test_r8_no_live_session_is_not_recovery_authorization(tmp_path, monkeypatch):
    app = _client(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert client.get("/interactive/continuous/sessions/missing/recovery/readiness").status_code == 404
