"""R7g independent forensic auditor gates existing continuous endurance evidence.

This uses only a deterministic control-protocol fixture for *control-plane
testing*; real native Basilisk 8s and 30s runs each invoke the auditor in an
actual separate Python process in the native workflow.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from fastapi.testclient import TestClient

from sat_sim.interactive.continuous_endurance import EVIDENCE_FILE, SUMMARY_FILE
from sat_sim.interactive.continuous_endurance_audit import audit_endurance_workspace
from test_continuous_console import _body, _client, _ControlProtocolStub


def _app(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sat_sim.interactive.api.build_persistent_runtime",
        lambda spec: (_ControlProtocolStub(), None),
    )
    return _client(tmp_path, monkeypatch)


def _cli(workspace, *args):
    return subprocess.run(
        [sys.executable, "-m", "sat_sim.interactive.continuous_endurance_audit",
         str(workspace), *args],
        text=True, capture_output=True, timeout=20.,
    )


def test_external_audit_live_incomplete_then_complete_but_short(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        name = "audit-real-workspace"
        assert client.post("/interactive/continuous/sessions", json=_body(name)).status_code == 201
        base = "/interactive/continuous/sessions/" + name + "/endurance"
        missing = client.get(base + "/audit")
        assert missing.status_code == 200
        assert missing.json()["verdict"] == "INCOMPLETE"
        assert missing.json()["reason_codes"] == ["EVIDENCE_MISSING"]
        assert client.post(base + "/start", json={"sample_interval_s": 1.}).status_code == 201
        workspace = next(tmp_path.rglob(EVIDENCE_FILE)).parent
        deadline = time.monotonic() + 5.
        while client.get(base + "/status").json()["sample_count"] < 2:
            assert time.monotonic() < deadline
            time.sleep(.02)
        live = client.get(base + "/audit")
        assert live.status_code == 200
        assert live.json()["verdict"] == "INCOMPLETE"
        assert live.json()["qualified_24h_wallclock"] is False
        sub = _cli(workspace, "--allow-incomplete")
        assert sub.returncode == 0, sub.stderr
        assert json.loads(sub.stdout)["verdict"] == "INCOMPLETE"
        assert _cli(workspace).returncode == 3
        stopped = client.post(base + "/stop")
        assert stopped.status_code == 200, stopped.text
        final = client.get(base + "/audit")
        assert final.status_code == 200, final.text
        row = final.json()
        assert row["verdict"] == "NOT_QUALIFIED", row
        assert row["observed_running_wall_s"] == 0.
        assert row["observed_wall_span_s"] >= 1.
        assert row["simulation_progress_s"] == 0.
        assert row["samples"] >= 2
        assert row["manual_review_required"] is True
        assert row["automatic_tc_replay_allowed"] is False
        assert _cli(workspace).returncode == 0
        assert client.get("/interactive/continuous/sessions/" + name + "/status").json()["session"]["state"] == "READY"
        assert (workspace / SUMMARY_FILE).is_file()


def test_external_audit_rejects_corrupt_and_missing_final_summary(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        name = "audit-corruption"
        assert client.post("/interactive/continuous/sessions", json=_body(name)).status_code == 201
        base = "/interactive/continuous/sessions/" + name + "/endurance"
        assert client.post(base + "/start", json={"sample_interval_s": 1.}).status_code == 201
        workspace = next(tmp_path.rglob(EVIDENCE_FILE)).parent
        deadline = time.monotonic() + 4.
        while client.get(base + "/status").json()["sample_count"] < 1:
            assert time.monotonic() < deadline
            time.sleep(.02)
        assert client.post(base + "/stop").status_code == 200
        evidence = workspace / EVIDENCE_FILE
        original = evidence.read_bytes()
        report = audit_endurance_workspace(workspace)
        assert report["verdict"] == "NOT_QUALIFIED"
        # No complete summary after a parent crash is never falsely accepted.
        (workspace / SUMMARY_FILE).unlink()
        assert audit_endurance_workspace(workspace)["verdict"] == "INCOMPLETE"
        evidence.write_bytes(original[:-4] + b"oops")
        corrupt = audit_endurance_workspace(workspace)
        assert corrupt["verdict"] == "BLOCKED"
        assert "EVIDENCE_INTEGRITY_INVALID" in corrupt["reason_codes"]
        sub = _cli(workspace)
        assert sub.returncode == 2, sub.stdout


def test_external_audit_rejects_invalid_threshold_configuration(tmp_path):
    from pytest import raises
    with raises(ValueError, match="BAD_THRESHOLDS"):
        audit_endurance_workspace(tmp_path, max_stale_s=0)
    with raises(ValueError, match="BAD_THRESHOLDS"):
        audit_endurance_workspace(tmp_path, rss_growth_warning_mib_h=-1)


def test_split_wallclock_runs_over_24h_are_not_continuous(tmp_path, monkeypatch):
    """Both R7f summary and external auditor reject aggregated 24h across pause.

    All rows are deliberately generated *control-plane evidence fixtures*.
    Genuine satellite physical consistency is tested only in native Basilisk.
    """
    from types import SimpleNamespace
    from datetime import datetime, timezone, timedelta
    from sat_sim.interactive.models import SessionState
    from sat_sim.interactive.continuous_endurance import (
        EnduranceRecorder, verify_endurance_summary,
    )
    import sat_sim.interactive.continuous_endurance as endurance

    session = SimpleNamespace(
        spec=SimpleNamespace(continuous=True, session_id="split-24h", quantum_s=.2),
        _workspace=SimpleNamespace(path=tmp_path),
        state=SessionState.READY,
    )
    times_states = (
        [(t, "RUNNING") for t in range(0, 48600, 1800)]
        + [(48600, "PAUSED"), (50400, "RUNNING")]
        + [(t, "RUNNING") for t in range(52200, 100800, 1800)]
        + [(100800, "COMPLETED")]
    )
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    items = []
    for i, (wall, state) in enumerate(times_states):
        items.append({
            "schema_version": "sat-continuous-observability.v1",
            "sampled_at": (start + timedelta(seconds=wall)).isoformat(),
            "session_id": "split-24h", "session_state": state,
            "wall_elapsed_s": float(wall), "sim_elapsed_s": float(i),
            "telemetry": {
                "stream_id": "whole.continuous", "status": "FRESH",
                "lag_wall_s": 0., "last_sequence": i,
                "last_sim_time_s": float(i), "last_wall_time": start.isoformat(),
                "durable_frame_count": i, "durable_bytes": i * 40,
            },
            "process": {
                "available": True, "pid": 1234,
                "scope": "API_AND_BASILISK_PARENT",
                "resident_bytes": 1024 * 1024 * 100,
                "cpu_time_s": float(i), "source": "TEST_FIXTURE",
            },
            "disk": {
                "available": True, "free_bytes": 1024**3,
                "total_bytes": 2*1024**3, "reserved_bytes": 0,
            },
            "resume_supported": False, "automatic_tc_replay_allowed": False,
        })
    stream = iter(items)
    monkeypatch.setattr(endurance, "observe_continuous_session", lambda s: next(stream))
    recorder = EnduranceRecorder(session, interval_s=3600.)
    for _ in items:
        recorder._sample()
    recorder._finalize()
    summary = verify_endurance_summary(tmp_path / SUMMARY_FILE)["summary"]
    assert summary["observed_running_wall_s"] >= 86400.
    assert summary["observed_pause_after_run"] is True
    assert summary["eligible_for_manual_24h_review"] is False
    report = audit_endurance_workspace(tmp_path, max_stale_s=10800.)
    assert report["verdict"] == "NOT_QUALIFIED", report
    assert report["observed_running_wall_s"] >= 86400.
    assert report["observed_pause_after_run"] is True
    assert "CONTINUOUS_RUN_INTERRUPTED_BY_PAUSE" in report["reason_codes"]


def test_original_state_log_corruption_blocks_valid_endurance_evidence(tmp_path, monkeypatch):
    """Final evidence SHA alone is insufficient when original state is altered."""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        name = "source-state-tamper"
        assert client.post("/interactive/continuous/sessions", json=_body(name)).status_code == 201
        base = "/interactive/continuous/sessions/" + name + "/endurance"
        assert client.post(base + "/start", json={"sample_interval_s": 1.}).status_code == 201
        workspace = next(tmp_path.rglob(EVIDENCE_FILE)).parent
        deadline = time.monotonic() + 4.
        while client.get(base + "/status").json()["sample_count"] < 1:
            assert time.monotonic() < deadline
            time.sleep(.02)
        assert client.post(base + "/stop").status_code == 200
        original = client.get(base + "/audit").json()
        assert original["verdict"] == "NOT_QUALIFIED", original
        assert original["original_sources_verified"] is True
        assert original["original_source_audit"]["state"]["events"] >= 3
        state_path = workspace / "state_events.jsonl"
        contents = state_path.read_text(encoding="utf-8")
        assert '"state": "READY"' in contents
        state_path.write_text(contents.replace('"state": "READY"', '"state": "FAILED"', 1), encoding="utf-8")
        bad = client.get(base + "/audit").json()
        assert bad["verdict"] == "BLOCKED"
        assert bad["original_sources_verified"] is False
        assert "ORIGINAL_SOURCE_RECONCILIATION_INVALID" in bad["reason_codes"]
        assert _cli(workspace).returncode == 2


def test_original_command_outbox_mismatch_blocks_even_if_endurance_chain_good(tmp_path, monkeypatch):
    """An ACKED effect is never inferred from changed SQLite mirror metadata."""
    import sqlite3
    from test_continuous_console import _ControlProtocolStub
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        name = "source-outbox-tamper"
        created = client.post("/interactive/continuous/sessions", json={
            **_body(name), "paced": False,
        })
        assert created.status_code == 201, created.text
        base = "/interactive/continuous/sessions/" + name + "/endurance"
        assert client.post(base + "/start", json={"sample_interval_s": 1.}).status_code == 201
        command = {
            "command_id": "source-ack-audit", "session_id": name,
            "session_revision": created.json()["revision"],
            "operation": "adcs.target.set", "target": "subsystem.adcs",
            "parameters": {"sigma_rn": [0.1, 0., 0.]},
            "actor_id": "r7h", "actor_role": "operator",
            "execute_at_sim_time_s": .2,
        }
        queued = client.post("/interactive/sessions/" + name + "/commands", json=command)
        assert queued.status_code == 202, queued.text
        assert client.post("/interactive/sessions/" + name + "/control",
                           json={"action": "start"}).status_code == 200
        deadline = time.monotonic() + 8.
        while True:
            rows = client.get("/interactive/sessions/" + name + "/commands",
                              params={"command_id": "source-ack-audit"}).json()["acks"]
            if any(row["state"] == "ACKED" for row in rows):
                break
            assert time.monotonic() < deadline, rows
            time.sleep(.02)
        assert client.post(base + "/stop").status_code == 200
        assert client.post("/interactive/sessions/" + name + "/control",
                           json={"action": "stop"}).status_code == 200
        good = client.get(base + "/audit").json()
        assert good["verdict"] == "NOT_QUALIFIED", good
        assert good["original_sources_verified"] is True
        assert good["original_source_audit"]["tc_ledger"]["accepted_commands"] == 1
        assert good["original_source_audit"]["tc_ledger"]["tc_all_acked"] is True
        workspace = next(tmp_path.rglob(EVIDENCE_FILE)).parent
        with sqlite3.connect(workspace / "command_ids.sqlite") as db:
            db.execute(
                "UPDATE command_event_outbox SET mirrored_entry_hash=? "
                "WHERE command_id=? AND state='ACKED'",
                ("f" * 64, "source-ack-audit"),
            )
        invalid = client.get(base + "/audit").json()
        assert invalid["verdict"] == "BLOCKED", invalid
        assert "ORIGINAL_SOURCE_RECONCILIATION_INVALID" in invalid["reason_codes"]
        assert _cli(workspace).returncode == 2
