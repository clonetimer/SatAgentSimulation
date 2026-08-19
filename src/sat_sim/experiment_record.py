"""Reproducible experiment records for AstroGraph-facing simulation runs."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

EXPERIMENT_RECORD_SCHEMA_VERSION = "sat-sim.experiment-record.v1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_commit(root: Path) -> str | None:
    explicit = os.environ.get("SAT_SIM_GIT_COMMIT") or os.environ.get("GIT_COMMIT")
    if explicit:
        return explicit.strip() or None
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, capture_output=True, check=False, timeout=2
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and value else None


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def build_experiment_record(
    dataset_root: str | Path,
    *,
    contract: Mapping[str, Any],
    manifest: Mapping[str, Any],
    task_spec: Mapping[str, Any],
    runtime_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a provenance record without changing the simulation artifacts."""

    root = Path(dataset_root)
    metadata = _mapping(task_spec.get("metadata"))
    simulation = _mapping(task_spec.get("simulation"))
    solver = _mapping(simulation.get("solver"))
    platform_block = _mapping(manifest.get("platform"))
    task_block = _mapping(manifest.get("task"))
    ground_truth = _mapping(contract.get("ground_truth"))
    telemetry = _mapping(contract.get("telemetry"))
    files = _mapping(manifest.get("files"))
    repo_root = root
    for candidate in (root, *root.parents):
        if (candidate / "pyproject.toml").is_file():
            repo_root = candidate
            break

    event_definitions = {
        "faults": list(_mapping(task_spec.get("modifiers")).get("faults") or []),
        "degradations": list(_mapping(task_spec.get("modifiers")).get("degradations") or []),
        "constraints": list(_mapping(task_spec.get("modifiers")).get("constraints") or []),
    }
    artifact_paths = {
        "task_spec": root / "task_spec.json",
        "compiled_task": root / "compiled_task.json",
        "manifest": root / "manifest.json",
        "summary": root / str(files.get("summary") or "summary.json"),
        "source_trace": root / str(telemetry.get("source_trace_file") or files.get("trace") or "trace.csv"),
        "astrograph_telemetry": root / str(telemetry.get("file") or "astrograph/telemetry.csv"),
        "simulation_report": root / str(contract.get("simulation_report_file") or "astrograph/simulation_report.json"),
    }
    artifact_hashes = {
        name: {"file": str(path.relative_to(root).as_posix()), "sha256": _sha256(path)}
        for name, path in artifact_paths.items()
    }

    git_commit = _git_commit(repo_root)
    record = {
        "schema_version": EXPERIMENT_RECORD_SCHEMA_VERSION,
        "experiment_id": str(metadata.get("experiment_id") or f"experiment:{contract.get('dataset_id') or task_spec.get('task_id')}"),
        "generated_at_utc": _now_iso(),
        "lineage": {
            "campaign_id": metadata.get("campaign_id"),
            "pair_id": ground_truth.get("pair_id"),
            "case_role": ground_truth.get("case_role"),
            "simulation_run_id": contract.get("dataset_id"),
            "dataset_id": contract.get("dataset_id"),
            "independent_run_id": contract.get("independent_run_id"),
            "astrograph_case_id": metadata.get("astrograph_case_id"),
            "experiment_template_id": metadata.get("experiment_template_id"),
        },
        "engine": {
            "name": "Basilisk" if str(simulation.get("backend") or "").lower() == "basilisk" else str(simulation.get("backend") or "unknown"),
            "backend": simulation.get("backend"),
            "capability_id": task_spec.get("capability_id") or task_block.get("capability_id"),
            "bsk_version": platform_block.get("bsk_version"),
            "platform_version": platform_block.get("platform_version"),
            "runtime_fidelity": _mapping(_mapping(contract.get("simulation")).get("fidelity")),
        },
        "execution": {
            "status": manifest.get("status"),
            "seed": simulation.get("seed"),
            "duration_s": simulation.get("duration_s"),
            "sample_s": simulation.get("sample_s"),
            "solver_step_s": solver.get("step_s"),
            "task_spec_sha256": task_block.get("spec_hash"),
            "randomization_policy": "paired fault/nominal cases share seed and initial parameters",
        },
        "spacecraft_and_environment": {
            "target": _mapping(task_spec.get("target")),
            "parameters": _mapping(task_spec.get("parameters")),
            "simulation_extra": {k: v for k, v in simulation.items() if k not in {"seed", "duration_s", "sample_s", "solver"}},
        },
        "fault_injection": {
            "ground_truth": ground_truth,
            "event_definitions": event_definitions,
        },
        "runtime_summary": dict(runtime_summary or {}),
        "source_control": {
            "git_commit": git_commit,
            "git_commit_available": git_commit is not None,
            "source_package_release": platform_block.get("platform_version"),
        },
        "host": {
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "artifacts": artifact_hashes,
        "claim_boundary": {
            "flight_validated": False,
            "formal_training_eligibility_is_not_physics_validation": True,
            "expert_review_required": True,
        },
    }
    return record


def write_experiment_record(
    dataset_root: str | Path,
    *,
    contract: Mapping[str, Any],
    manifest: Mapping[str, Any],
    task_spec: Mapping[str, Any],
    runtime_summary: Mapping[str, Any] | None = None,
) -> tuple[Path, dict[str, Any]]:
    root = Path(dataset_root)
    record = build_experiment_record(
        root, contract=contract, manifest=manifest, task_spec=task_spec, runtime_summary=runtime_summary
    )
    path = root / "astrograph" / "experiment_record.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path, record


__all__ = ["EXPERIMENT_RECORD_SCHEMA_VERSION", "build_experiment_record", "write_experiment_record"]
