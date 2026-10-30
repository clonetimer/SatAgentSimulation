"""R7b opt-in, fresh-process Basilisk runtime execution (NO automatic resume).

The existing InteractiveSession owns all TC/ACK SQLite state and runs on its
parent worker thread; only the PersistentSimulationRuntime graph lives in a
distinct, directly owned Python process. RPC has an exact child handle, a
per-launch random nonce, ordered request IDs, and a hard fail-closed timeout.
A lost response to apply_command/advance_to is *ambiguous*, never retried.

This is process isolation and reliable direct-child reaping, not a generic
Basilisk checkpoint or a proof that an interrupted spacecraft can restart.
"""
from __future__ import annotations

import multiprocessing as mp
import copy
import os
import secrets
import sys
from pathlib import Path
from typing import Any

from .models import InteractiveSessionSpec, Telecommand
from .native_parent_death_fence import arm_native_parent_death_fence
from .native_cpu_scheduling import configure_native_worker_scheduling

_SCHEMA = "sat-native-owner-rpc.v1"
_OPERATIONS = frozenset({
    "prepare", "advance_to", "apply_command", "read_delta",
    "finalize", "abort", "checkpoint_audit",
})


def _native_process_main(
    connection: Any, spec_raw: dict[str, Any], nonce: str,
    expected_parent_pid: int,
) -> None:
    """Runs only in an mp.spawn child. Native graph never crosses the pipe."""
    runtime = None
    try:
        # This must happen *before* constructing Basilisk native C++ graph
        # objects. Linux delivers SIGKILL to this exact child when its actual
        # parent exits, even if the runtime is inside a native integration.
        parent_fence = arm_native_parent_death_fence(expected_parent_pid)
        cpu_scheduling = configure_native_worker_scheduling()
        # Two-phase startup is deliberate: the child announces its identity
        # BEFORE creating a Basilisk C++ graph. The parent must successfully
        # install its Windows Job or verify the Linux pdeathsig handshake and
        # only then issue the first prepare RPC to permit construction.
        spec = InteractiveSessionSpec.model_validate(spec_raw)
        if not spec.continuous:
            raise ValueError("SUPERVISED_RUNTIME_REQUIRES_UNBOUNDED_NATIVE")
        connection.send({
            "schema": _SCHEMA, "seq": 0, "nonce": nonce,
            "pid": os.getpid(), "ok": True, "time_s": 0.,
            "payload": {
                "ready": True, "native_resume_supported": False,
                "parent_death_fence": parent_fence,
                "cpu_scheduling": cpu_scheduling,
                "automatic_tc_replay_allowed": False,
            },
        })
        next_seq = 1
        while True:
            message = connection.recv()
            if (not isinstance(message, dict)
                    or set(message) != {"schema", "seq", "nonce", "op", "payload"}
                    or message["schema"] != _SCHEMA
                    or message["nonce"] != nonce
                    or message["seq"] != next_seq
                    or message["op"] not in _OPERATIONS):
                raise ValueError("SUPERVISED_NATIVE_RPC_IDENTITY_OR_ORDER_INVALID")
            op = message["op"]
            payload = message["payload"]
            try:
                if op == "prepare":
                    if next_seq != 1 or runtime is not None:
                        raise RuntimeError("SUPERVISED_NATIVE_PREPARE_OUT_OF_ORDER")
                    from .runtime_factory import build_persistent_runtime
                    runtime, end_time = build_persistent_runtime(spec, supervised=False)
                    if end_time is not None:
                        raise ValueError("SUPERVISED_RUNTIME_REQUIRES_UNBOUNDED_NATIVE")
                    runtime.prepare()
                    value = None
                elif runtime is None:
                    raise RuntimeError("SUPERVISED_NATIVE_PREPARE_REQUIRED")
                elif op == "advance_to":
                    value = runtime.advance_to(float(payload))
                elif op == "apply_command":
                    runtime.apply_command(Telecommand.model_validate(payload))
                    value = None
                elif op == "read_delta":
                    value = runtime.read_delta()
                elif op == "checkpoint_audit":
                    value = runtime.audit_native_checkpoint_coverage()
                elif op == "finalize":
                    value = runtime.finalize()
                elif op == "abort":
                    runtime.abort()
                    value = None
                else:
                    raise ValueError("SUPERVISED_RPC_UNKNOWN_OPERATION")
                connection.send({
                    "schema": _SCHEMA, "seq": next_seq, "nonce": nonce,
                    "pid": os.getpid(), "ok": True,
                    "time_s": float(runtime.current_time_s) if runtime is not None else 0.,
                    "payload": value,
                })
                next_seq += 1
                if op in {"finalize", "abort"}:
                    break
            except BaseException as exc:
                # Never try to auto-repeat the failed effect or restore C++
                # graph after an unknown commit boundary.
                connection.send({
                    "schema": _SCHEMA, "seq": next_seq, "nonce": nonce,
                    "pid": os.getpid(), "ok": False,
                    "time_s": float(runtime.current_time_s) if runtime is not None else 0.,
                    "payload": {"reason_code": "NATIVE_SUBPROCESS_OPERATION_FAILED",
                                "type": type(exc).__name__, "detail": str(exc)},
                })
                break
    except BaseException:
        # Parent observes child death or response absence, then blocks. The
        # worker may emit a diagnostic traceback to stderr, never a replay.
        raise
    finally:
        connection.close()


