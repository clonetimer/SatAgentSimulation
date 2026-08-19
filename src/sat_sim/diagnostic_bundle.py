"""Create a redacted support bundle for local deployment diagnostics."""
from __future__ import annotations

import json
import os
import platform
import sys
import zipfile
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Mapping

from utils.runtime_diagnostics import runtime_diagnostics_report

DIAGNOSTIC_SCHEMA_VERSION = "release.diagnostic-bundle.v1"
_SECRET_MARKERS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIAL")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _versions() -> dict[str, Any]:
    payload: dict[str, Any] = {"python": platform.python_version()}
    for name in ("satellite-simulation-platform", "bsk", "fastapi", "starlette", "uvicorn", "httpx", "pydantic", "numpy"):
        try:
            payload[name] = version(name)
        except PackageNotFoundError:
            payload[name] = None
    try:
        import Basilisk
        payload["Basilisk"] = getattr(Basilisk, "__version__", payload.get("bsk"))
    except Exception:
        payload["Basilisk"] = None
    return payload


def _safe_env() -> dict[str, Any]:
    names = sorted(name for name in os.environ if name.startswith("SAT_SIM_") or name.endswith("_API_KEY"))
    return {
        name: {
            "configured": bool(os.environ.get(name)),
            "value_exposed": False,
            "sensitive": any(marker in name.upper() for marker in _SECRET_MARKERS),
        }
        for name in names
    }


def _write_json(archive: zipfile.ZipFile, name: str, payload: Any) -> None:
    archive.writestr(name, json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n")


def create_diagnostic_bundle(
    output_path: str | Path,
    *,
    release: Mapping[str, Any],
    health: Mapping[str, Any],
    provider_catalog: Mapping[str, Any],
    task_center_health: Mapping[str, Any],
    experiment_health: Mapping[str, Any],
    queue_health: Mapping[str, Any],
    log_text: str = "",
    audit_log_path: str | Path | None = None,
    run_root: str | Path | None = None,
) -> Path:
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        _write_json(archive, "manifest.json", {
            "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
            "created_at": _now(),
            "python_executable_exposed": False,
            "secret_values_exposed": False,
        })
        _write_json(archive, "system/platform.json", {
            "platform": platform.platform(), "system": platform.system(), "release": platform.release(),
            "machine": platform.machine(), "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(), "executable_name": Path(sys.executable).name,
        })
        _write_json(archive, "system/dependency_versions.json", _versions())
        _write_json(archive, "system/environment_presence.json", _safe_env())
        _write_json(archive, "control_plane/release.json", dict(release))
        _write_json(archive, "control_plane/health.json", dict(health))
        _write_json(archive, "control_plane/queue_health.json", dict(queue_health))
        _write_json(archive, "control_plane/task_center_health.json", dict(task_center_health))
        _write_json(archive, "control_plane/experiment_health.json", dict(experiment_health))
        _write_json(archive, "control_plane/provider_catalog_redacted.json", dict(provider_catalog))
        _write_json(archive, "control_plane/runtime_diagnostics.json", runtime_diagnostics_report(clear=False))
        if log_text:
            archive.writestr("logs/control_plane_tail.jsonl", log_text)
        audit = Path(audit_log_path).resolve() if audit_log_path else None
        if audit and audit.is_file():
            lines = audit.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
            archive.writestr("logs/api_audit_tail.jsonl", "\n".join(lines) + ("\n" if lines else ""))
        root = Path(run_root).resolve() if run_root else None
        if root and root.is_dir():
            allowed = [
                "run_record.json", "SEALED.json", "bundle_manifest.json",
                "input/task_spec.json", "input/resolved_spec.json", "input/execution_plan.json",
                "runtime/environment.json", "runtime/dependency_versions.json", "runtime/execution_trace.json",
                "runtime/runtime_diagnostics.json", "results/summary.json", "results/metrics.json",
                "results/events.json", "results/assertions.json", "validation/validation_outcome.json",
                "validation/claim_report.json",
            ]
            for relative in allowed:
                path = root / relative
                if path.is_file():
                    archive.write(path, f"run/{relative}")
    return output


__all__ = ["DIAGNOSTIC_SCHEMA_VERSION", "create_diagnostic_bundle"]
