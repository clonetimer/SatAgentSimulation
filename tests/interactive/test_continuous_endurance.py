"""R7f durable, independently controlled continuous endurance evidence tests.

The protocol stub here validates only session and filesystem mechanics.
Genuine spacecraft physics stays in existing Basilisk native test jobs.
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from sat_sim.interactive.continuous_endurance import (
    EVIDENCE_FILE, SUMMARY_FILE, verify_endurance_evidence,
    verify_endurance_summary,
)
from test_continuous_console import _client, _body, _ControlProtocolStub


def _test_client(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sat_sim.interactive.api.build_persistent_runtime",
        lambda spec: (_ControlProtocolStub(), None),
    )
    return _client(tmp_path, monkeypatch)


def test_endurance_samples_are_fsynced_hashed_and_never_replayed(tmp_path, monkeypatch):
    app = _test_client(tmp_path, monkeypatch)
    with TestClient(app) as client:
        created = client.post(
            "/interactive/continuous/sessions", json=_body("endurance-test"),
        )
        assert created.status_code == 201, created.text
        base = "/interactive/continuous/sessions/endurance-test/endurance"
        missing = client.get(base + "/status")
        assert missing.status_code == 200
        assert missing.json()["recording_status"] == "NOT_STARTED"
        assert client.get(base + "/evidence").status_code == 404
        assert client.get(base + "/summary").status_code == 404

        start = client.post(base + "/start", json={"sample_interval_s": 1.})
        assert start.status_code == 201, start.text
        assert start.json()["recording_status"] == "RECORDING"
        assert client.post(base + "/start", json={"sample_interval_s": 1.}).status_code == 409
        deadline = time.monotonic() + 5.
        while True:
            row = client.get(base + "/status").json()
            if row["sample_count"] >= 2:
                break
            assert time.monotonic() < deadline
            time.sleep(.05)
        assert row["native_resume_supported"] is False
        assert row["automatic_tc_replay_allowed"] is False
        assert row["sample_count"] >= 2
        assert row["last_observation"]["telemetry"]["status"] == "PAUSED_OR_READY"
        stopped = client.post(base + "/stop")
        assert stopped.status_code == 200, stopped.text
        report = stopped.json()
        assert report["recording_status"] == "FINISHED"
        assert report["summary"]["qualified_24h_wallclock"] is False
        assert report["summary"]["eligible_for_manual_24h_review"] is False
        assert report["summary"]["observed_wall_span_s"] >= 1.
        assert report["summary"]["sim_progress_s"] == 0.
        assert client.get(base + "/summary").status_code == 200
        assert client.get(base + "/evidence").status_code == 200
        root = tmp_path / "runs" / "interactive_sessions" / "endurance-test"
        if not root.is_dir():
            # Resolve workspace from the actual application manager.
            root = next((tmp_path / "runs").rglob(EVIDENCE_FILE)).parent
        verified = verify_endurance_summary(root / SUMMARY_FILE)
        assert verified["evidence"]["samples"] == report["sample_count"]
        assert verified["evidence"]["final_entry_hash"] == report["summary"]["final_evidence_hash"]
        assert client.post(base + "/start", json={"sample_interval_s": 1}).status_code == 409
        # A record collection must never stop / apply commands to the simulator.
        status = client.get("/interactive/continuous/sessions/endurance-test/status").json()
        assert status["session"]["state"] == "READY"


def test_hash_tamper_is_detected_and_crash_incomplete_never_qualified(tmp_path, monkeypatch):
    app = _test_client(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert client.post(
            "/interactive/continuous/sessions", json=_body("tamper-proof"),
        ).status_code == 201
        base = "/interactive/continuous/sessions/tamper-proof/endurance"
        assert client.post(base + "/start", json={"sample_interval_s": 1}).status_code == 201
        deadline = time.monotonic() + 4.
        while client.get(base + "/status").json()["sample_count"] < 1:
            assert time.monotonic() < deadline
            time.sleep(.02)
        assert client.post(base + "/stop").status_code == 200
        evidence = next((tmp_path / "runs").rglob(EVIDENCE_FILE))
        rows = evidence.read_text(encoding="utf-8").splitlines()
        original = rows[0]
        payload = json.loads(original)
        payload["observation"]["sim_elapsed_s"] = 123.
        rows[0] = json.dumps(payload)
        evidence.write_text("\n".join(rows) + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match="ENDURANCE_EVIDENCE_DIGEST_INVALID"):
            verify_endurance_evidence(evidence)
        with pytest.raises(ValueError, match="ENDURANCE_EVIDENCE_DIGEST_INVALID"):
            verify_endurance_summary(evidence.parent / SUMMARY_FILE)
        # An incomplete/truncated JSONL is not silently accepted.
        evidence.write_bytes((original + "\n" + '{"partial":').encode())
        with pytest.raises((ValueError, json.JSONDecodeError)):
            verify_endurance_evidence(evidence)


def test_endurance_role_and_allowed_intervals_and_no_restart(tmp_path, monkeypatch):
    app = _test_client(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert client.post(
            "/interactive/continuous/sessions", json=_body("bounds"),
        ).status_code == 201
        base = "/interactive/continuous/sessions/bounds/endurance"
        assert client.post(base + "/start", json={"sample_interval_s": 0.1}).status_code == 422
        assert client.post(base + "/start", json={"sample_interval_s": 19}).status_code == 422
        assert client.post(base + "/start", json={"sample_interval_s": 5, "arbitrary_code": "x"}).status_code == 422
        assert client.post(base + "/stop").status_code == 404
        assert client.get(
            "/interactive/continuous/sessions/missing/endurance/status"
        ).status_code == 404