class SupervisedContinuousNativeRuntime:
    """PersistentSimulationRuntime adapter using one non-restartable child."""

    def __init__(self, spec: InteractiveSessionSpec, *, timeout_s: float = 60.) -> None:
        from sat_sim.bsk_engine.unified_native import WHOLE_UNIFIED_CAPABILITY_ID
        if (not spec.continuous or spec.capability_id != WHOLE_UNIFIED_CAPABILITY_ID
                or timeout_s <= 0):
            raise ValueError("SUPERVISED_NATIVE_ONLY_WHOLE_CONTINUOUS")
        self._spec = spec
        self._timeout = timeout_s
        self._ctx = mp.get_context("spawn")
        self._process: mp.Process | None = None
        self._connection: Any = None
        self._nonce: str | None = None
        self._seq = 0
        self._current_time_s = 0.
        self._closed = False
        self._faulted = False
        self._last_reap: dict[str, Any] | None = None
        self._parent_death_fence: dict[str, object] | None = None
        self._windows_job: Any = None
        self._cpu_scheduling: dict[str, object] | None = None

    @property
    def current_time_s(self) -> float:
        return self._current_time_s

    @property
    def child_pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def parent_death_fence(self) -> dict[str, object] | None:
        return dict(self._parent_death_fence) if self._parent_death_fence is not None else None

    @property
    def last_reap(self) -> dict[str, Any] | None:
        return dict(self._last_reap) if self._last_reap is not None else None

    @property
    def cpu_scheduling(self) -> dict[str, object] | None:
        return copy.deepcopy(self._cpu_scheduling)

    def _reap(self, *, force: bool) -> None:
        process = self._process
        if process is None:
            return
        if process.is_alive() and force:
            process.kill()
        process.join(timeout=5.)
        if process.is_alive():
            # Closing the Windows Job triggers kernel termination even when
            # the child is stuck in native code. Parent never self-restarts.
            if self._windows_job is not None:
                self._windows_job.close()
                self._windows_job = None
                process.join(timeout=5.)
            if process.is_alive():
                self._faulted = True
                raise RuntimeError("SUPERVISED_NATIVE_PROCESS_NOT_REAPED")
        if self._windows_job is not None:
            self._windows_job.close()
            self._windows_job = None
        self._last_reap = {
            "child_pid": process.pid,
            "exitcode": process.exitcode,
            "direct_child_join_observed": True,
            "process_tree_quiescence_proven": False,
            "native_resume_supported": False,
            "automatic_tc_replay_allowed": False,
        }
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        self._closed = True

    def _recv(self, *, seq: int) -> Any:
        proc = self._process
        if proc is None or self._connection is None:
            raise RuntimeError("SUPERVISED_NATIVE_CHILD_NOT_STARTED")
        if not self._connection.poll(self._timeout):
            self._faulted = True
            self._reap(force=True)
            raise RuntimeError("SUPERVISED_NATIVE_RESPONSE_TIMEOUT_AMBIGUOUS")
        try:
            result = self._connection.recv()
        except (EOFError, BrokenPipeError, OSError) as exc:
            self._faulted = True
            self._reap(force=True)
            raise RuntimeError("SUPERVISED_NATIVE_CHILD_DIED_AMBIGUOUS") from exc
        if (not isinstance(result, dict)
                or set(result) != {
                    "schema", "seq", "nonce", "pid", "ok", "time_s", "payload",
                }
                or result["schema"] != _SCHEMA
                or result["seq"] != seq or result["nonce"] != self._nonce
                or result["pid"] != proc.pid
                or type(result["ok"]) is not bool):
            self._faulted = True
            self._reap(force=True)
            raise RuntimeError("SUPERVISED_NATIVE_RPC_RESPONSE_MISMATCH")
        self._current_time_s = float(result["time_s"])
        if not result["ok"]:
            self._faulted = True
            self._reap(force=True)
            raise RuntimeError(
                "SUPERVISED_NATIVE_EFFECT_OUTCOME_UNCERTAIN:" +
                str(result["payload"])
            )
        return result["payload"]

    def prepare(self) -> None:
        if self._process is not None or self._closed:
            raise RuntimeError("SUPERVISED_NATIVE_REPREPARE_FORBIDDEN")
        parent, child = self._ctx.Pipe(duplex=True)
        nonce = secrets.token_hex(32)
        proc = self._ctx.Process(
            target=_native_process_main,
            args=(child, self._spec.model_dump(mode="json"), nonce, os.getpid()),
            name="sat-native-owner-" + self._spec.session_id,
            daemon=False,
        )
        try:
            proc.start()
        except BaseException:
            parent.close()
            child.close()
            raise
        child.close()
        self._process = proc
        self._connection = parent
        self._nonce = nonce
        if sys.platform == "win32":
            # Job assignment precedes the parent's first prepare RPC,
            # which is the *only* operation constructing the Basilisk graph.
            # Parent death before assignment leaves a passive child waiting
            # for the request; no native graph exists yet.
            try:
                from .native_windows_job import WindowsKillOnCloseJob
                self._windows_job = WindowsKillOnCloseJob.attach(proc.pid)
            except BaseException as exc:
                self._faulted = True
                self._reap(force=True)
                raise RuntimeError("SUPERVISED_NATIVE_WINDOWS_JOB_REQUIRED") from exc
        ready_record = self._recv(seq=0)  # child+nonce/PID verified
        if (not isinstance(ready_record, dict)
                or ready_record.get("ready") is not True
                or ready_record.get("native_resume_supported") is not False
                or ready_record.get("automatic_tc_replay_allowed") is not False
                or not isinstance(ready_record.get("parent_death_fence"), dict)):
            self._faulted = True
            self._reap(force=True)
            raise RuntimeError("SUPERVISED_NATIVE_PARENT_FENCE_HANDSHAKE_INVALID")
        fence = ready_record["parent_death_fence"]
        if (fence.get("parent_pid") != os.getpid()
                or (sys.platform == "linux"
                    and (fence.get("supported") is not True
                         or fence.get("kind") != "LINUX_PR_SET_PDEATHSIG_SIGKILL"))):
            self._faulted = True
            self._reap(force=True)
            raise RuntimeError("SUPERVISED_NATIVE_PARENT_FENCE_UNVERIFIED")
        if sys.platform == "win32":
            if self._windows_job is None or not self._windows_job.attached:
                self._faulted = True
                self._reap(force=True)
                raise RuntimeError("SUPERVISED_NATIVE_WINDOWS_JOB_REQUIRED")
            self._parent_death_fence = {
                "supported": True,
                "kind": "WINDOWS_JOB_KILL_ON_CLOSE",
                "parent_pid": os.getpid(),
            }
        else:
            self._parent_death_fence = dict(fence)
        scheduling = ready_record.get("cpu_scheduling")
        if (not isinstance(scheduling, dict)
                or scheduling.get("schema_version") != "sat-native-cpu-scheduling.v1"
                or scheduling.get("pid") != proc.pid
                or scheduling.get("mode") not in {"SYSTEM_DEFAULT", "PERFORMANCE_CORES"}
                or type(scheduling.get("applied")) is not bool):
            self._faulted = True
            self._reap(force=True)
            raise RuntimeError("SUPERVISED_NATIVE_CPU_POLICY_HANDSHAKE_INVALID")
        self._cpu_scheduling = dict(scheduling)
        ready = self._rpc("prepare", None)
        if ready is not None:
            self._faulted = True
            self._reap(force=True)
            raise RuntimeError("SUPERVISED_NATIVE_PREPARE_PROTOCOL_ERROR")

    def _rpc(self, op: str, payload: Any) -> Any:
        if self._closed or self._faulted or self._connection is None:
            raise RuntimeError("SUPERVISED_NATIVE_SESSION_FENCED")
        if op not in _OPERATIONS:
            raise ValueError("SUPERVISED_NATIVE_UNKNOWN_OPERATION")
        self._seq += 1
        try:
            self._connection.send({
                "schema": _SCHEMA, "seq": self._seq,
                "nonce": self._nonce, "op": op, "payload": payload,
            })
        except (OSError, EOFError, BrokenPipeError) as exc:
            self._faulted = True
            self._reap(force=True)
            raise RuntimeError("SUPERVISED_NATIVE_SEND_FAILED_AMBIGUOUS") from exc
        return self._recv(seq=self._seq)

    def advance_to(self, stop_time_s: float) -> tuple[dict[str, Any], ...]:
        return tuple(self._rpc("advance_to", float(stop_time_s)))

    def apply_command(self, command: Telecommand) -> None:
        self._rpc("apply_command", command.model_dump(mode="json"))

    def read_delta(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._rpc("read_delta", None))

    def audit_native_checkpoint_coverage(self) -> dict[str, Any]:
        return self._rpc("checkpoint_audit", None)

    def finalize(self) -> dict[str, Any]:
        result = self._rpc("finalize", None)
        self._reap(force=False)
        if self._last_reap is None or self._last_reap["exitcode"] != 0:
            raise RuntimeError("SUPERVISED_NATIVE_FINALIZE_CHILD_EXIT_UNCLEAN")
        if isinstance(result, dict):
            result.setdefault("metadata", {})["native_cpu_scheduling"] = self.cpu_scheduling
        return result

    def abort(self) -> None:
        if self._process is None:
            self._closed = True
            return
        if not self._closed:
            try:
                if not self._faulted and self._process.is_alive():
                    self._rpc("abort", None)
            except BaseException:
                # No next-generation execution; terminate our exact held
                # child and reap rather than infer it exited from a PID.
                self._faulted = True
            finally:
                self._reap(force=True)

__all__ = ["SupervisedContinuousNativeRuntime"]
