"""Authenticated remote execution worker protocol and client.

The protocol transports immutable prepared Run Bundles from the control plane
to a worker and returns a sealed result archive.  Queue metadata remains in the
control-plane SQLite database; workers never mount or directly modify that
file.
"""
from __future__ import annotations

import json
import os
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from uuid import uuid4

from .run_bundle import request_cancel, verify_run_bundle

REMOTE_WORKER_PROTOCOL_VERSION = "remote-worker.v1"
DEFAULT_MAX_ARCHIVE_FILES = 20000
DEFAULT_MAX_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024
DEFAULT_MAX_COMPRESSION_RATIO = 250.0


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _safe_member_name(name: str) -> PurePosixPath:
    normalized = PurePosixPath(name)
    if normalized.is_absolute() or not normalized.parts:
        raise ValueError(f"unsafe archive member: {name}")
    if any(part in {"", ".", ".."} for part in normalized.parts):
        raise ValueError(f"unsafe archive member: {name}")
    if "\\" in name or ":" in normalized.parts[0]:
        raise ValueError(f"unsafe archive member: {name}")
    return normalized


def build_bundle_archive(bundle_root: str | Path, archive_path: str | Path) -> Path:
    root = Path(bundle_root).resolve()
    output = Path(archive_path).resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"symlink is not allowed in Run Bundle: {path}")
            if path.is_file():
                relative = path.relative_to(root).as_posix()
                _safe_member_name(relative)
                archive.write(path, relative)
    return output


def safe_extract_bundle_archive(
    archive_path: str | Path,
    destination: str | Path,
    *,
    max_files: int = DEFAULT_MAX_ARCHIVE_FILES,
    max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES,
    max_compression_ratio: float = DEFAULT_MAX_COMPRESSION_RATIO,
) -> Path:
    source = Path(archive_path).resolve()
    target = Path(destination).resolve()
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source, "r") as archive:
        infos = archive.infolist()
        if len(infos) > int(max_files):
            raise ValueError(f"archive file count exceeds limit: {len(infos)} > {max_files}")
        total = 0
        for info in infos:
            relative = _safe_member_name(info.filename)
            if info.is_dir():
                continue
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise ValueError(f"symlink archive member is not allowed: {info.filename}")
            total += int(info.file_size)
            if total > int(max_uncompressed_bytes):
                raise ValueError("archive uncompressed size exceeds limit")
            compressed = max(int(info.compress_size), 1)
            if info.file_size > 1024 * 1024 and float(info.file_size) / compressed > float(max_compression_ratio):
                raise ValueError(f"suspicious compression ratio for {info.filename}")
            output = (target / Path(*relative.parts)).resolve()
            if target not in output.parents:
                raise ValueError(f"archive member escapes destination: {info.filename}")
            output.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source_handle, output.open("wb") as target_handle:
                shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
    return target


def prepared_bundle_identity(bundle_root: str | Path) -> dict[str, Any]:
    root = Path(bundle_root).resolve()
    prepared = _json(root / "runtime" / "prepared_run.json")
    plan = _json(root / "input" / "execution_plan.json") or _json(root / "runtime" / "execution_plan.json")
    plan_hash = str(
        prepared.get("execution_plan_sha256")
        or prepared.get("plan_sha256")
        or plan.get("execution_plan_sha256")
        or ""
    )
    return {
        "run_id": str(prepared.get("run_id") or root.name),
        "execution_plan_sha256": plan_hash,
        "prepared": prepared,
    }


def validate_remote_result_bundle(
    bundle_root: str | Path,
    *,
    expected_run_id: str,
    expected_plan_sha256: str,
) -> dict[str, Any]:
    root = Path(bundle_root).resolve()
    identity = prepared_bundle_identity(root)
    if identity["run_id"] != expected_run_id:
        raise ValueError(f"result run_id mismatch: {identity['run_id']} != {expected_run_id}")
    actual_hash = str(identity.get("execution_plan_sha256") or "")
    if actual_hash and actual_hash != expected_plan_sha256:
        raise ValueError("result execution plan hash mismatch")
    if not (root / "SEALED.json").is_file():
        raise ValueError("remote result Run Bundle is not sealed")
    integrity = verify_run_bundle(root)
    if not integrity.get("ok"):
        raise ValueError(f"remote result Run Bundle integrity failed: {integrity.get('reason_code')}")
    record = _json(root / "run_record.json")
    state = str(record.get("status") or "").upper()
    if state not in {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}:
        raise ValueError(f"remote result has no terminal run state: {state or 'missing'}")
    validation = _json(root / "validation" / "validation_outcome.json")
    return {
        "run_id": expected_run_id,
        "state": state,
        "return_code": record.get("return_code", 0 if state == "SUCCEEDED" else None),
        "validation_result": validation.get("result") or record.get("validation_result"),
        "terminal_reason_code": record.get("terminal_reason_code"),
        "integrity": integrity,
    }


