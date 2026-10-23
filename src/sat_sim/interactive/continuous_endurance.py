"""R7f genuine wall-clock endurance evidence for an already-running satellite.

A separate read-only observer samples the *real* InteractiveSession and host
using the R7e observability endpoint's common implementation. It never owns,
advances, restarts or sends commands to a Basilisk runtime.

Evidence is append-only/fsynced JSONL with a SHA-256 chain (integrity rather
than authentication). Crash/truncation is detectable and never treated as a
valid completion. A qualifying >=24h continuous sample is only *eligible for
human assessment*; it is not automatic release or physics-restoration proof.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .continuous_observability import observe_continuous_session
from .models import SessionState

EVIDENCE_FILE = "continuous_endurance_evidence.jsonl"
SUMMARY_FILE = "continuous_endurance_summary.json"
TERMINAL = {SessionState.COMPLETED, SessionState.ABORTED,
            SessionState.FAILED, SessionState.INTERRUPTED}
ZERO = "0" * 64


def _serialize(row: dict[str, Any]) -> bytes:
    return json.dumps(
        row, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _hash(row: dict[str, Any]) -> str:
    return hashlib.sha256(_serialize(row)).hexdigest()


def verify_endurance_evidence(path: Path) -> dict[str, Any]:
    """Stream-validation of every row; safe on large traces (O(1) RAM)."""
    path = Path(path)
    previous = ZERO
    count = 0
    first: dict[str, Any] | None = None
    last: dict[str, Any] | None = None
    prior_elapsed = -1.
    prior_frames = -1
    with path.open("rb") as source:
        for raw in source:
            row = json.loads(raw)
            if (not isinstance(row, dict)
                    or row.get("schema_version") != "sat-endurance-sample.v1"
                    or type(row.get("sequence")) is not int
                    or row["sequence"] != count
                    or row.get("previous_hash") != previous):
                raise ValueError("ENDURANCE_EVIDENCE_CHAIN_INVALID")
            observed_hash = row.get("entry_hash")
            content = {k: v for k, v in row.items() if k != "entry_hash"}
            if not isinstance(observed_hash, str) or _hash(content) != observed_hash:
                raise ValueError("ENDURANCE_EVIDENCE_DIGEST_INVALID")
            sample = row.get("observation")
            if not isinstance(sample, dict) or sample.get("schema_version") != "sat-continuous-observability.v1":
                raise ValueError("ENDURANCE_EVIDENCE_METRICS_INVALID")
            wall = sample.get("wall_elapsed_s")
            if (type(wall) not in (int, float) or wall < prior_elapsed
                    or not __import__("math").isfinite(wall)):
                raise ValueError("ENDURANCE_EVIDENCE_WALL_MONOTONIC_INVALID")
            frames = sample.get("telemetry", {}).get("durable_frame_count")
            if frames is not None and (type(frames) is not int or frames < prior_frames):
                raise ValueError("ENDURANCE_EVIDENCE_FRAMES_MONOTONIC_INVALID")
            if frames is not None:
                prior_frames = frames
            prior_elapsed = wall
            if first is None:
                first = row
            last = row
            previous = observed_hash
            count += 1
    return {
        "verified": True,
        "samples": count,
        "first_wall_elapsed_s": first["observation"]["wall_elapsed_s"] if first else None,
        "last_wall_elapsed_s": last["observation"]["wall_elapsed_s"] if last else None,
        "final_entry_hash": previous,
        "session_id": first["observation"]["session_id"] if first else None,
    }


def verify_endurance_summary(path: Path) -> dict[str, Any]:
    """Require complete, matching evidence+summary; never bless crash tails."""
    path = Path(path)
    with path.open("rb") as source:
        summary = json.load(source)
    if not isinstance(summary, dict):
        raise ValueError("ENDURANCE_SUMMARY_INVALID")
    digest = summary.get("summary_sha256")
    unsigned = {k: v for k, v in summary.items() if k != "summary_sha256"}
    if not isinstance(digest, str) or _hash(unsigned) != digest:
        raise ValueError("ENDURANCE_SUMMARY_DIGEST_INVALID")
    evidence = verify_endurance_evidence(path.parent / EVIDENCE_FILE)
    if (summary.get("samples") != evidence["samples"]
            or summary.get("final_evidence_hash") != evidence["final_entry_hash"]
            or summary.get("session_id") != evidence["session_id"]):
        raise ValueError("ENDURANCE_SUMMARY_EVIDENCE_MISMATCH")
    return {"summary": summary, "evidence": evidence}


class EnduranceRecorder:
    def __init__(self, session: Any, *, interval_s: float = 5.) -> None:
        if (not session.spec.continuous or session._workspace is None
                or not 1. <= interval_s <= 3600.):
            raise ValueError("ENDURANCE_REQUIRES_PERSISTENT_CONTINUOUS_SESSION")
        self.session = session
        self.interval_s = float(interval_s)
        self.path = session._workspace.path / EVIDENCE_FILE
        self.summary_path = session._workspace.path / SUMMARY_FILE
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._status = "NOT_STARTED"
        self._started_monotonic: float | None = None
        self._count = 0
        self._hash = ZERO
        self._first: dict[str, Any] | None = None
        self._last: dict[str, Any] | None = None
        self._sample_gap_max_s = 0.
        self._observed_running_wall_s = 0.
        self._ever_observed_running = False
        self._observed_pause_after_run = False
        self._lag_max_s = 0.
        self._disk_free_min: int | None = None
        self._rss_first: int | None = None
        self._rss_last: int | None = None
        self._rss_max: int | None = None
        self._alerts: dict[str, int] = {}
        self._error: str | None = None
        self._summary: dict[str, Any] | None = None

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._status != "NOT_STARTED":
                raise RuntimeError("ENDURANCE_ALREADY_STARTED")
            if self.path.exists() or self.summary_path.exists():
                raise RuntimeError("ENDURANCE_PREVIOUS_EVIDENCE_EXISTS_NO_OVERWRITE")
            if self.session.state in TERMINAL:
                raise RuntimeError("ENDURANCE_SESSION_TERMINAL")
            # Reserve the pathname before returning to a concurrent caller;
            # no reopening a prior run, no truncated existing evidence.
            with self.path.open("xb") as output:
                output.flush()
                os.fsync(output.fileno())
            self._started_monotonic = time.monotonic()
            self._status = "RECORDING"
            self._thread = threading.Thread(
                target=self._run, name="sat-endurance-" + self.session.spec.session_id,
                daemon=True,
            )
            self._thread.start()
            return self._status_view()

    def _alert(self, code: str) -> None:
        self._alerts[code] = self._alerts.get(code, 0) + 1

    def _sample(self) -> None:
        observation = observe_continuous_session(self.session)
        with self._lock:
            prior = self._last
            if prior is not None:
                elapsed_gap = observation["wall_elapsed_s"] - prior["observation"]["wall_elapsed_s"]
                if elapsed_gap < 0:
                    raise RuntimeError("ENDURANCE_WALL_CLOCK_MOVED_BACKWARDS")
                self._sample_gap_max_s = max(self._sample_gap_max_s, elapsed_gap)
                # Intervals with paused/ready/terminal endpoints do not count
                # towards *observed active running* wall-clock eligibility.
                # Total session elapsed time includes setup/pauses and MUST
                # NEVER qualify a 24h uninterrupted-running claim on its own.
                if (observation["session_state"] == SessionState.RUNNING.value
                        and prior["observation"]["session_state"] == SessionState.RUNNING.value):
                    self._observed_running_wall_s += elapsed_gap
                if elapsed_gap > max(self.interval_s * 3., self.interval_s + 5.):
                    self._alert("SAMPLE_INTERVAL_GAP")
            if observation["session_state"] == SessionState.RUNNING.value:
                self._ever_observed_running = True
            elif (self._ever_observed_running
                  and observation["session_state"] in {
                      SessionState.READY.value, SessionState.PAUSED.value
                  }):
                self._observed_pause_after_run = True
                self._alert("CONTINUOUS_RUN_INTERRUPTED_BY_PAUSE")
            tm = observation["telemetry"]
            if observation["session_state"] == SessionState.RUNNING.value:
                if tm["status"] != "FRESH":
                    # Allow the very first sampling instant of a genuinely
                    # fresh run before its first native TM recorder output.
                    # This is not a silent ongoing grace period: every
                    # subsequent RUNNING sample checks the actual stream.
                    if not (self._count == 0 and tm["status"] == "NO_TELEMETRY"):
                        self._alert("TELEMETRY_" + tm["status"])
            lag = tm["lag_wall_s"]
            if lag is not None:
                self._lag_max_s = max(self._lag_max_s, lag)
            disk = observation["disk"]
            if disk is not None:
                if not disk["available"]:
                    self._alert("DISK_OBSERVATION_UNAVAILABLE")
                else:
                    free = disk["free_bytes"]
                    self._disk_free_min = min(self._disk_free_min, free) if self._disk_free_min is not None else free
                    if free <= disk["reserved_bytes"]:
                        self._alert("DISK_BELOW_RESERVED_THRESHOLD")
            process = observation["process"]
            if not process["available"] and observation["session_state"] == SessionState.RUNNING.value:
                self._alert("RUNNING_PROCESS_METRICS_UNAVAILABLE")
            if process["available"]:
                rss = process["resident_bytes"]
                self._rss_first = self._rss_first if self._rss_first is not None else rss
                self._rss_last = rss
                self._rss_max = max(self._rss_max, rss) if self._rss_max is not None else rss
            if prior is not None:
                prev_tm = prior["observation"]["telemetry"]
                if (observation["session_state"] == SessionState.RUNNING.value
                        and tm["last_sequence"] is not None
                        and tm["last_sequence"] == prev_tm["last_sequence"]
                        and observation["wall_elapsed_s"] - prior["observation"]["wall_elapsed_s"] >= max(5., 3*self.session.spec.quantum_s)):
                    self._alert("RUNNING_TELEMETRY_NO_SEQUENCE_PROGRESS")
            row = {
                "schema_version": "sat-endurance-sample.v1",
                "sequence": self._count,
                "previous_hash": self._hash,
                "observation": observation,
            }
            row["entry_hash"] = _hash(row)
            with self.path.open("ab") as output:
                output.write(_serialize(row) + b"\n")
                output.flush()
                os.fsync(output.fileno())
            self._count += 1
            self._hash = row["entry_hash"]
            self._first = self._first or row
            self._last = row

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                self._sample()
                if self.session.state in TERMINAL:
                    break
                if self._stop.wait(self.interval_s):
                    break
        except Exception as exc:
            with self._lock:
                self._error = type(exc).__name__ + ": " + str(exc)[:300]
                self._alert("ENDURANCE_RECORDER_ERROR")
        finally:
            self._finalize()

    def _finalize(self) -> None:
        with self._lock:
            if self._count == 0 and self._error is None:
                # Immediate stop before the sampler's first scheduling slice
                # cannot be reported as a verified, completed endurance run.
                self._error = "ENDURANCE_NO_DURABLE_SAMPLES"
                self._alert("ENDURANCE_NO_DURABLE_SAMPLES")
            first = self._first["observation"] if self._first else None
            last = self._last["observation"] if self._last else None
            span = (
                max(0., last["wall_elapsed_s"] - first["wall_elapsed_s"])
                if first and last else 0.
            )
            eligible = (
                self._observed_running_wall_s >= 86400.
                and self._count >= 2
                and not self._alerts
                and not self._observed_pause_after_run
                and self._error is None
                and self._sample_gap_max_s <= self.interval_s * 3.
                and last is not None
                and last["session_state"] == SessionState.COMPLETED.value
            )
            summary = {
                "schema_version": "sat-endurance-summary.v1",
                "session_id": self.session.spec.session_id,
                "recording_status": "INCOMPLETE_ERROR" if self._error else "FINISHED",
                "reason_code": self._error,
                "samples": self._count,
                "interval_target_s": self.interval_s,
                "observed_wall_span_s": span,
                "observed_running_wall_s": self._observed_running_wall_s,
                "observed_pause_after_run": self._observed_pause_after_run,
                "sim_progress_s": (last["sim_elapsed_s"] - first["sim_elapsed_s"] if first and last else 0.),
                "max_sample_gap_wall_s": self._sample_gap_max_s,
                "max_telemetry_lag_wall_s": self._lag_max_s,
                "min_free_disk_bytes": self._disk_free_min,
                "rss_first_bytes": self._rss_first,
                "rss_last_bytes": self._rss_last,
                "rss_peak_bytes": self._rss_max,
                "alerts": dict(sorted(self._alerts.items())),
                "final_evidence_hash": self._hash,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "qualified_24h_wallclock": False,
                "eligible_for_manual_24h_review": eligible,
                "native_resume_supported": False,
                "automatic_tc_replay_allowed": False,
            }
            summary["summary_sha256"] = _hash(summary)
            staged = self.summary_path.with_suffix(".pending")
            try:
                with staged.open("xb") as output:
                    output.write(_serialize(summary) + b"\n")
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(staged, self.summary_path)
            except OSError as exc:
                # Never report FINISHED while the durable summary is absent.
                # Keep the evidence tail for explicit forensic inspection.
                self._error = "ENDURANCE_SUMMARY_DURABILITY_FAILURE:" + type(exc).__name__
                self._alert("ENDURANCE_SUMMARY_DURABILITY_FAILURE")
                self._status = "INCOMPLETE_ERROR"
                self._summary = None
            else:
                self._summary = summary
                self._status = summary["recording_status"]

    def stop(self, *, timeout_s: float = 15.) -> dict[str, Any]:
        with self._lock:
            if self._status == "NOT_STARTED":
                raise RuntimeError("ENDURANCE_NOT_STARTED")
            self._stop.set()
            worker = self._thread
        if worker is not None:
            worker.join(timeout=timeout_s)
            if worker.is_alive():
                raise RuntimeError("ENDURANCE_SAMPLER_STOP_TIMEOUT")
        return self.status()

    def _status_view(self) -> dict[str, Any]:
        latest = self._last["observation"] if self._last else None
        return {
            "schema_version": "sat-endurance-status.v1",
            "session_id": self.session.spec.session_id,
            "recording_status": self._status,
            "interval_s": self.interval_s,
            "sample_count": self._count,
            "last_observation": latest,
            "alerts": dict(sorted(self._alerts.items())),
            "reason_code": self._error,
            "summary": self._summary,
            "evidence_available": self.path.is_file(),
            "native_resume_supported": False,
            "automatic_tc_replay_allowed": False,
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._status_view()


class EnduranceRegistry:
    """No automatic takeover of an existing recorder/evidence file."""
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, EnduranceRecorder] = {}

    def start(self, session: Any, interval_s: float) -> dict[str, Any]:
        with self._lock:
            if session.spec.session_id in self._sessions:
                raise RuntimeError("ENDURANCE_RECORDER_EXISTS")
            recorder = EnduranceRecorder(session, interval_s=interval_s)
            status = recorder.start()
            self._sessions[session.spec.session_id] = recorder
            return status

    def get(self, session_id: str) -> EnduranceRecorder | None:
        with self._lock:
            return self._sessions.get(session_id)
