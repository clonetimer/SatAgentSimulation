"""Durable queue worker for immutable Run Bundle execution."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from .durable_queue import DurableExecutionQueue, QueueJob, TERMINAL_STATES
from .run_bundle import RunStatus, finalize_interrupted_run, request_cancel as request_bundle_cancel


def _terminate_subprocess_tree(process: subprocess.Popen[str], *, grace_s: float = 5.0) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
        )
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            process.terminate()
    try:
        process.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
            )
        else:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                process.kill()
        try:
            process.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            pass


class ExecutionQueueWorker:
    def __init__(
        self,
        queue: DurableExecutionQueue,
        *,
        worker_id: str | None = None,
        poll_interval_s: float = 0.25,
        lease_seconds: float = 30.0,
        heartbeat_interval_s: float = 5.0,
        source_root: str | Path | None = None,
    ) -> None:
        self.queue = queue
        self.worker_id = worker_id or f"worker_{uuid4().hex[:12]}"
        self.poll_interval_s = float(poll_interval_s)
        self.lease_seconds = float(lease_seconds)
        self.heartbeat_interval_s = float(heartbeat_interval_s)
        self.source_root = Path(source_root or Path(__file__).resolve().parents[1]).resolve()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._current_process: subprocess.Popen[str] | None = None
        self._current_job_id: str | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.queue.requeue_stale()
        self.queue.register_worker(
            self.worker_id,
            capabilities={"execution_type": "simulation", "basilisk": True, "queue": "sqlite"},
        )
        self._stop.clear()
        self._thread = threading.Thread(target=self.run_forever, name=self.worker_id, daemon=True)
        self._thread.start()

    def stop(self, *, wait: bool = True) -> None:
        self._stop.set()
        self.queue.heartbeat_worker(self.worker_id, status="STOPPING")
        if self._current_process is not None and self._current_process.poll() is None:
            _terminate_subprocess_tree(self._current_process)
        if wait and self._thread:
            self._thread.join(timeout=5.0)
        self.queue.heartbeat_worker(self.worker_id, status="OFFLINE")

    def run_forever(self, *, max_jobs: int | None = None, idle_timeout_s: float | None = None) -> int:
        completed = 0
        idle_started = time.monotonic()
        self.queue.register_worker(
            self.worker_id,
            capabilities={"execution_type": "simulation", "basilisk": True, "queue": "sqlite"},
        )
        while not self._stop.is_set():
            self.queue.requeue_stale()
            job = self.queue.claim_next(worker_id=self.worker_id, lease_seconds=self.lease_seconds)
            if job is None:
                self.queue.heartbeat_worker(self.worker_id)
                if idle_timeout_s is not None and time.monotonic() - idle_started >= idle_timeout_s:
                    break
                self._stop.wait(self.poll_interval_s)
                continue
            idle_started = time.monotonic()
            self.execute_job(job)
            completed += 1
            if max_jobs is not None and completed >= max_jobs:
                break
        return completed

    def _command(self, job: QueueJob) -> list[str]:
        command = [
            sys.executable,
            "-m",
            "sat_sim.cli",
            "execute-run",
            job.bundle_root,
            "--plan-sha256",
            job.execution_plan_sha256,
            "--max-attempts",
            str(job.max_attempts),
        ]
        command.append("--hard-timeout" if job.hard_timeout else "--no-hard-timeout")
        return command

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def execute_job(self, job: QueueJob) -> QueueJob:
        latest = self.queue.get(job.job_id) or job
        if latest.state == "CANCELLED":
            return latest
        running = self.queue.mark_running(
            job.job_id,
            worker_id=self.worker_id,
            pid=None,
            lease_seconds=self.lease_seconds,
        )
        if running.state != "RUNNING":
            return running
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.source_root) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        root = Path(job.bundle_root)
        log_dir = self.queue.database_path.parent / "worker_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = log_dir / f"{job.job_id}.stdout.log"
        stderr_path = log_dir / f"{job.job_id}.stderr.log"
        stdout_handle = None
        stderr_handle = None
        try:
            stdout_handle = stdout_path.open("w", encoding="utf-8")
            stderr_handle = stderr_path.open("w", encoding="utf-8")
            popen_kwargs: dict[str, Any] = {
                "cwd": str(Path.cwd()),
                "env": env,
                "text": True,
                "stdout": stdout_handle,
                "stderr": stderr_handle,
            }
            if os.name == "nt":
                popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            else:
                popen_kwargs["start_new_session"] = True
            process = subprocess.Popen(self._command(job), **popen_kwargs)
        except Exception as exc:
            cleanup_errors: list[str] = []
            for handle in (stdout_handle, stderr_handle):
                if handle is None:
                    continue
                try:
                    handle.close()
                except Exception as cleanup_exc:
                    cleanup_errors.append(str(cleanup_exc))
            message = str(exc)
            if cleanup_errors:
                message += "; cleanup: " + "; ".join(cleanup_errors)
            return self.queue.finish(job.job_id, state="FAILED", error=message, worker_id=self.worker_id)
        self._current_process = process
        self._current_job_id = job.job_id
        if not self.queue.heartbeat(
            job.job_id,
            worker_id=self.worker_id,
            pid=process.pid,
            lease_seconds=self.lease_seconds,
        ):
            _terminate_subprocess_tree(process)
            stdout_handle.close()
            stderr_handle.close()
            current = self.queue.get(job.job_id)
            if current is None:
                raise KeyError(job.job_id)
            return current
        last_heartbeat = 0.0
        while process.poll() is None:
            now = time.monotonic()
            if now - last_heartbeat >= self.heartbeat_interval_s:
                owns_lease = self.queue.heartbeat(
                    job.job_id,
                    worker_id=self.worker_id,
                    lease_seconds=self.lease_seconds,
                    pid=process.pid,
                )
                if not owns_lease:
                    _terminate_subprocess_tree(process)
                    break
                self.queue.heartbeat_worker(self.worker_id)
                last_heartbeat = now
            current = self.queue.get(job.job_id)
            if current and current.state in {"CANCEL_REQUESTED", "CANCELLED"}:
                try:
                    request_bundle_cancel(root, reason=current.cancel_reason or "queue_cancel_requested")
                except RuntimeError:
                    pass
                _terminate_subprocess_tree(process)
                try:
                    finalize_interrupted_run(
                        root,
                        status=RunStatus.CANCELLED,
                        reason_code="EXECUTION_CANCELLED_BY_QUEUE",
                        reason=current.cancel_reason or "queue cancellation requested",
                    )
                except Exception:
                    pass
                break
            self._stop.wait(min(self.poll_interval_s, 0.5))
        if process.poll() is None:
            process.wait()
        stdout_handle.close()
        stderr_handle.close()
        stdout = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.exists() else ""
        stderr = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
        record = self._read_json(root / "run_record.json")
        validation = self._read_json(root / "validation" / "validation_outcome.json")
        state = str(record.get("status") or ("SUCCEEDED" if process.returncode == 0 else "FAILED")).upper()
        if state not in TERMINAL_STATES:
            state = "FAILED" if process.returncode else "SUCCEEDED"
        current = self.queue.get(job.job_id)
        if current and current.state in {"CANCEL_REQUESTED", "CANCELLED"}:
            state = "CANCELLED"
            try:
                finalize_interrupted_run(
                    root,
                    status=RunStatus.CANCELLED,
                    reason_code="EXECUTION_CANCELLED_BY_QUEUE",
                    reason=current.cancel_reason or "queue cancellation requested",
                )
                record = self._read_json(root / "run_record.json")
            except Exception:
                pass
        error = None
        if process.returncode != 0 or state in {"FAILED", "TIMED_OUT"}:
            error = str(
                record.get("terminal_reason_code")
                or stderr.strip()
                or stdout.strip()
                or f"worker exited with code {process.returncode}"
            )
        finished = self.queue.finish(
            job.job_id,
            state=state,
            return_code=process.returncode,
            validation_result=validation.get("result") or record.get("validation_result"),
            error=error,
            worker_id=self.worker_id,
        )
        self._current_process = None
        self._current_job_id = None
        return finished


__all__ = ["ExecutionQueueWorker"]