def install_remote_result_bundle(staged_root: str | Path, target_root: str | Path) -> Path:
    staged = Path(staged_root).resolve()
    target = Path(target_root).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = target.with_name(f".{target.name}.remote-backup-{uuid4().hex}")
    if target.exists():
        os.replace(target, backup)
    try:
        os.replace(staged, target)
    except Exception:
        if backup.exists() and not target.exists():
            os.replace(backup, target)
        raise
    if backup.exists():
        shutil.rmtree(backup)
    return target


@dataclass(frozen=True)
class RemoteWorkerConfig:
    control_plane_url: str
    worker_id: str
    token: str
    work_root: Path
    poll_interval_s: float = 1.0
    heartbeat_interval_s: float = 5.0
    lease_seconds: float = 30.0
    request_timeout_s: float = 60.0
    insecure_skip_tls_verify: bool = False
    capabilities: dict[str, Any] | None = None


class RemoteWorkerHTTPClient:
    def __init__(self, config: RemoteWorkerConfig) -> None:
        self.config = config
        self.base_url = config.control_plane_url.rstrip("/")
        self.config.work_root.mkdir(parents=True, exist_ok=True)
        if config.insecure_skip_tls_verify:
            self.ssl_context = ssl._create_unverified_context()  # noqa: S323 - explicit operator opt-in
        else:
            self.ssl_context = ssl.create_default_context()

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
        body: bytes | None = None,
        content_type: str = "application/json",
    ) -> tuple[bytes, Mapping[str, str]]:
        data = body
        if payload is not None:
            data = json.dumps(dict(payload), ensure_ascii=False).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.config.token}",
            "X-Sat-Sim-Protocol": REMOTE_WORKER_PROTOCOL_VERSION,
            "Content-Type": content_type,
            "Accept": "application/json, application/zip",
        }
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.config.request_timeout_s, context=self.ssl_context) as response:  # nosec B310 - operator-configured control plane
                return response.read(), dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"control plane HTTP {exc.code}: {detail}") from exc

    def _json_request(self, method: str, path: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        raw, _ = self._request(method, path, payload=payload)
        value = json.loads(raw.decode("utf-8")) if raw else {}
        if not isinstance(value, dict):
            raise RuntimeError("control plane returned a non-object JSON response")
        return value

    def register(self) -> dict[str, Any]:
        return self._json_request("POST", f"/remote-workers/{self.config.worker_id}/register", {
            "worker_host": os.uname().nodename if hasattr(os, "uname") else "remote-worker",
            "capabilities": self.config.capabilities or {
                "execution_type": "simulation",
                "basilisk": True,
                "transport": "https-bundle",
                "protocol_version": REMOTE_WORKER_PROTOCOL_VERSION,
            },
        })

    def claim(self) -> dict[str, Any] | None:
        payload = self._json_request("POST", f"/remote-workers/{self.config.worker_id}/claim", {
            "lease_seconds": self.config.lease_seconds,
        })
        return payload.get("job") if payload.get("claimed") else None

    def download_bundle(self, job: Mapping[str, Any], destination: Path) -> Path:
        raw, _ = self._request(
            "GET",
            f"/remote-workers/{self.config.worker_id}/jobs/{job['job_id']}/bundle",
            content_type="application/octet-stream",
        )
        destination.write_bytes(raw)
        return destination

    def mark_running(self, job_id: str, *, pid: int | None) -> dict[str, Any]:
        return self._json_request("POST", f"/remote-workers/{self.config.worker_id}/jobs/{job_id}/start", {
            "pid": pid,
            "lease_seconds": self.config.lease_seconds,
        })

    def heartbeat(self, job_id: str, *, pid: int | None) -> dict[str, Any]:
        return self._json_request("POST", f"/remote-workers/{self.config.worker_id}/jobs/{job_id}/heartbeat", {
            "pid": pid,
            "lease_seconds": self.config.lease_seconds,
        })

    def upload_result(self, job_id: str, archive_path: Path) -> dict[str, Any]:
        raw, _ = self._request(
            "POST",
            f"/remote-workers/{self.config.worker_id}/jobs/{job_id}/result",
            body=archive_path.read_bytes(),
            content_type="application/zip",
        )
        value = json.loads(raw.decode("utf-8")) if raw else {}
        if not isinstance(value, dict):
            raise RuntimeError("control plane returned a non-object result response")
        return value


class RemoteExecutionWorker:
    def __init__(self, config: RemoteWorkerConfig) -> None:
        self.config = config
        self.client = RemoteWorkerHTTPClient(config)
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def _execute_claimed_job(self, job: Mapping[str, Any]) -> dict[str, Any]:
        job_id = str(job["job_id"])
        run_id = str(job["run_id"])
        job_root = self.config.work_root / job_id
        if job_root.exists():
            shutil.rmtree(job_root)
        job_root.mkdir(parents=True)
        source_zip = job_root / "prepared.zip"
        bundle_root = job_root / "bundle"
        self.client.download_bundle(job, source_zip)
        safe_extract_bundle_archive(source_zip, bundle_root)
        identity = prepared_bundle_identity(bundle_root)
        if identity["run_id"] != run_id:
            raise RuntimeError("downloaded bundle run_id mismatch")
        actual_hash = identity.get("execution_plan_sha256")
        if actual_hash and actual_hash != job.get("execution_plan_sha256"):
            raise RuntimeError("downloaded bundle execution plan hash mismatch")
        command = [
            sys.executable,
            "-m",
            "sat_sim.cli",
            "execute-run",
            str(bundle_root),
            "--plan-sha256",
            str(job["execution_plan_sha256"]),
            "--max-attempts",
            str(job.get("max_attempts", 1)),
        ]
        if job.get("hard_timeout"):
            command.append("--hard-timeout")
        stdout_path = job_root / "worker.stdout.log"
        stderr_path = job_root / "worker.stderr.log"
        with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr, text=True)
            self.client.mark_running(job_id, pid=process.pid)
            last_heartbeat = 0.0
            while process.poll() is None:
                if self._stop.is_set():
                    request_cancel(bundle_root, reason="remote_worker_stopping")
                now = time.monotonic()
                if now - last_heartbeat >= self.config.heartbeat_interval_s:
                    heartbeat = self.client.heartbeat(job_id, pid=process.pid)
                    state = str((heartbeat.get("job") or {}).get("state") or "")
                    if state in {"CANCEL_REQUESTED", "CANCELLED"}:
                        request_cancel(bundle_root, reason="control_plane_cancel_requested")
                    if heartbeat.get("owns_lease") is False:
                        process.terminate()
                        raise RuntimeError("remote worker lost queue lease")
                    last_heartbeat = now
                self._stop.wait(min(self.config.poll_interval_s, 0.5))
            process.wait()
        result_zip = job_root / "result.zip"
        build_bundle_archive(bundle_root, result_zip)
        response = self.client.upload_result(job_id, result_zip)
        response["local_return_code"] = process.returncode
        response["worker_stdout"] = str(stdout_path)
        response["worker_stderr"] = str(stderr_path)
        return response

    def run_forever(self, *, max_jobs: int | None = None, idle_timeout_s: float | None = None) -> int:
        self.client.register()
        completed = 0
        idle_started = time.monotonic()
        while not self._stop.is_set():
            job = self.client.claim()
            if job is None:
                if idle_timeout_s is not None and time.monotonic() - idle_started >= idle_timeout_s:
                    break
                self._stop.wait(self.config.poll_interval_s)
                continue
            idle_started = time.monotonic()
            self._execute_claimed_job(job)
            completed += 1
            if max_jobs is not None and completed >= max_jobs:
                break
        return completed


__all__ = [
    "DEFAULT_MAX_ARCHIVE_FILES",
    "DEFAULT_MAX_COMPRESSION_RATIO",
    "DEFAULT_MAX_UNCOMPRESSED_BYTES",
    "REMOTE_WORKER_PROTOCOL_VERSION",
    "RemoteExecutionWorker",
    "RemoteWorkerConfig",
    "RemoteWorkerHTTPClient",
    "build_bundle_archive",
    "install_remote_result_bundle",
    "prepared_bundle_identity",
    "safe_extract_bundle_archive",
    "validate_remote_result_bundle",
]
