"""R7a restricted child-process lifecycle barrier for Basilisk pilot workers.

Unlike PID checks, a supervisor proves that its *own Popen child* exited
by calling wait() on that exact process handle. It never assumes that a
PID number belongs to the same OS process after a restart. The proof is
a test-only local evidence record (SHA-256 is NOT authentication).

The barrier proves one direct child was reaped. It does not prove that
unrelated processes, detached grandchildren, GPU drivers or external
actuators stopped, that Basilisk C++ state is serializable, or that an
original continuous SessionWorkspace is safe to resume.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Sequence

READY_SCHEMA = "basilisk-supervised-child-ready.v1"
BARRIER_SCHEMA = "basilisk-direct-child-reap-barrier.v1"
PROFILES = {"stub-worker", "adcs-r5-joint-source"}


def _canonical(record: dict[str, Any]) -> bytes:
    return json.dumps(
        record, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(record)).hexdigest()


def write_child_ready(path: Path, *, profile: str, checkpoint_ns: int,
                      checkpoint_sha256: str | None) -> None:
    """Called by the supervised worker after materializing its native source."""
    if profile not in PROFILES or type(checkpoint_ns) is not int:
        raise ValueError("SUPERVISOR_READY_PROFILE_INVALID")
    nonce = os.environ.get("SAT_R7_DIRECT_CHILD_NONCE")
    if nonce is None or len(nonce) != 64:
        raise ValueError("SUPERVISOR_CHILD_MISSING_HANDSHAKE")
    content = {
        "schema_version": READY_SCHEMA,
        "run_nonce": nonce,
        "pid": os.getpid(),
        "profile": profile,
        "checkpoint_ns": checkpoint_ns,
        "checkpoint_sha256": checkpoint_sha256,
    }
    path = Path(path)
    if path.exists():
        raise ValueError("SUPERVISOR_READY_ALREADY_EXISTS")
    staged = path.with_suffix(".pending")
    with staged.open("x", encoding="utf-8") as output:
        output.write(json.dumps(content, sort_keys=True) + "\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(staged, path)
    if os.name == "posix":
        fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def verify_reap_barrier(path: Path, *, expected_pid: int | None = None,
                        expected_nonce: str | None = None,
                        expected_checkpoint_sha256: str | None = None) -> dict[str, Any]:
    """Verify stored evidence; this alone is NOT a live OS-process check."""
    path = Path(path)
    if path.with_suffix(".pending").exists():
        raise ValueError("SUPERVISOR_BARRIER_STAGING_FOUND")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError("SUPERVISOR_BARRIER_INVALID_DOCUMENT")
    data = dict(doc)
    digest = data.pop("digest_sha256", None)
    if not isinstance(digest, str) or digest != _digest(data):
        raise ValueError("SUPERVISOR_BARRIER_DIGEST_INVALID")
    if set(data) != {
        "schema_version", "pid", "run_nonce_sha256", "profile",
        "checkpoint_ns", "checkpoint_sha256", "termination_request",
        "returncode", "direct_child_wait_observed", "process_tree_quiescence_proven",
        "native_resume_supported", "automatic_tc_replay_allowed",
        "source_child_was_ready",
    } or data["schema_version"] != BARRIER_SCHEMA:
        raise ValueError("SUPERVISOR_BARRIER_SCHEMA_INVALID")
    if (type(data["pid"]) is not int or data["pid"] <= 0
            or data["profile"] not in PROFILES
            or type(data["returncode"]) is not int
            or data["returncode"] == 0
            or data["direct_child_wait_observed"] is not True
            or data["source_child_was_ready"] is not True
            or data["process_tree_quiescence_proven"] is not False
            or data["native_resume_supported"] is not False
            or data["automatic_tc_replay_allowed"] is not False
            or data["termination_request"] not in {"SIGTERM", "SIGKILL"}):
        raise ValueError("SUPERVISOR_BARRIER_UNSUPPORTED_CLAIM")
    if expected_pid is not None and data["pid"] != expected_pid:
        raise ValueError("SUPERVISOR_BARRIER_CHILD_ID_MISMATCH")
    if (expected_nonce is not None
            and data["run_nonce_sha256"] != hashlib.sha256(
                expected_nonce.encode("ascii")
            ).hexdigest()):
        raise ValueError("SUPERVISOR_BARRIER_NONCE_MISMATCH")
    if (expected_checkpoint_sha256 is not None
            and data["checkpoint_sha256"] != expected_checkpoint_sha256):
        raise ValueError("SUPERVISOR_BARRIER_CHECKPOINT_MISMATCH")
    return doc


class DirectChildSupervisor:
    """Test harness: handoff requires an actual held OS child handle."""

    def __init__(self, *, child: subprocess.Popen[bytes], nonce: str,
                 ready_path: Path, barrier_path: Path, profile: str) -> None:
        self._child = child
        self._nonce = nonce
        self.ready_path = Path(ready_path)
        self.barrier_path = Path(barrier_path)
        self.profile = profile
        self._ready: dict[str, Any] | None = None
        self._attested = False

    @classmethod
    def launch(cls, argv: Sequence[str], *, ready_path: Path,
               barrier_path: Path, profile: str,
               cwd: Path | None = None) -> "DirectChildSupervisor":
        if (profile not in PROFILES or not argv
                or ready_path.exists() or barrier_path.exists()
                or barrier_path.with_suffix(".pending").exists()):
            raise ValueError("SUPERVISOR_LAUNCH_INVALID")
        nonce = secrets.token_hex(32)
        env = dict(os.environ)
        env["SAT_R7_DIRECT_CHILD_NONCE"] = nonce
        child = subprocess.Popen(
            list(argv), cwd=str(cwd) if cwd is not None else None,
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=(os.name == "posix"),
        )
        return cls(
            child=child, nonce=nonce, ready_path=ready_path,
            barrier_path=barrier_path, profile=profile,
        )

    @property
    def pid(self) -> int:
        return self._child.pid

    @property
    def returncode(self) -> int | None:
        return self._child.poll()

    def observe_ready(self, *, timeout_s: float = 15.) -> dict[str, Any]:
        if self._ready is not None:
            return self._ready
        deadline = time.monotonic() + timeout_s
        while True:
            if self.ready_path.is_file():
                doc = json.loads(self.ready_path.read_text(encoding="utf-8"))
                if (not isinstance(doc, dict) or set(doc) != {
                    "schema_version", "run_nonce", "pid", "profile",
                    "checkpoint_ns", "checkpoint_sha256",
                } or doc["schema_version"] != READY_SCHEMA
                        or doc["run_nonce"] != self._nonce
                        or doc["pid"] != self._child.pid
                        or doc["profile"] != self.profile
                        or type(doc["checkpoint_ns"]) is not int
                        or (self.profile == "adcs-r5-joint-source" and (
                            doc["checkpoint_ns"] != 2_000_000_000
                            or not isinstance(doc["checkpoint_sha256"], str)
                            or len(doc["checkpoint_sha256"]) != 64
                        ))):
                    raise ValueError("SUPERVISOR_READY_CHILD_IDENTITY_INVALID")
                if self._child.poll() is not None:
                    raise RuntimeError("SUPERVISOR_READY_CHILD_ALREADY_EXITED")
                self._ready = doc
                return doc
            if self._child.poll() is not None:
                raise RuntimeError("SUPERVISOR_CHILD_EXITED_BEFORE_READY")
            if time.monotonic() >= deadline:
                raise TimeoutError("SUPERVISOR_CHILD_READY_TIMEOUT")
            time.sleep(.02)

    def terminate_and_attest(self, *, force: bool = False,
                             timeout_s: float = 5.) -> dict[str, Any]:
        """Explicit local shutdown and wait(); does NOT create a new worker."""
        if self._attested or self.barrier_path.exists():
            raise ValueError("SUPERVISOR_BARRIER_ALREADY_ATTESTED")
        if self._ready is None:
            raise ValueError("SUPERVISOR_BARRIER_NO_READY_HANDSHAKE")
        if self._child.poll() is not None:
            raise RuntimeError("SUPERVISOR_CHILD_DIED_BEFORE_TERMINATION_REQUEST")
        sig = signal.SIGKILL if force else signal.SIGTERM
        if os.name == "posix":
            os.killpg(self._child.pid, sig)
        else:
            if force:
                self._child.kill()
            else:
                self._child.terminate()
        # Critical: this invokes wait() on our original Popen object, not
        # os.kill(pid,0), a Linux /proc sample, or a possibly recycled PID.
        try:
            code = self._child.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            raise RuntimeError("SUPERVISOR_DIRECT_CHILD_NOT_REAPED")
        if code == 0:
            raise RuntimeError("SUPERVISOR_UNEXPECTED_CLEAN_TERMINATION")
        data = {
            "schema_version": BARRIER_SCHEMA,
            "pid": self._child.pid,
            "run_nonce_sha256": hashlib.sha256(self._nonce.encode("ascii")).hexdigest(),
            "profile": self.profile,
            "checkpoint_ns": self._ready["checkpoint_ns"],
            "checkpoint_sha256": self._ready["checkpoint_sha256"],
            "termination_request": "SIGKILL" if force else "SIGTERM",
            "returncode": code,
            "direct_child_wait_observed": True,
            "process_tree_quiescence_proven": False,
            "native_resume_supported": False,
            "automatic_tc_replay_allowed": False,
            "source_child_was_ready": True,
        }
        data["digest_sha256"] = _digest(data)
        staged = self.barrier_path.with_suffix(".pending")
        with staged.open("x", encoding="utf-8") as output:
            output.write(json.dumps(data, sort_keys=True) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(staged, self.barrier_path)
        if os.name == "posix":
            fd = os.open(str(self.barrier_path.parent), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        self._attested = True
        return verify_reap_barrier(
            self.barrier_path, expected_pid=self._child.pid,
            expected_nonce=self._nonce,
            expected_checkpoint_sha256=self._ready["checkpoint_sha256"],
        )

    def cleanup_without_attestation(self) -> None:
        """Always reap child after failed handshake; never write a proof."""
        if self._child.poll() is None:
            if os.name == "posix":
                try:
                    os.killpg(self._child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                self._child.kill()
        self._child.wait(timeout=5.)
