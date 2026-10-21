"""R7c: kernel-enforced parent-death kill of the real Basilisk worker.

Linux PR_SET_PDEATHSIG is installed before the child builds its native graph.
The test crashes the *real owning Python SessionWorkspace/SQLite process*,
not the Basilisk child, then checks the child no longer executes.
"""
from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from sat_sim.interactive.native_parent_death_fence import (
    arm_native_parent_death_fence,
)
from sat_sim.interactive.manager import InteractiveSessionManager
from sat_sim.interactive.models import SessionState
from sat_sim.interactive.workspace import SessionWorkspace


def _proc_lifecycle(pid: int, start_ticks: int) -> str:
    try:
        parts = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(") ", 1)[1].split()
    except (FileNotFoundError, ProcessLookupError):
        return "GONE"
    # /proc/PID/stat field 22 is the process creation start-time counter.
    if int(parts[19]) != start_ticks:
        return "PID_REUSED"
    return parts[0]  # R/S/D/T/Z: Z is dead zombie waiting for init to reap


def test_parent_guard_rejects_wrong_or_missing_parent_identity():
    with pytest.raises(ValueError, match="BAD_EXPECTED_PARENT"):
        arm_native_parent_death_fence(1)
    with pytest.raises(RuntimeError, match="PARENT_DIED_BEFORE_ARM"):
        arm_native_parent_death_fence(os.getpid())


@pytest.mark.skipif(sys.platform != "linux", reason="Linux kernel PR_SET_PDEATHSIG only")
def test_real_basilisk_subprocess_dies_when_parent_os_process_is_killed(tmp_path):
    pytest.importorskip("Basilisk")
    probe = Path(__file__).with_name("native_parent_death_probe.py")
    marker = tmp_path / "observed-parent-native-child.json"
    parent = subprocess.Popen(
        [sys.executable, str(probe), str(tmp_path), str(marker)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True,
    )
    child_pid = None
    child_start = None
    try:
        deadline = time.monotonic() + 45.
        while not marker.is_file():
            if parent.poll() is not None:
                raise AssertionError("R7C_PARENT_EXITED_BEFORE_REAL_NATIVE_READY")
            if time.monotonic() > deadline:
                raise AssertionError("R7C_PARENT_CHILD_READY_TIMEOUT")
            time.sleep(.03)
        data = json.loads(marker.read_text(encoding="utf-8"))
        assert data["parent_pid"] == parent.pid
        assert data["native_resume_supported"] is False
        assert data["automatic_tc_replay_allowed"] is False
        assert data["fence"] == {
            "supported": True,
            "kind": "LINUX_PR_SET_PDEATHSIG_SIGKILL",
            "parent_pid": parent.pid,
        }
        child_pid = data["child_pid"]
        child_start = data["child_start_ticks"]
        assert child_pid != parent.pid
        assert _proc_lifecycle(child_pid, child_start) not in {"GONE", "Z", "PID_REUSED"}

        # No graceful worker/session cleanup: simulate hard server/process
        # SIGKILL while Basilisk graph lives in another OS process.
        parent.kill()
        assert parent.wait(timeout=10.) != 0

        deadline = time.monotonic() + 12.
        while _proc_lifecycle(child_pid, child_start) not in {"GONE", "Z", "PID_REUSED"}:
            if time.monotonic() > deadline:
                raise AssertionError("R7C_NATIVE_CHILD_SURVIVED_KILLED_PARENT")
            time.sleep(.04)
        # Our evidence may indicate zombie/unreaped by system init: that is
        # not a running simulator. It DOES NOT prove all descendants died.
        assert _proc_lifecycle(child_pid, child_start) in {"GONE", "Z", "PID_REUSED"}

        from sat_sim.interactive.command_mailbox import DeterministicCommandMailbox

        dbpath = tmp_path / data["session_id"] / "command_ids.sqlite"
        with sqlite3.connect(dbpath) as db:
            owner = db.execute(
                "SELECT owner_state FROM command_writer_epoch WHERE singleton=1"
            ).fetchone()
            state = db.execute(
                "SELECT state FROM command_lifecycle WHERE command_id=?",
                (data["queued_command_id"],),
            ).fetchone()
        assert owner == ("ACTIVE",)
        assert state == ("QUEUED",)
        with pytest.raises(RuntimeError, match="WRITER_ALREADY_ACTIVE"):
            DeterministicCommandMailbox(
                persistent_ledger=True, ledger_path=dbpath,
            )

        before = (tmp_path / data["session_id"] / "state_events.jsonl")
        existing = before.read_bytes()
        manager = InteractiveSessionManager(enabled=True, workspace_root=tmp_path)
        assert len(manager.interrupted_workspaces) == 1
        assert manager.interrupted_workspaces[0].valid is False
        assert "WRITER_ACTIVE_OR_ORPHANED" in manager.interrupted_workspaces[0].reason_code
        assert SessionWorkspace.inspect(tmp_path / data["session_id"]).state is SessionState.READY
        assert before.read_bytes() == existing
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=8.)
        # Cleanup is restricted to the original observed PID+birth identity.
        if child_pid is not None and child_start is not None:
            if _proc_lifecycle(child_pid, child_start) not in {"GONE", "Z", "PID_REUSED"}:
                os.kill(child_pid, signal.SIGKILL)
