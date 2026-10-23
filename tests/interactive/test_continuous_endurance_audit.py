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
