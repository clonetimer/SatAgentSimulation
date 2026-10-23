"""R7c POSIX fault-injection helper: REAL Basilisk graph behind an OS parent.

This helper must be launched as a separate process. Its owning test kills
the *parent process* with SIGKILL and observes the child's kernel lifecycle.
Do not run this directly inside the pytest collection process.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from sat_sim.interactive.manager import InteractiveSession
from sat_sim.interactive.models import (
    InteractiveSessionSpec, ActorRole, Telecommand,
)
from sat_sim.interactive.runtime_factory import build_persistent_runtime
from sat_sim.interactive.workspace import SessionWorkspace


def main(root: Path, marker: Path) -> None:
    spec = InteractiveSessionSpec(
        session_id="r7c-real-parent-death",
        capability_id="whole_spacecraft.unified_native.v1",
        task_spec={
            "simulation": {
                "duration_s": 1., "sample_s": .2,
                "solver": {"step_s": .2},
            },
            "parameters": {"values": {}},
        },
        continuous=True, quantum_s=.2, paced=False,
        heartbeat_interval_s=1., idle_timeout_s=30.,
    )
    ws = SessionWorkspace(root, spec)
    runtime, end = build_persistent_runtime(spec, supervised=True)
    session = InteractiveSession(spec, runtime, end_time_s=end, workspace=ws)
    session.prepare()
    cmd = Telecommand(
        command_id="future-tc-not-to-replay", session_id=spec.session_id,
        session_revision=session.revision, operation="adcs.target.set",
        target="subsystem.adcs", parameters={"sigma_rn": [.1, 0., 0.]},
        actor_id="r7c-operator", actor_role=ActorRole.OPERATOR,
        execute_at_sim_time_s=4.,
    )
    ack = session.submit_command(cmd)
    assert ack.state.value == "QUEUED"
    assert runtime.parent_death_fence is not None
    child_pid = runtime.child_pid
    assert child_pid is not None

    stat = Path(f"/proc/{child_pid}/stat").read_text(encoding="utf-8")
    after_name = stat.rsplit(") ", 1)[1].split()
    payload = {
        "parent_pid": os.getpid(),
        "child_pid": child_pid,
        "child_start_ticks": int(after_name[19]),
        "fence": runtime.parent_death_fence,
        "session_id": spec.session_id,
        "queued_command_id": cmd.command_id,
        "native_resume_supported": False,
        "automatic_tc_replay_allowed": False,
    }
    if marker.exists():
        raise RuntimeError("R7C_MARKER_ALREADY_EXISTS")
    staged = marker.with_suffix(".pending")
    with staged.open("x", encoding="utf-8") as f:
        json.dump(payload, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(staged, marker)
    # Keep the genuine graph prepared but not advancing, then have the
    # supervising pytest process kill this parent without cleanup.
    while True:
        time.sleep(.1)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: native_parent_death_probe.py ROOT MARKER")
    main(Path(sys.argv[1]), Path(sys.argv[2]))
