"""Authenticated local/remote FastAPI control plane and Web simulation workbench.

The API exposes constrained TaskSpec, planning, execution and evidence operations.
It never exposes arbitrary Python or raw Basilisk object construction. The production API adds
Token/RBAC controls, audit evidence, and a sealed remote-Worker bundle protocol.
"""
from __future__ import annotations

import copy
import csv
import json
import os
import sqlite3
from contextlib import asynccontextmanager
import shutil
import tempfile
import zipfile
from time import monotonic
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .agent_guards import agent_tool_policy_payload, evaluate_agent_guards
from .capability_registry import capability_summary_payload
from .model_governance import capability_governance_matrix, validate_capability_governance
from .product_closure import product_capability_closure
from .execution_planner import plan_task_spec
from .durable_queue import DurableExecutionQueue, TERMINAL_STATES
from .execution_worker import ExecutionQueueWorker
from .model_providers import ModelProviderProfile, ModelProviderRegistry
from .task_center import TaskCenterStore
from .experiment_manager import ExperimentStore
from .experience import ExperienceScope, ExperienceStore
from .experience_lessons import ExperienceLessonService
from .experiments.dispersion_registry import dispersion_parameter_options

from .workbench_integration import (
    agent_tool_surface,
    experiment_design_options,
    list_workbench_fault_models,
    preview_experiment_plan,
)
from .diagnostic_bundle import create_diagnostic_bundle
from .diagnostic_mapping_bundle import (
    build_astrograph_diagnostic_mapping_bundle,
    get_astrograph_diagnostic_mapping,
)
from .diagnostic_pipeline_registry import list_diagnostic_pipelines
from .diagnostic_pipeline_runtime import DiagnosticPipelineExecutor
from .observability import WorkbenchLogStore
from .scenario_templates import list_scenario_templates, instantiate_scenario_template
from .remote_worker import (
    REMOTE_WORKER_PROTOCOL_VERSION,
    build_bundle_archive,
    install_remote_result_bundle,
    safe_extract_bundle_archive,
    validate_remote_result_bundle,
)
from .security import AuthManager, AuditLogger, required_roles_for_request
from .form_schema import capability_form_catalog, capability_form_schema, output_label, task_spec_to_form_data
from .release_closure import release_manifest, run_environment_doctor
from .run_bundle import execute_prepared_run, prepare_run, request_cancel, verify_run_bundle
from .task_models import CanonicalTaskSpec, canonicalize_task_spec
from .task_validator import validate_task_spec
from .unified_agent import UnifiedAgentRequest, run_unified_agent

API_VERSION = "workbench-api.v1"
WEB_WORKBENCH_VERSION = "sidebar-observability-workbench.v1"
_MAX_TELEMETRY_ROWS = 5000


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskCreateBody(_Strict):
    input_kind: Literal["natural_language", "form", "task_spec", "patch"]
    request_text: str = ""
    form_data: dict[str, Any] | None = None
    task_spec: dict[str, Any] | None = None
    base_task_spec: dict[str, Any] | None = None
    patch: dict[str, Any] | None = None
    backend: str = "auto"
    local_backend: str = "template"
    remote_backend: str | None = None
    model_name: str | None = None
    model_base_url: str | None = None
    model_api_key_env: str = "OPENAI_API_KEY"
    model_timeout_s: float = Field(default=60.0, gt=0.0, le=600.0)
    model_temperature: float | None = None
    model_max_output_tokens: int | None = Field(default=None, ge=1, le=65536)
    model_structured_output: str = "json_object"
    provider_id: str | None = None
    routing_mode: Literal["legacy", "auto", "local", "remote"] = "auto"
    compile_if_valid: bool = True
    experience_reuse_enabled: bool = False
    experience_tenant_id: str | None = Field(default=None, min_length=1, max_length=128)
    experience_project_id: str | None = Field(default=None, min_length=1, max_length=128)


class TaskBody(_Strict):
    task_spec: dict[str, Any]


class RunCreateBody(_Strict):
    task_spec: dict[str, Any]
    run_id: str | None = None
    supersedes_run_id: str | None = None
    execute: bool = False
    execution_mode: Literal["sync", "async"] = "sync"
    max_attempts: int = Field(default=2, ge=1, le=2)
    hard_timeout: bool = True


class RunExecuteBody(_Strict):
    execution_plan_sha256: str = Field(min_length=64, max_length=64)
    max_attempts: int = Field(default=2, ge=1, le=2)
    hard_timeout: bool = True


class FormProjectionBody(_Strict):
    task_spec: dict[str, Any]


class CancelBody(_Strict):
    reason: str = "user_requested"


class ProviderRouteBody(_Strict):
    request_text: str
    input_kind: Literal["natural_language", "form", "task_spec", "patch"] = "natural_language"
    routing_mode: Literal["auto", "local", "remote"] = "auto"
    provider_id: str | None = None




class ProviderProbeBody(_Strict):
    timeout_s: float = Field(default=180.0, gt=0.0, le=900.0)
    include_taskspec_probe: bool = True


class LocalModelDiscoveryBody(_Strict):
    service_type: Literal["ollama", "lmstudio", "vllm", "openai_compatible"]
    provider_id: str | None = None
    base_url: str | None = None
    api_key: str | None = Field(default=None, max_length=4096)
    timeout_s: float = Field(default=5.0, gt=0.0, le=30.0)


class LocalModelServiceBody(_Strict):
    service_type: Literal["ollama", "lmstudio", "vllm", "openai_compatible"]
    provider_id: str | None = None
    label: str | None = None
    base_url: str | None = None
    model: str = Field(min_length=1, max_length=300)
    api_key: str | None = Field(default=None, max_length=4096)
    enabled: bool = True
    timeout_s: float | None = Field(default=None, gt=0.0, le=900.0)
    max_output_tokens: int | None = Field(default=None, ge=1, le=65536)
    structured_output: str | None = None


class TaskCenterSaveBody(_Strict):
    task_spec: dict[str, Any]
    task_id: str | None = None
    name: str | None = None
    source: str = "workbench"
    status: str = "DRAFT"
    notes: str = ""
    change_summary: str = ""
    force_new_version: bool = False


class TaskCenterCloneBody(_Strict):
    name: str | None = Field(default=None, max_length=300)


class TaskCenterVersionActionBody(_Strict):
    change_summary: str = ""


class TaskCenterRunBody(_Strict):
    max_attempts: int = Field(default=2, ge=1, le=2)
    hard_timeout: bool = True



class ScenarioTemplateInstantiateBody(_Strict):
    task_id: str | None = Field(default=None, max_length=200)
    name: str | None = Field(default=None, max_length=300)


class ExperimentCreateBody(_Strict):
    name: str = Field(min_length=1, max_length=300)
    base_task_spec: dict[str, Any]
    sweep: dict[str, list[Any]] = Field(default_factory=dict)
    assertions: list[dict[str, Any]] = Field(default_factory=list)
    experiment_type: str = Field(default="sweep", max_length=40)
    sampling_plan: dict[str, Any] = Field(default_factory=dict)


class ExperimentOptionsBody(_Strict):
    base_task_spec: dict[str, Any]


class ExperimentPlanBody(_Strict):
    base_task_spec: dict[str, Any]
    sweep: dict[str, list[Any]] = Field(default_factory=dict)
    assertions: list[dict[str, Any]] = Field(default_factory=list)
    experiment_type: str = Field(default="sweep", max_length=40)
    sampling_plan: dict[str, Any] = Field(default_factory=dict)
    preview_limit: int = Field(default=8, ge=0, le=64)


class ExperimentLaunchBody(_Strict):
    max_attempts: int = Field(default=2, ge=1, le=2)
    hard_timeout: bool = True


class RunComparisonBody(_Strict):
    run_ids: list[str] = Field(min_length=2, max_length=32)
    metrics: list[str] = Field(default_factory=list, max_length=64)


class ExperienceCaptureBody(_Strict):
    tenant_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=300)
    request_text: str = Field(min_length=1, max_length=100_000)
    model_identity: dict[str, Any] = Field(default_factory=dict)
    feedback: dict[str, Any] = Field(default_factory=dict)
    environment_tags: list[str] = Field(default_factory=list, max_length=64)


class ExperienceRevokeBody(_Strict):
    tenant_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=2_000)


class ExperienceLessonCompileBody(_Strict):
    tenant_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    experience_ids: list[str] = Field(min_length=1, max_length=100)


class ExperienceLessonEvaluateBody(_Strict):
    tenant_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    held_out_case_ids: list[str] = Field(min_length=1, max_length=10_000)
    baseline_metrics: dict[str, float]
    candidate_metrics: dict[str, float]
    evaluation_evidence: dict[str, Any]


class ExperienceLessonReviewBody(_Strict):
    tenant_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    decision: Literal["APPROVE", "REJECT"]
    reason: str = Field(min_length=1, max_length=2_000)


class ExperienceLessonScopeBody(_Strict):
    tenant_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)


class ExperienceLessonReuseBody(ExperienceLessonScopeBody):
    enabled: bool


class ExperienceLessonRollbackBody(ExperienceLessonScopeBody):
    snapshot_id: str = Field(min_length=1, max_length=100)


class DiagnosticPipelineExecuteBody(_Strict):
    fault_case_id: str = Field(min_length=1, max_length=500)
    nominal_case_id: str = Field(min_length=1, max_length=500)
    external_stage_outputs: dict[str, Any] = Field(default_factory=dict)
    persist: bool = True

class RemoteWorkerRegisterBody(_Strict):
    worker_host: str = Field(min_length=1, max_length=255)
    capabilities: dict[str, Any] = Field(default_factory=dict)


class RemoteWorkerClaimBody(_Strict):
    lease_seconds: float = Field(default=30.0, ge=5.0, le=300.0)


class RemoteWorkerStateBody(_Strict):
    pid: int | None = Field(default=None, ge=1)
    lease_seconds: float = Field(default=30.0, ge=5.0, le=300.0)


def _json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _safe_bundle(runs_root: Path, run_id: str) -> Path:
    root = runs_root.resolve()
    candidate = (root / run_id).resolve()
    if candidate.parent != root:
        raise ValueError("invalid run_id")
    return candidate


def _safe_diagnostic_case(cases_root: Path, case_id: str) -> Path:
    root = cases_root.resolve()
    candidate = (root / case_id).resolve()
    if candidate == root or root not in candidate.parents:
        raise ValueError("invalid diagnostic case id")
    if not candidate.is_dir():
        raise FileNotFoundError(case_id)
    return candidate


def _safe_artifact(bundle_root: Path, artifact_path: str) -> Path:
    candidate = (bundle_root / artifact_path).resolve()
    root = bundle_root.resolve()
    if candidate == root or root not in candidate.parents:
        raise ValueError("invalid artifact path")
    if not candidate.is_file():
        raise FileNotFoundError(artifact_path)
    return candidate


def _artifact_inventory(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    return [
        {
            "path": str(path.relative_to(root)).replace(os.sep, "/"),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def _dataset_inventory(dataset_root: Path) -> list[dict[str, Any]]:
    """Return regular files that can safely be exported from a run dataset."""

    root = dataset_root.resolve()
    if not root.is_dir():
        return []
    inventory: list[dict[str, Any]] = []
    for path in sorted(dataset_root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        resolved = path.resolve()
        if root not in resolved.parents:
            continue
        inventory.append({
            "path": resolved.relative_to(root).as_posix(),
            "size_bytes": resolved.stat().st_size,
        })
    return inventory


def _run_payload(root: Path) -> dict[str, Any]:
    return {
        "run_id": root.name,
        "exists": root.exists(),
        "task_spec": _json(root / "input" / "task_spec.json"),
        "prepared_run": _json(root / "runtime" / "prepared_run.json"),
        "run_record": _json(root / "run_record.json"),
        "validation_outcome": _json(root / "validation" / "validation_outcome.json"),
        "claim_report": _json(root / "validation" / "claim_report.json"),
        "summary": _json(root / "results" / "summary.json"),
        "metrics": _json(root / "results" / "metrics.json"),
        "events": _json(root / "results" / "events.json"),
        "assertions": _json(root / "results" / "assertions.json"),
        "plot_manifest": _json(root / "results" / "plot_manifest.json"),
        "sealed": (root / "SEALED.json").exists(),
        "integrity": verify_run_bundle(root) if (root / "bundle_manifest.json").exists() else None,
    }


def _metric_presentation_payload(root: Path, payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    task_spec = _json(root / "input" / "task_spec.json") or {}
    outputs = task_spec.get("outputs") if isinstance(task_spec.get("outputs"), dict) else {}
    requested_qoi = [str(item) for item in (outputs.get("qoi") or [])]
    capability_id = str((task_spec.get("model") or {}).get("capability_id") or "")
    metadata: dict[str, Any] = {}
    if capability_id:
        try:
            form_schema = capability_form_schema(capability_id)
            output_catalog = [
                *form_schema.get("outputs", {}).get("summary_options", []),
                *form_schema.get("outputs", {}).get("trace_options", []),
            ]
            for item in output_catalog:
                if not isinstance(item, dict) or not item.get("name"):
                    continue
                metadata[str(item["name"])] = {
                    "label_zh": str(item.get("label") or output_label(str(item["name"]))),
                    "unit": item.get("unit"),
                    "description_zh": str(item.get("description") or "该指标由当前仿真能力计算。"),
                    "technical_name": str(item["name"]),
                }
        except Exception:
            pass
    metric_values = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else payload
    for key in metric_values:
        metadata.setdefault(str(key), {
            "label_zh": output_label(str(key)),
            "unit": None,
            "description_zh": "该指标来自本次运行结果；技术字段用于日志、接口与数据导出。",
            "technical_name": str(key),
        })
    return metadata, requested_qoi


def _public_queue_job(payload: dict[str, Any]) -> dict[str, Any]:
    public = dict(payload)
    public.pop("bundle_root", None)
    public["bundle_ref"] = public.get("run_id")
    public["bundle_root_exposed"] = False
    return public


def _assert_remote_worker_binding(request: Any, worker_id: str) -> None:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise PermissionError("missing authenticated principal")
    if "admin" in principal.roles:
        return
    if "worker" not in principal.roles or principal.worker_id != worker_id:
        raise PermissionError("worker token is not bound to this worker_id")


def _run_list_payload(run_root: Path, *, limit: int, offset: int, status: str | None) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    if run_root.exists():
        for root in run_root.iterdir():
            if not root.is_dir():
                continue
            record = _json(root / "run_record.json") or {}
            prepared = _json(root / "runtime" / "prepared_run.json") or {}
            validation = _json(root / "validation" / "validation_outcome.json") or {}
            row_status = str(record.get("status") or "PREPARED")
            if status and row_status.upper() != status.upper():
                continue
            modified = max(
                (path.stat().st_mtime for path in (root / "run_record.json", root / "runtime" / "prepared_run.json") if path.exists()),
                default=root.stat().st_mtime,
            )
            task_spec = _json(root / "input" / "task_spec.json") or {}
            task_info = task_spec.get("task") if isinstance(task_spec.get("task"), dict) else {}
            rows.append({
                "run_id": root.name,
                "task_id": record.get("task_id") or prepared.get("task_id"),
                "task_name": task_info.get("name") or record.get("task_id") or prepared.get("task_id"),
                "status": row_status,
                "validation_result": validation.get("result") or record.get("validation_result"),
                "primary_capability_id": prepared.get("primary_capability_id"),
                "updated_at": record.get("updated_at") or prepared.get("prepared_at"),
                "sealed": (root / "SEALED.json").exists(),
                "modified_epoch": modified,
            })
    rows.sort(key=lambda item: float(item["modified_epoch"]), reverse=True)
    total = len(rows)
    selected = rows[offset:offset + limit]
    for item in selected:
        item.pop("modified_epoch", None)
    return {"count": len(selected), "total": total, "offset": offset, "limit": limit, "runs": selected}


def _read_telemetry(root: Path, *, offset: int, limit: int) -> dict[str, Any]:
    source = root / "results" / "telemetry.jsonl"
    if not source.exists():
        return {"source": None, "offset": offset, "limit": limit, "count": 0, "total": 0, "rows": []}
    rows: list[dict[str, Any]] = []
    total = 0
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            if offset <= total < offset + limit:
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
            total += 1
    return {
        "source": "results/telemetry.jsonl",
        "offset": offset,
        "limit": limit,
        "count": len(rows),
        "total": total,
        "truncated": offset + len(rows) < total,
        "rows": rows,
    }


def _read_dataset_stream(
    root: Path,
    stream_id: str,
    *,
    offset: int,
    limit: int,
    start_s: float | None = None,
    end_s: float | None = None,
) -> dict[str, Any]:
    manifest_path = root / "results" / "dataset" / "telemetry" / "multi_rate_manifest.json"
    manifest = _json(manifest_path)
    if not isinstance(manifest, dict):
        raise FileNotFoundError("multi-rate telemetry manifest not found")
    selected = None
    for item in manifest.get("streams", []):
        if isinstance(item, dict) and str(item.get("stream_id")) == stream_id:
            selected = item
            break
    if selected is None:
        raise KeyError(stream_id)
    source = root / "results" / "dataset" / str(selected["file"])
    all_rows: list[dict[str, Any]] = []
    if source.suffix.lower() == ".jsonl":
        with source.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                value = json.loads(line)
                if isinstance(value, dict):
                    all_rows.append(value)
    else:
        import csv
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            all_rows.extend(dict(value) for value in csv.DictReader(handle))

    def in_window(row: Mapping[str, Any]) -> bool:
        if start_s is None and end_s is None:
            return True
        try:
            value = float(row.get("time_s"))
        except (TypeError, ValueError):
            return False
        if start_s is not None and value < start_s - 1e-9:
            return False
        if end_s is not None and value > end_s + 1e-9:
            return False
        return True

    filtered = [row for row in all_rows if in_window(row)]
    total = len(filtered)
    rows = filtered[offset:offset + limit]
    return {
        "stream": selected,
        "source": str(source.relative_to(root)),
        "offset": offset,
        "limit": limit,
        "count": len(rows),
        "total": total,
        "truncated": offset + len(rows) < total,
        "start_s": start_s,
        "end_s": end_s,
        "rows": rows,
    }


def create_app(
    *,
    runs_root: str | Path | None = None,
    artifacts_root: str | Path | None = None,
    queue_database: str | Path | None = None,
    embedded_worker: bool | None = None,
    auth_mode: str | None = None,
    auth_config: str | Path | None = None,
    audit_log_path: str | Path | None = None,
    trusted_hosts: list[str] | None = None,
    max_request_bytes: int | None = None,
    remote_upload_max_bytes: int | None = None,
    diagnostic_cases_root: str | Path | None = None,
):
    try:
        from fastapi import FastAPI, HTTPException, Query, Request
        from fastapi.responses import FileResponse, JSONResponse
        from starlette.background import BackgroundTask
        from starlette.middleware.trustedhost import TrustedHostMiddleware
        from fastapi.staticfiles import StaticFiles
        globals()["Request"] = Request
    except ImportError as exc:  # pragma: no cover - optional dependency boundary
        raise RuntimeError("FastAPI is not installed; install satellite-simulation-platform[api]") from exc

    run_root = Path(runs_root or os.getenv("SAT_SIM_RUNS_ROOT", "runs")).resolve()
    artifact_root = Path(artifacts_root or os.getenv("SAT_SIM_API_ARTIFACTS_ROOT", ".sat_sim_api")).resolve()
    diagnostic_root = Path(diagnostic_cases_root or os.getenv("SAT_SIM_DIAGNOSTIC_CASES_ROOT", run_root)).resolve()
    web_root = Path(__file__).with_name("web").resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    artifact_root.mkdir(parents=True, exist_ok=True)
    diagnostic_root.mkdir(parents=True, exist_ok=True)

    queue_path = Path(
        queue_database
        or os.getenv("SAT_SIM_QUEUE_DATABASE")
        or artifact_root / "execution_queue.sqlite3"
    ).resolve()
    execution_queue = DurableExecutionQueue(queue_path)
    task_center_path = Path(os.getenv("SAT_SIM_TASK_CENTER_DATABASE") or artifact_root / "task_center.sqlite3").resolve()
    task_center = TaskCenterStore(task_center_path)
    experiment_path = Path(os.getenv("SAT_SIM_EXPERIMENT_DATABASE") or artifact_root / "experiments.sqlite3").resolve()
    experiment_store = ExperimentStore(experiment_path)
    experience_store = ExperienceStore(artifact_root / "experiences")
    experience_lessons = ExperienceLessonService(experience_store)
    provider_config_path = Path(os.getenv("SAT_SIM_MODEL_PROVIDERS_FILE") or artifact_root / "model_providers.json").resolve()

    workbench_log = WorkbenchLogStore(os.getenv("SAT_SIM_WORKBENCH_LOG") or artifact_root / "logs" / "workbench.jsonl")

    def provider_registry() -> ModelProviderRegistry:
        return ModelProviderRegistry(config_path=provider_config_path if provider_config_path.exists() else None)

    if embedded_worker is None:
        embedded_worker = os.getenv("SAT_SIM_EMBEDDED_WORKER", "1").strip().lower() not in {"0", "false", "no", "off"}
    queue_worker = ExecutionQueueWorker(execution_queue) if embedded_worker else None
    interactive_manager = None

    @asynccontextmanager
    async def lifespan(_app):
        """Start and stop the embedded worker using the ASGI lifespan protocol.

        FastAPI recommends lifespan handlers instead of the deprecated startup/
        shutdown event registration API.  Keeping the worker lifecycle here also
        makes startup portable across supported FastAPI/Starlette versions.
        """
        if queue_worker is not None:
            queue_worker.start()
        try:
            yield
        finally:
            if interactive_manager is not None:
                interactive_manager.shutdown()
            if queue_worker is not None:
                queue_worker.stop()

    app = FastAPI(
        title="Satellite Simulation Agent API",
        version=API_VERSION,
        description="Local-first deterministic TaskSpec, planning, execution, evidence and Web workbench API.",
        lifespan=lifespan,
    )
    app.state.execution_queue = execution_queue
    app.state.queue_worker = queue_worker
    app.state.task_center = task_center
    app.state.experiment_store = experiment_store
    app.state.experience_store = experience_store
    app.state.experience_lessons = experience_lessons
    app.state.provider_config_path = provider_config_path
    app.state.workbench_log = workbench_log
    resolved_trusted_hosts = trusted_hosts
    if resolved_trusted_hosts is None:
        raw_hosts = os.getenv("SAT_SIM_TRUSTED_HOSTS", "*")
        resolved_trusted_hosts = [item.strip() for item in raw_hosts.split(",") if item.strip()]
    if resolved_trusted_hosts and resolved_trusted_hosts != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=resolved_trusted_hosts)
    if web_root.is_dir():
        app.mount("/assets", StaticFiles(directory=web_root), name="workbench-assets")

    auth_manager = AuthManager.from_env(mode=auth_mode, config_path=auth_config)
    audit_logger = AuditLogger(audit_log_path or os.getenv("SAT_SIM_AUDIT_LOG") or artifact_root / "audit" / "api_audit.jsonl")
    from .interactive import interactive_enabled

    if interactive_enabled():
        from .interactive.api import create_interactive_router
        from .interactive.manager import InteractiveSessionManager

        interactive_manager = InteractiveSessionManager(
            enabled=True,
            max_sessions=int(os.getenv("SAT_SIM_INTERACTIVE_MAX_SESSIONS", "4")),
            workspace_root=artifact_root / "interactive_sessions",
        )
        app.state.interactive_manager = interactive_manager
        app.include_router(create_interactive_router(interactive_manager, auth_manager, audit_logger))
    request_limit = int(max_request_bytes or os.getenv("SAT_SIM_MAX_REQUEST_BYTES", 16 * 1024 * 1024))
    remote_upload_limit = int(remote_upload_max_bytes or os.getenv("SAT_SIM_REMOTE_UPLOAD_MAX_BYTES", 1024 * 1024 * 1024))
    public_paths = {"/", "/workspace", "/health", "/auth/status", "/favicon.ico"}

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        started_clock = monotonic()
        path = request.url.path
        principal = auth_manager.authenticate_header(request.headers.get("Authorization"))
        request.state.principal = principal
        request.state.request_id = request.headers.get("X-Request-ID") or uuid4().hex
        is_public = path in public_paths or path.startswith("/assets/")
        required_roles = frozenset() if is_public else required_roles_for_request(request.method, path)
        denied_reason = None
        if auth_manager.enabled and not is_public:
            if principal is None:
                denied_reason = "AUTHENTICATION_REQUIRED"
            elif not principal.has_any_role(required_roles):
                denied_reason = "AUTHORIZATION_DENIED"
        content_length = request.headers.get("content-length")
        route_limit = remote_upload_limit if path.endswith("/result") and path.startswith("/remote-workers/") else request_limit
        if content_length:
            try:
                if int(content_length) > route_limit:
                    denied_reason = "REQUEST_BODY_TOO_LARGE"
            except ValueError:
                denied_reason = "INVALID_CONTENT_LENGTH"
        if denied_reason:
            status = 413 if denied_reason == "REQUEST_BODY_TOO_LARGE" else (400 if denied_reason == "INVALID_CONTENT_LENGTH" else (401 if denied_reason == "AUTHENTICATION_REQUIRED" else 403))
            audit_logger.log({
                "event": "api_request_denied",
                "request_id": request.state.request_id,
                "method": request.method,
                "path": path,
                "status_code": status,
                "reason_code": denied_reason,
                "principal_id": principal.principal_id if principal else None,
                "remote_address": request.client.host if request.client else None,
            })
            headers = {"WWW-Authenticate": "Bearer"} if status == 401 else {}
            return JSONResponse(status_code=status, content={"detail": {"reason_code": denied_reason}}, headers=headers)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        response.headers["X-Request-ID"] = request.state.request_id
        if path == "/" or path == "/workspace" or path.startswith("/assets/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        elapsed_ms = round((monotonic() - started_clock) * 1000.0, 3)
        if not path.startswith("/assets/"):
            workbench_log.append(
                category="api",
                event="request_complete",
                level="ERROR" if response.status_code >= 500 else "WARNING" if response.status_code >= 400 else "INFO",
                message=f"{request.method} {path} -> {response.status_code}",
                details={
                    "request_id": request.state.request_id,
                    "method": request.method,
                    "path": path,
                    "status_code": response.status_code,
                    "elapsed_ms": elapsed_ms,
                    "principal_id": principal.principal_id if principal else None,
                },
            )
        if request.method not in {"GET", "HEAD", "OPTIONS"} or path.startswith("/remote-workers"):
            audit_logger.log({
                "event": "api_request",
                "request_id": request.state.request_id,
                "method": request.method,
                "path": path,
                "status_code": response.status_code,
                "principal_id": principal.principal_id if principal else None,
                "roles": sorted(principal.roles) if principal else [],
                "remote_address": request.client.host if request.client else None,
            })
        return response

    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def request_actor(request: Request) -> str:
        principal = getattr(request.state, "principal", None)
        return str(principal.principal_id if principal else "anonymous")

    def reconcile_execution(run_id: str) -> dict[str, Any] | None:
        job = execution_queue.get_by_run_id(run_id)
        if job is None:
            return None
        root = Path(job.bundle_root)
        record = _json(root / "run_record.json") or {}
        validation = _json(root / "validation" / "validation_outcome.json") or {}
        terminal = str(record.get("status") or "").upper()
        sealed = (root / "SEALED.json").is_file()
        if terminal in TERMINAL_STATES and sealed and job.state not in TERMINAL_STATES:
            job = execution_queue.finish(
                job.job_id,
                state=terminal,
                return_code=record.get("return_code", 0 if terminal == "SUCCEEDED" else None),
                validation_result=validation.get("result") or record.get("validation_result"),
                error=None if terminal == "SUCCEEDED" else record.get("terminal_reason_code"),
            )
        task_center.mark_run_by_run_id(
            run_id,
            run_status=job.state,
            validation_result=job.validation_result,
        )
        return _public_queue_job(job.to_dict())

    def execution_snapshot(run_id: str) -> dict[str, Any]:
        queued = reconcile_execution(run_id)
        if queued is not None:
            return queued
        root = _safe_bundle(run_root, run_id)
        record = _json(root / "run_record.json") or {}
        prepared = _json(root / "runtime" / "prepared_run.json") or {}
        if record:
            state = str(record.get("status") or "UNKNOWN")
        elif prepared:
            state = "PREPARED"
        else:
            state = "NOT_FOUND"
        task_center.mark_run_by_run_id(
            run_id,
            run_status=state,
            validation_result=record.get("validation_result"),
        )
        return {
            "run_id": run_id,
            "state": state,
            "submitted_at": None,
            "started_at": record.get("started_at"),
            "finished_at": record.get("finished_at") or record.get("updated_at"),
            "validation_result": record.get("validation_result"),
            "error": None if state == "SUCCEEDED" else record.get("terminal_reason_code"),
            "persistent": bool(record or prepared),
            "queue_persistent": False,
        }

    def submit_execution(
        run_id: str,
        bundle_root: str,
        expected_plan_sha256: str,
        max_attempts: int,
        hard_timeout: bool,
    ) -> dict[str, Any]:
        job = execution_queue.enqueue(
            run_id=run_id,
            bundle_root=bundle_root,
            execution_plan_sha256=expected_plan_sha256,
            max_attempts=max_attempts,
            hard_timeout=hard_timeout,
            metadata={"submitted_by": "api", "submitted_at": now_iso()},
        )
        return _public_queue_job(job.to_dict())

    def reconcile_experiment(experiment_id: str) -> list[dict[str, Any]]:
        members = experiment_store.members(experiment_id)
        for member in members:
            run_id = member.get("run_id")
            if not run_id:
                continue
            snapshot = execution_snapshot(str(run_id))
            state = str(snapshot.get("state") or member.get("state") or "UNKNOWN")
            root = _safe_bundle(run_root, str(run_id))
            assertion_payload = _json(root / "results" / "assertions.json") or {}
            experiment_store.update_member(
                experiment_id, int(member["variant_index"]), state=state,
                validation_result=snapshot.get("validation_result"),
                assertion_status=assertion_payload.get("status"),
            )
        return experiment_store.members(experiment_id)

    def comparison_payload(run_ids: list[str], requested_metrics: list[str] | None = None) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        metric_names: set[str] = set(requested_metrics or [])
        for run_id in run_ids:
            root = _safe_bundle(run_root, run_id)
            metrics_payload = _json(root / "results" / "metrics.json") or {}
            metrics = metrics_payload.get("metrics") if isinstance(metrics_payload.get("metrics"), dict) else {}
            if not requested_metrics:
                metric_names.update(str(key) for key in metrics)
            record = _json(root / "run_record.json") or {}
            assertions = _json(root / "results" / "assertions.json") or {}
            rows.append({
                "run_id": run_id,
                "status": record.get("status"),
                "validation_result": record.get("validation_result"),
                "assertion_status": assertions.get("status"),
                "metrics": metrics,
            })
        selected = sorted(metric_names)[:64]
        table = [{
            "run_id": row["run_id"], "status": row["status"],
            "validation_result": row["validation_result"], "assertion_status": row["assertion_status"],
            "values": {name: row["metrics"].get(name) for name in selected},
        } for row in rows]
        from .experiments.statistics import summarize_result_metrics
        return {
            "schema_version": "scenario.run-comparison.v2",
            "run_count": len(rows),
            "metrics": selected,
            "rows": table,
            "statistics": summarize_result_metrics(rows, selected),
        }

    @app.get("/")
    @app.get("/workspace")
    def workspace():
        index = web_root / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=503, detail={"reason_code": "WORKBENCH_ASSET_MISSING"})
        return FileResponse(index, media_type="text/html", headers={"Cache-Control": "no-store"})

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "api_version": API_VERSION,
            "workbench_version": WEB_WORKBENCH_VERSION,
            "workbench_available": (web_root / "index.html").is_file(),
            "runs_root": str(run_root),
            "diagnostic_cases_root": str(diagnostic_root),
            "queue": execution_queue.health(),
            "embedded_worker": bool(queue_worker),
            "auth": {"mode": auth_manager.mode, "enabled": auth_manager.enabled},
            "remote_worker_protocol": REMOTE_WORKER_PROTOCOL_VERSION,
            "experiments": experiment_store.health(),
            "observability": {"schema_version": "release.observability-health.v1", "log_available": workbench_log.path.exists()},
        }

    @app.get("/auth/status")
    def auth_status(request: Request) -> dict[str, Any]:
        principal = getattr(request.state, "principal", None)
        return {
            "ok": True,
            "auth": {
                "schema_version": "auth-status.v1",
                "mode": auth_manager.mode,
                "enabled": auth_manager.enabled,
                "authenticated": bool(principal and principal.authenticated),
                "principal": principal.to_public_dict() if principal else None,
                "secret_values_exposed": False,
            },
        }

    @app.get("/auth/config")
    def auth_config_payload() -> dict[str, Any]:
        return {"ok": True, "auth": auth_manager.catalog()}

    @app.get("/health/details")
    def health_details() -> dict[str, Any]:
        doctor = run_environment_doctor(
            runs_root=run_root,
            artifacts_root=artifact_root,
            strict_assets=False,
            require_api=True,
            smoke=True,
        )
        return {"ok": doctor.ok, "doctor": doctor.model_dump(mode="json")}

    @app.get("/release")
    def release() -> dict[str, Any]:
        return {"ok": True, "release": release_manifest()}

    @app.get("/capabilities")
    def capabilities() -> dict[str, Any]:
        return {"ok": True, "capabilities": capability_summary_payload()}

    @app.get("/capabilities/product-closure")
    def capability_product_closure() -> dict[str, Any]:
        return {"ok": True, "product_closure": product_capability_closure()}

    @app.get("/capabilities/governance")
    def capability_governance() -> dict[str, Any]:
        matrix = capability_governance_matrix()
        issues = validate_capability_governance()
        return {
            "ok": not any(item.get("severity") == "error" for item in issues),
            "governance": matrix,
            "validation_issues": issues,
        }

    def _provider_config_payload() -> dict[str, Any]:
        if not provider_config_path.exists():
            return {"schema_version": "taskcenter.local-model-services.v2", "providers": [], "active_provider_id": "local-template"}
        try:
            payload = json.loads(provider_config_path.read_text(encoding="utf-8"))
        except Exception:
            return {"schema_version": "taskcenter.local-model-services.v2", "providers": [], "active_provider_id": "local-template"}
        if not isinstance(payload, dict) or not isinstance(payload.get("providers"), list):
            return {"schema_version": "taskcenter.local-model-services.v2", "providers": [], "active_provider_id": "local-template"}
        payload.setdefault("schema_version", "taskcenter.local-model-services.v2")
        payload.setdefault("active_provider_id", "local-template")
        return payload

    def _write_provider_config_payload(payload: Mapping[str, Any]) -> None:
        provider_config_path.parent.mkdir(parents=True, exist_ok=True)
        provider_config_path.write_text(
            json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _provider_catalog_payload(*, live_probe: bool = False) -> dict[str, Any]:
        catalog = provider_registry().catalog(live_probe=live_probe)
        config = _provider_config_payload()
        configured = {str(item.get("provider_id")): item for item in config.get("providers", []) if isinstance(item, dict) and item.get("provider_id")}
        active_provider_id = str(config.get("active_provider_id") or "local-template")
        for item in catalog.get("providers", []):
            provider_id = str(item.get("provider_id") or "")
            stored = configured.get(provider_id)
            item["saved"] = stored is not None or provider_id == "local-template"
            item["active"] = provider_id == active_provider_id
            item["last_probe"] = copy.deepcopy(stored.get("last_probe")) if isinstance(stored, dict) else None
            readiness = item.get("readiness") if isinstance(item.get("readiness"), dict) else {}
            last_probe = item.get("last_probe") if isinstance(item.get("last_probe"), dict) else None
            if last_probe is not None:
                state = "FULL_TEST_PASSED" if last_probe.get("ok") else "FULL_TEST_FAILED"
            elif readiness.get("live_probe") and readiness.get("ready"):
                state = "ENDPOINT_REACHABLE"
            elif readiness.get("configured"):
                state = "CONFIGURED_NOT_TESTED"
            else:
                state = "NOT_CONFIGURED"
            if provider_id == "local-template":
                state = "FULL_TEST_PASSED"
            item["availability_state"] = state
        catalog["active_provider_id"] = active_provider_id
        catalog["saved_provider_ids"] = sorted(configured)
        return catalog

    @app.get("/models/providers")
    def model_providers(live_probe: bool = Query(default=False)) -> dict[str, Any]:
        return {"ok": True, "catalog": _provider_catalog_payload(live_probe=live_probe)}

    @app.get("/models/local-services")
    def local_model_services(live_probe: bool = Query(default=False)) -> dict[str, Any]:
        catalog = _provider_catalog_payload(live_probe=live_probe)
        saved_ids = set(catalog.get("saved_provider_ids", [])) | {"local-template"}
        local = [
            item for item in catalog.get("providers", [])
            if item.get("location") in {"local", "deterministic"} and item.get("provider_id") in saved_ids
        ]
        return {
            "ok": True, "config_path": str(provider_config_path), "providers": local,
            "active_provider_id": catalog.get("active_provider_id"),
            "supported_service_types": ["ollama", "lmstudio", "vllm", "openai_compatible"],
            "secret_values_exposed": False,
        }

    @app.post("/models/local-services/discover")
    def discover_local_model_service(body: LocalModelDiscoveryBody) -> dict[str, Any]:
        defaults = {
            "ollama": ("local-ollama", "本地 Ollama", "openai_compatible", "http://127.0.0.1:11434/v1"),
            "lmstudio": ("local-lmstudio", "本地 LM Studio", "openai_compatible", "http://127.0.0.1:1234/v1"),
            "vllm": ("local-vllm", "本地 vLLM / Qwen", "vllm", "http://127.0.0.1:8001/v1"),
            "openai_compatible": ("local-openai-compatible", "本地 OpenAI 兼容服务", "openai_compatible", "http://127.0.0.1:8000/v1"),
        }
        default_id, label, backend, default_url = defaults[body.service_type]
        provider_id = (body.provider_id or default_id).strip()
        if not provider_id.startswith("local-"):
            raise HTTPException(status_code=422, detail={"reason_code": "LOCAL_PROVIDER_ID_REQUIRED"})
        env_name = "SAT_SIM_RUNTIME_DISCOVERY_API_KEY"
        if body.api_key:
            os.environ[env_name] = body.api_key
        profile = ModelProviderProfile(
            provider_id=provider_id, label=label, backend=backend, location="local", tier="L2",
            model=None, base_url=(body.base_url or default_url).rstrip("/"), api_key_env=env_name,
            priority=1, description="临时本地模型发现配置。",
        )
        discovery = ModelProviderRegistry(profiles=[profile]).discover_models(provider_id, timeout_s=body.timeout_s)
        return {"ok": bool(discovery.get("ok")), "discovery": discovery, "api_key_persisted": False}

    @app.post("/models/local-services")
    def configure_local_model_service(body: LocalModelServiceBody) -> dict[str, Any]:
        defaults = {
            "ollama": ("local-ollama", "本地 Ollama", "openai_compatible", "http://127.0.0.1:11434/v1"),
            "lmstudio": ("local-lmstudio", "本地 LM Studio", "openai_compatible", "http://127.0.0.1:1234/v1"),
            "vllm": ("local-vllm", "本地 vLLM / Qwen", "vllm", "http://127.0.0.1:8001/v1"),
            "openai_compatible": ("local-openai-compatible", "本地 OpenAI 兼容服务", "openai_compatible", "http://127.0.0.1:8000/v1"),
        }
        default_id, default_label, backend, default_url = defaults[body.service_type]
        provider_id = (body.provider_id or default_id).strip()
        if not provider_id.startswith("local-"):
            raise HTTPException(status_code=422, detail={"reason_code": "LOCAL_PROVIDER_ID_REQUIRED"})
        env_name = "SAT_SIM_RUNTIME_" + "".join(ch if ch.isalnum() else "_" for ch in provider_id.upper()) + "_API_KEY"
        if body.api_key:
            os.environ[env_name] = body.api_key
        config_payload = _provider_config_payload()
        rows = config_payload.get("providers", [])
        rows = [item for item in rows if isinstance(item, dict) and item.get("provider_id") != provider_id]
        default_timeout = 300.0 if body.service_type == "lmstudio" else 180.0
        default_max_tokens = 2048 if body.service_type == "lmstudio" else 4000
        default_structured_output = "json_schema" if body.service_type == "lmstudio" else "json_object"
        rows.append({
            "provider_id": provider_id,
            "label": body.label or default_label,
            "backend": backend,
            "location": "local",
            "tier": "L2",
            "model": body.model,
            "base_url": (body.base_url or default_url).rstrip("/"),
            "api_key_env": env_name,
            "structured_output": body.structured_output or default_structured_output,
            "timeout_s": float(body.timeout_s or default_timeout),
            "max_output_tokens": int(body.max_output_tokens or default_max_tokens),
            "priority": 22,
            "enabled": body.enabled,
            "description": f"由 Web 工作台配置的 {body.service_type} 本地模型服务。",
            "capabilities": ["taskspec_generation"],
        })
        config_payload["schema_version"] = "taskcenter.local-model-services.v2"
        config_payload["providers"] = rows
        _write_provider_config_payload(config_payload)
        readiness = provider_registry().readiness(provider_id, live_probe=False)
        return {
            "ok": True,
            "provider_id": provider_id,
            "readiness": readiness.to_dict(),
            "api_key_persisted": False,
            "config_path": str(provider_config_path),
        }

    @app.delete("/models/local-services/{provider_id}")
    def delete_local_model_service(provider_id: str) -> dict[str, Any]:
        payload = _provider_config_payload()
        before = len(payload.get("providers", []))
        payload["providers"] = [item for item in payload.get("providers", []) if isinstance(item, dict) and item.get("provider_id") != provider_id]
        if payload.get("active_provider_id") == provider_id:
            payload["active_provider_id"] = "local-template"
        _write_provider_config_payload(payload)
        return {"ok": True, "deleted": len(payload["providers"]) < before, "provider_id": provider_id, "active_provider_id": payload.get("active_provider_id")}

    @app.post("/models/providers/{provider_id}/activate")
    def activate_model_provider(provider_id: str) -> dict[str, Any]:
        try:
            profile = provider_registry().get(provider_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"reason_code": "MODEL_PROVIDER_NOT_FOUND", "message": str(exc)}) from exc
        readiness = provider_registry().readiness(provider_id, live_probe=False)
        if not readiness.configured:
            raise HTTPException(status_code=422, detail={"reason_code": "MODEL_PROVIDER_NOT_CONFIGURED", "message": readiness.reason})
        payload = _provider_config_payload()
        payload["active_provider_id"] = provider_id
        _write_provider_config_payload(payload)
        return {"ok": True, "provider_id": provider_id, "active_provider_id": provider_id, "profile": profile.to_dict()}

    @app.get("/models/providers/{provider_id}/readiness")
    def model_provider_readiness(provider_id: str, live_probe: bool = Query(default=False)) -> dict[str, Any]:
        try:
            readiness = provider_registry().readiness(provider_id, live_probe=live_probe)
            return {"ok": readiness.ready, "readiness": readiness.to_dict()}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"reason_code": "MODEL_PROVIDER_NOT_FOUND", "message": str(exc)}) from exc

    @app.get("/models/providers/{provider_id}/models")
    def model_provider_models(
        provider_id: str,
        timeout_s: float = Query(default=5.0, gt=0.0, le=30.0),
    ) -> dict[str, Any]:
        try:
            discovery = provider_registry().discover_models(provider_id, timeout_s=timeout_s)
            return {"ok": bool(discovery.get("ok")), "discovery": discovery}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"reason_code": "MODEL_PROVIDER_NOT_FOUND", "message": str(exc)}) from exc

    @app.post("/models/providers/{provider_id}/probe")
    def model_provider_probe(provider_id: str, body: ProviderProbeBody) -> dict[str, Any]:
        try:
            registry = provider_registry()
            profile = registry.get(provider_id)
            readiness = registry.readiness(provider_id, live_probe=True, timeout_s=min(body.timeout_s, 10.0))
            discovery = registry.discover_models(provider_id, timeout_s=min(body.timeout_s, 10.0))
            inference = registry.probe_generation(provider_id, timeout_s=body.timeout_s)
            taskspec_probe: dict[str, Any] | None = None
            if body.include_taskspec_probe and inference.get("ok") and profile.location != "remote":
                probe_id = uuid4().hex
                result = run_unified_agent(UnifiedAgentRequest(
                    input_kind="natural_language",
                    request_text="创建一个反作用轮正常仿真，运行10秒，输出轮速。",
                    output_dir=artifact_root / "model_diagnostics" / probe_id,
                    backend="auto",
                    provider_id=provider_id,
                    provider_config_path=provider_config_path if provider_config_path.exists() else None,
                    routing_mode="local",
                    compile_if_valid=True,
                    model_timeout_s=max(float(body.timeout_s), float(profile.timeout_s)),
                    model_max_output_tokens=profile.max_output_tokens,
                    model_structured_output=profile.structured_output,
                ))
                facade_payload = result.facade_result.to_dict() if result.facade_result is not None else {}
                backend_identity = facade_payload.get("backend_identity") if isinstance(facade_payload.get("backend_identity"), dict) else {}
                invocation_verified = bool(backend_identity.get("actual_model_execution_verified"))
                taskspec_probe = {
                    "ok": bool(result.ok and result.route.provider_id == provider_id and invocation_verified),
                    "selected_provider_id": result.route.provider_id,
                    "validation_ok": result.validation.ok,
                    "guards_ok": result.guards.ok,
                    "compiled": result.compiled is not None,
                    "capability_id": (result.task_spec.get("model") or {}).get("capability_id") if isinstance(result.task_spec, dict) else None,
                    "reason_codes": list(result.reason_codes),
                    "evidence_dir": str(artifact_root / "model_diagnostics" / probe_id),
                    "fallback_detected": result.route.provider_id != provider_id,
                    "model_invocation_verified": invocation_verified,
                    "backend_identity": backend_identity,
                    "invocation_evidence": backend_identity.get("invocation_evidence"),
                }
            service_usable = bool(readiness.ready and inference.get("ok"))
            taskspec_ready = bool(taskspec_probe is None or taskspec_probe.get("ok"))
            overall = bool(service_usable and taskspec_ready)
            probe_payload = {
                "ok": overall,
                "service_usable": service_usable,
                "taskspec_ready": taskspec_ready,
                "provider_id": provider_id, "readiness": readiness.to_dict(),
                "discovery": discovery, "inference_probe": inference, "taskspec_probe": taskspec_probe,
                "diagnostic_level": "FULL_TASKSPEC" if taskspec_probe else "CONNECTIVITY_AND_JSON",
                "diagnostic_summary": (
                    "连接、JSON结构化输出和TaskSpec语义校验均通过" if overall else
                    "连接和JSON结构化输出通过，但TaskSpec语义校验未通过" if service_usable else
                    "连接或JSON结构化输出未通过"
                ),
                "fallback_must_be_explicit": True, "secret_values_exposed": False,
            }
            config_payload = _provider_config_payload()
            for row in config_payload.get("providers", []):
                if isinstance(row, dict) and row.get("provider_id") == provider_id:
                    row["last_probe"] = {
                        "ok": overall, "service_usable": service_usable, "taskspec_ready": taskspec_ready, "tested_at": now_iso(),
                        "diagnostic_level": probe_payload["diagnostic_level"],
                        "readiness_status": readiness.status,
                        "inference_status": inference.get("status"),
                        "reason": inference.get("reason") or readiness.reason,
                        "taskspec_ok": taskspec_probe.get("ok") if isinstance(taskspec_probe, dict) else None,
                        "structured_output_transport": inference.get("structured_output_transport"),
                    }
                    break
            if overall:
                config_payload["active_provider_id"] = provider_id
            _write_provider_config_payload(config_payload)
            probe_payload["active_provider_id"] = config_payload.get("active_provider_id")
            return probe_payload
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"reason_code": "MODEL_PROVIDER_NOT_FOUND", "message": str(exc)}) from exc
        except Exception as exc:
            raise HTTPException(status_code=422, detail={"reason_code": "MODEL_PROVIDER_PROBE_FAILED", "message": str(exc)}) from exc

    @app.post("/models/route")
    def model_route(body: ProviderRouteBody) -> dict[str, Any]:
        try:
            selected = provider_registry().select(
                input_kind=body.input_kind,
                request_text=body.request_text,
                routing_mode=body.routing_mode,
                provider_id=body.provider_id,
            )
            return {"ok": True, "selection": selected.to_dict()}
        except Exception as exc:
            raise HTTPException(status_code=422, detail={"reason_code": "MODEL_ROUTE_FAILED", "message": str(exc)}) from exc

    @app.get("/schema/taskspec")
    def task_schema() -> dict[str, Any]:
        return {"ok": True, "schema": CanonicalTaskSpec.model_json_schema()}

    @app.get("/scenario-templates")
    def scenario_templates(
        level: str | None = Query(default=None),
        capability_id: str | None = Query(default=None),
        object_id: str | None = Query(default=None),
        include_compatibility: bool = Query(default=False),
        include_explicit: bool = Query(default=False),
    ) -> dict[str, Any]:
        return {"ok": True, **list_scenario_templates(
            level=level,
            capability_id=capability_id,
            object_id=object_id,
            product_visible_only=True,
            include_compatibility=include_compatibility,
            include_explicit=include_explicit,
        )}

    @app.post("/scenario-templates/{template_id}/instantiate")
    def instantiate_template(template_id: str, body: ScenarioTemplateInstantiateBody) -> dict[str, Any]:
        try:
            spec = instantiate_scenario_template(template_id, task_id=body.task_id, name=body.name)
            plan = plan_task_spec(spec)
            return {"ok": True, "template_id": template_id, "task_spec": spec, "planning": plan.to_dict()}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"reason_code": "SCENARIO_TEMPLATE_NOT_FOUND"}) from exc
        except Exception as exc:
            raise HTTPException(status_code=422, detail={"reason_code": "SCENARIO_TEMPLATE_INVALID", "message": str(exc)}) from exc

    @app.get("/agent/tools")
    def agent_tools() -> dict[str, Any]:
        return agent_tool_surface()

    @app.get("/fault-environment/catalog")
    def fault_environment_catalog(
        capability_id: str | None = Query(default=None),
        category: str | None = Query(default=None),
    ) -> dict[str, Any]:
        return list_workbench_fault_models(capability_id=capability_id, category=category)

    @app.post("/experiments/options")
    def experiment_options(body: ExperimentOptionsBody) -> dict[str, Any]:
        try:
            return experiment_design_options(body.base_task_spec)
        except Exception as exc:
            raise HTTPException(status_code=422, detail={"reason_code": "EXPERIMENT_OPTIONS_FAILED", "message": str(exc)}) from exc

    @app.post("/experiments/plan")
    def experiment_plan(body: ExperimentPlanBody) -> dict[str, Any]:
        try:
            return preview_experiment_plan(
                base_task_spec=body.base_task_spec,
                experiment_type=body.experiment_type,
                sweep=body.sweep,
                sampling_plan=body.sampling_plan,
                assertions=body.assertions,
                preview_limit=body.preview_limit,
            )
        except Exception as exc:
            raise HTTPException(status_code=422, detail={"reason_code": "EXPERIMENT_PLAN_INVALID", "message": str(exc)}) from exc

    @app.post("/experiments/dispersion-options")
    def experiment_dispersion_options(body: dict[str, Any]) -> dict[str, Any]:
        try:
            base_task_spec = body.get("base_task_spec") if isinstance(body, dict) else None
            if not isinstance(base_task_spec, dict):
                raise ValueError("base_task_spec is required")
            options = dispersion_parameter_options(base_task_spec)
            return {"ok": True, "schema_version": "scenario.experiment-center.v2", "count": len(options), "options": options}
        except Exception as exc:
            raise HTTPException(status_code=422, detail={"reason_code": "DISPERSION_OPTIONS_FAILED", "message": str(exc)}) from exc

    @app.get("/experiments")
    def experiments(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
        rows = experiment_store.list(limit=limit)
        return {"ok": True, "schema_version": "scenario.experiment-center.v1", "count": len(rows), "experiments": [item.to_dict(include_task_spec=False) for item in rows]}

    @app.post("/experiments")
    def create_experiment(body: ExperimentCreateBody) -> dict[str, Any]:
        try:
            record = experiment_store.create(
                name=body.name,
                base_task_spec=body.base_task_spec,
                sweep=body.sweep,
                assertions=body.assertions,
                experiment_type=body.experiment_type,
                sampling_plan=body.sampling_plan,
            )
            return {"ok": True, "experiment": record.to_dict(), "members": experiment_store.members(record.experiment_id)}
        except Exception as exc:
            raise HTTPException(status_code=422, detail={"reason_code": "EXPERIMENT_CREATE_FAILED", "message": str(exc)}) from exc

    @app.get("/experiments/{experiment_id}")
    def get_experiment(experiment_id: str) -> dict[str, Any]:
        record = experiment_store.get(experiment_id)
        if record is None:
            raise HTTPException(status_code=404, detail="experiment not found")
        members = reconcile_experiment(experiment_id)
        record = experiment_store.get(experiment_id) or record
        return {"ok": True, "experiment": record.to_dict(), "members": members}

    @app.post("/experiments/{experiment_id}/launch")
    def launch_experiment(experiment_id: str, body: ExperimentLaunchBody) -> dict[str, Any]:
        record = experiment_store.get(experiment_id)
        if record is None:
            raise HTTPException(status_code=404, detail="experiment not found")
        launched = []
        for member in experiment_store.members(experiment_id):
            if member.get("run_id"):
                continue
            spec = canonicalize_task_spec(member["task_spec"])
            run_id = f"{experiment_id}_v{int(member['variant_index']) + 1:03d}"
            prepared = prepare_run(spec, output_root=run_root, run_id=run_id)
            state = submit_execution(prepared.run_id, prepared.bundle_root, prepared.execution_plan_sha256, body.max_attempts, body.hard_timeout)
            experiment_store.assign_run(experiment_id, int(member["variant_index"]), prepared.run_id, state=str(state.get("state") or "QUEUED"))
            launched.append({"variant_index": member["variant_index"], "run_id": prepared.run_id, "execution_state": state})
        return {"ok": True, "experiment_id": experiment_id, "launched_count": len(launched), "launched": launched}

    @app.post("/experiments/{experiment_id}/execute-basilisk-controller")
    def execute_experiment_with_basilisk_controller(experiment_id: str) -> dict[str, Any]:
        """Explicit synchronous native batch path using Basilisk's official Controller.

        The regular launch endpoint remains queue/Run-Bundle based.  This route is
        restricted to small, eligible native batches and never silently routes a
        bridge capability through the official Controller.
        """
        record = experiment_store.get(experiment_id)
        if record is None:
            raise HTTPException(status_code=404, detail="experiment not found")
        if record.experiment_type != "monte_carlo":
            raise HTTPException(status_code=422, detail={"reason_code": "NOT_MONTE_CARLO_EXPERIMENT"})
        members = experiment_store.members(experiment_id)
        if len(members) > 32:
            raise HTTPException(status_code=422, detail={"reason_code": "NATIVE_CONTROLLER_SYNC_LIMIT", "message": "synchronous native Controller execution is limited to 32 cases"})
        try:
            from .experiments.basilisk_mc_adapter import build_basilisk_mc_plan, execute_basilisk_controller_variants
            plan = build_basilisk_mc_plan(
                record.base_task_spec,
                (record.sampling_plan or {}).get("dispersions") or {},
                sample_count=len(members),
                seed=int((record.sampling_plan or {}).get("seed") or 1),
            )
            if not plan.eligible:
                raise ValueError(f"official Controller is not eligible: {plan.to_dict()}")
            archive = artifact_root / "experiments" / experiment_id / "basilisk_controller"
            report = execute_basilisk_controller_variants(
                record.base_task_spec,
                members,
                archive_dir=archive,
                thread_count=1,
            )
            public_report = dict(report)
            public_report["runs"] = [
                {key: value for key, value in run.items() if key != "trace_rows"}
                for run in report.get("runs", [])
            ]
            return {"ok": True, "experiment_id": experiment_id, "controller_plan": plan.to_dict(), "report": public_report}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"reason_code": "BASILISK_CONTROLLER_NOT_ELIGIBLE", "message": str(exc)}) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail={"reason_code": "BASILISK_CONTROLLER_EXECUTION_FAILED", "message": str(exc)}) from exc

    @app.get("/experiments/{experiment_id}/comparison")
    def experiment_comparison(experiment_id: str, metrics: str | None = Query(default=None)) -> dict[str, Any]:
        record = experiment_store.get(experiment_id)
        if record is None:
            raise HTTPException(status_code=404, detail="experiment not found")
        members = reconcile_experiment(experiment_id)
        run_ids = [str(item["run_id"]) for item in members if item.get("run_id") and str(item.get("state")) in TERMINAL_STATES]
        selected = [item.strip() for item in (metrics or "").split(",") if item.strip()]
        return {"ok": True, "experiment_id": experiment_id, "comparison": comparison_payload(run_ids, selected)}

    @app.delete("/experiments/{experiment_id}")
    def delete_experiment(experiment_id: str) -> dict[str, Any]:
        deleted = experiment_store.delete(experiment_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="experiment not found")
        return {"ok": True, "deleted": True, "experiment_id": experiment_id}

    @app.post("/runs/compare")
    def compare_runs(body: RunComparisonBody) -> dict[str, Any]:
        return {"ok": True, "comparison": comparison_payload(body.run_ids, body.metrics)}

    @app.get("/forms/capabilities")
    def form_catalog(
        include_compatibility: bool = Query(default=False),
        include_explicit: bool = Query(default=False),
    ) -> dict[str, Any]:
        return {"ok": True, "catalog": capability_form_catalog(
            include_compatibility=include_compatibility,
            include_explicit=include_explicit,
        )}

    @app.get("/forms/capabilities/{capability_id}")
    def form_schema(capability_id: str) -> dict[str, Any]:
        try:
            return {"ok": True, "form_schema": capability_form_schema(capability_id)}
        except Exception as exc:
            raise HTTPException(status_code=404, detail={"reason_code": "CAPABILITY_FORM_NOT_FOUND", "message": str(exc)}) from exc

    @app.post("/forms/capabilities/{capability_id}/project")
    def project_form(capability_id: str, body: FormProjectionBody) -> dict[str, Any]:
        try:
            return {"ok": True, "projection": task_spec_to_form_data(capability_id, body.task_spec)}
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail={"reason_code": "TASKSPEC_FORM_PROJECTION_FAILED", "message": str(exc)},
            ) from exc

    @app.get("/policy/tools")
    def tool_policy() -> dict[str, Any]:
        return {"ok": True, "tool_policy": agent_tool_policy_payload()}

    @app.post("/tasks/parse")
    def parse_task(body: TaskCreateBody) -> dict[str, Any]:
        request_id = uuid4().hex
        try:
            result = run_unified_agent(UnifiedAgentRequest(
                input_kind=body.input_kind,
                request_text=body.request_text,
                form_data=body.form_data,
                task_spec=body.task_spec,
                base_task_spec=body.base_task_spec,
                patch=body.patch,
                output_dir=artifact_root / "tasks" / request_id,
                backend=body.backend,
                local_backend=body.local_backend,
                remote_backend=body.remote_backend,
                model_name=body.model_name,
                model_base_url=body.model_base_url,
                model_api_key_env=body.model_api_key_env,
                model_timeout_s=body.model_timeout_s,
                model_temperature=body.model_temperature,
                model_max_output_tokens=body.model_max_output_tokens,
                model_structured_output=body.model_structured_output,
                provider_id=body.provider_id,
                provider_config_path=provider_config_path if provider_config_path.exists() else None,
                routing_mode=body.routing_mode,
                compile_if_valid=body.compile_if_valid,
                experience_reuse_enabled=body.experience_reuse_enabled,
                experience_store_root=experience_store.root,
                experience_tenant_id=body.experience_tenant_id,
                experience_project_id=body.experience_project_id,
            ))
            return {"ok": result.ok, "request_id": request_id, "result": result.to_dict()}
        except Exception as exc:
            raise HTTPException(status_code=422, detail={"reason_code": "TASK_PARSE_FAILED", "message": str(exc)}) from exc

    @app.post("/tasks/validate")
    def validate_task(body: TaskBody) -> dict[str, Any]:
        try:
            canonical = canonicalize_task_spec(body.task_spec)
            validation = validate_task_spec(canonical)
            guards = evaluate_agent_guards(canonical)
            return {
                "ok": validation.ok and guards.ok,
                "task_spec": canonical,
                "validation": validation.to_dict(),
                "guards": guards.to_dict(),
            }
        except Exception as exc:
            raise HTTPException(status_code=422, detail={"reason_code": "TASK_VALIDATION_FAILED", "message": str(exc)}) from exc

    @app.post("/tasks/resolve")
    def resolve_task(body: TaskBody) -> dict[str, Any]:
        planning = plan_task_spec(body.task_spec)
        return {"ok": planning.ok, "planning": planning.to_dict()}

    @app.post("/tasks/plan")
    def plan_task(body: TaskBody) -> dict[str, Any]:
        planning = plan_task_spec(body.task_spec)
        return {"ok": planning.ok, "planning": planning.to_dict()}

    @app.get("/task-center/health")
    def task_center_health() -> dict[str, Any]:
        return {"ok": True, "task_center": task_center.health()}

    @app.get("/task-center/tasks")
    def task_center_tasks(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        status: str | None = Query(default=None),
        q: str | None = Query(default=None),
    ) -> dict[str, Any]:
        payload = task_center.list(limit=limit, offset=offset, status=status, query=q)
        for item in payload.get("tasks", []):
            run_id = item.get("last_run_id")
            if run_id:
                execution_snapshot(str(run_id))
        payload = task_center.list(limit=limit, offset=offset, status=status, query=q)
        return {"ok": True, **payload}

    @app.post("/task-center/tasks")
    def save_task_center_task(body: TaskCenterSaveBody) -> dict[str, Any]:
        try:
            canonical = canonicalize_task_spec(body.task_spec)
            validation = validate_task_spec(canonical)
            record = task_center.save(
                canonical,
                task_id=body.task_id,
                name=body.name,
                status=body.status if not validation.ok else ("READY" if body.status.upper() == "DRAFT" else body.status),
                source=body.source,
                notes=body.notes,
                change_summary=body.change_summary,
                force_new_version=body.force_new_version,
            )
            return {"ok": True, "task": record.to_dict(), "validation": validation.to_dict()}
        except Exception as exc:
            raise HTTPException(status_code=422, detail={"reason_code": "TASK_CENTER_SAVE_FAILED", "message": str(exc)}) from exc

    @app.get("/task-center/tasks/{task_id}")
    def get_task_center_task(task_id: str) -> dict[str, Any]:
        record = task_center.get(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail={"reason_code": "TASK_CENTER_TASK_NOT_FOUND"})
        if record.last_run_id:
            execution_snapshot(record.last_run_id)
            record = task_center.get(task_id) or record
        return {"ok": True, "task": record.to_dict()}

    @app.put("/task-center/tasks/{task_id}")
    def update_task_center_task(task_id: str, body: TaskCenterSaveBody) -> dict[str, Any]:
        try:
            canonical = canonicalize_task_spec(body.task_spec)
            validation = validate_task_spec(canonical)
            record = task_center.save(
                canonical,
                task_id=task_id,
                name=body.name,
                status=body.status if not validation.ok else ("READY" if body.status.upper() == "DRAFT" else body.status),
                source=body.source,
                notes=body.notes,
                change_summary=body.change_summary,
                force_new_version=body.force_new_version,
            )
            return {"ok": True, "task": record.to_dict(), "validation": validation.to_dict()}
        except Exception as exc:
            raise HTTPException(status_code=422, detail={"reason_code": "TASK_CENTER_UPDATE_FAILED", "message": str(exc)}) from exc

    @app.get("/task-center/tasks/{task_id}/versions")
    def task_center_versions(task_id: str, include_task_spec: bool = Query(default=False)) -> dict[str, Any]:
        if task_center.get(task_id) is None:
            raise HTTPException(status_code=404, detail={"reason_code": "TASK_CENTER_TASK_NOT_FOUND"})
        versions = task_center.list_versions(task_id, include_task_spec=include_task_spec)
        return {
            "ok": True,
            "task_id": task_id,
            "count": len(versions),
            "versions": [item.to_dict(include_task_spec=include_task_spec) for item in versions],
        }

    @app.get("/task-center/tasks/{task_id}/versions/{version}")
    def task_center_version(task_id: str, version: int) -> dict[str, Any]:
        item = task_center.get_version(task_id, version)
        if item is None:
            raise HTTPException(status_code=404, detail={"reason_code": "TASK_CENTER_VERSION_NOT_FOUND"})
        return {"ok": True, "version": item.to_dict()}

    @app.post("/task-center/tasks/{task_id}/versions/{version}/restore")
    def restore_task_center_version(task_id: str, version: int, body: TaskCenterVersionActionBody) -> dict[str, Any]:
        record = task_center.restore_version(task_id, version, change_summary=body.change_summary)
        if record is None:
            raise HTTPException(status_code=404, detail={"reason_code": "TASK_CENTER_VERSION_NOT_FOUND"})
        return {"ok": True, "task": record.to_dict()}

    @app.post("/task-center/tasks/{task_id}/versions/{version}/baseline")
    def baseline_task_center_version(task_id: str, version: int) -> dict[str, Any]:
        record = task_center.set_baseline(task_id, version)
        if record is None:
            raise HTTPException(status_code=404, detail={"reason_code": "TASK_CENTER_VERSION_NOT_FOUND"})
        return {"ok": True, "task": record.to_dict()}

    @app.get("/task-center/tasks/{task_id}/runs")
    def task_center_runs(task_id: str, limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
        if task_center.get(task_id) is None:
            raise HTTPException(status_code=404, detail={"reason_code": "TASK_CENTER_TASK_NOT_FOUND"})
        rows = task_center.list_runs(task_id, limit=limit)
        for item in rows:
            execution_snapshot(item.run_id)
        rows = task_center.list_runs(task_id, limit=limit)
        return {"ok": True, "task_id": task_id, "count": len(rows), "runs": [item.to_dict() for item in rows]}

    @app.post("/task-center/tasks/{task_id}/clone")
    def clone_task_center_task(task_id: str, body: TaskCenterCloneBody) -> dict[str, Any]:
        record = task_center.clone(task_id, name=body.name)
        if record is None:
            raise HTTPException(status_code=404, detail={"reason_code": "TASK_CENTER_TASK_NOT_FOUND"})
        return {"ok": True, "task": record.to_dict()}

    @app.delete("/task-center/tasks/{task_id}")
    def delete_task_center_task(task_id: str) -> dict[str, Any]:
        deleted = task_center.delete(task_id)
        if not deleted:
            raise HTTPException(status_code=404, detail={"reason_code": "TASK_CENTER_TASK_NOT_FOUND"})
        return {"ok": True, "deleted": True, "task_id": task_id}

    @app.post("/task-center/tasks/{task_id}/run")
    def run_task_center_task(task_id: str, body: TaskCenterRunBody) -> dict[str, Any]:
        record = task_center.get(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail={"reason_code": "TASK_CENTER_TASK_NOT_FOUND"})
        try:
            prepared = prepare_run(record.task_spec, output_root=run_root)
            state = submit_execution(
                prepared.run_id,
                prepared.bundle_root,
                prepared.execution_plan_sha256,
                body.max_attempts,
                body.hard_timeout,
            )
            task_center.mark_run(task_id, run_id=prepared.run_id, run_status=str(state.get("state") or "QUEUED"))
            return {"ok": True, "prepared_run": prepared.model_dump(mode="json"), "execution_state": state}
        except Exception as exc:
            raise HTTPException(status_code=422, detail={"reason_code": "TASK_CENTER_RUN_FAILED", "message": str(exc)}) from exc

    @app.get("/queue/health")
    def queue_health() -> dict[str, Any]:
        return {"ok": True, "queue": execution_queue.health(), "workers": execution_queue.list_workers()}

    @app.get("/workers")
    def workers() -> dict[str, Any]:
        return {"ok": True, "workers": execution_queue.list_workers(), "protocol_version": REMOTE_WORKER_PROTOCOL_VERSION}

    @app.get("/queue/jobs")
    def queue_jobs(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        state: str | None = Query(default=None),
    ) -> dict[str, Any]:
        states = [item.strip() for item in state.split(",")] if state else None
        payload = execution_queue.list_jobs(limit=limit, offset=offset, states=states)
        payload["jobs"] = [_public_queue_job(item) for item in payload.get("jobs", [])]
        return {"ok": True, **payload}

    @app.get("/queue/jobs/{job_id}")
    def queue_job(job_id: str) -> dict[str, Any]:
        job = execution_queue.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail={"reason_code": "QUEUE_JOB_NOT_FOUND"})
        return {"ok": True, "job": _public_queue_job(job.to_dict()), "events": execution_queue.events(job_id)}

    def _bound_remote_job(request: Request, worker_id: str, job_id: str):
        try:
            _assert_remote_worker_binding(request, worker_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail={"reason_code": "REMOTE_WORKER_BINDING_DENIED", "message": str(exc)}) from exc
        job = execution_queue.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail={"reason_code": "QUEUE_JOB_NOT_FOUND"})
        if job.worker_id != worker_id:
            raise HTTPException(status_code=409, detail={"reason_code": "REMOTE_WORKER_LEASE_NOT_OWNED"})
        return job

    @app.post("/remote-workers/{worker_id}/register")
    def remote_worker_register(worker_id: str, body: RemoteWorkerRegisterBody, request: Request) -> dict[str, Any]:
        try:
            _assert_remote_worker_binding(request, worker_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail={"reason_code": "REMOTE_WORKER_BINDING_DENIED", "message": str(exc)}) from exc
        capabilities = dict(body.capabilities)
        capabilities["remote"] = True
        capabilities["protocol_version"] = REMOTE_WORKER_PROTOCOL_VERSION
        execution_queue.register_worker(worker_id, capabilities=capabilities, worker_host=body.worker_host)
        return {"ok": True, "worker_id": worker_id, "protocol_version": REMOTE_WORKER_PROTOCOL_VERSION}

    @app.post("/remote-workers/{worker_id}/claim")
    def remote_worker_claim(worker_id: str, body: RemoteWorkerClaimBody, request: Request) -> dict[str, Any]:
        try:
            _assert_remote_worker_binding(request, worker_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail={"reason_code": "REMOTE_WORKER_BINDING_DENIED", "message": str(exc)}) from exc
        workers_by_id = {item["worker_id"]: item for item in execution_queue.list_workers()}
        worker = workers_by_id.get(worker_id)
        if worker is None:
            raise HTTPException(status_code=409, detail={"reason_code": "REMOTE_WORKER_NOT_REGISTERED"})
        job = execution_queue.claim_next(
            worker_id=worker_id,
            worker_host=str(worker.get("worker_host") or "remote"),
            lease_seconds=body.lease_seconds,
        )
        execution_queue.heartbeat_worker(worker_id)
        return {
            "ok": True,
            "claimed": job is not None,
            "job": _public_queue_job(job.to_dict()) if job else None,
            "protocol_version": REMOTE_WORKER_PROTOCOL_VERSION,
        }

    @app.post("/remote-workers/{worker_id}/jobs/{job_id}/start")
    def remote_worker_start(worker_id: str, job_id: str, body: RemoteWorkerStateBody, request: Request) -> dict[str, Any]:
        job = _bound_remote_job(request, worker_id, job_id)
        running = execution_queue.mark_running(
            job.job_id,
            worker_id=worker_id,
            pid=body.pid,
            lease_seconds=body.lease_seconds,
            worker_host=job.worker_host,
        )
        return {"ok": running.state == "RUNNING", "job": _public_queue_job(running.to_dict())}

    @app.post("/remote-workers/{worker_id}/jobs/{job_id}/heartbeat")
    def remote_worker_heartbeat(worker_id: str, job_id: str, body: RemoteWorkerStateBody, request: Request) -> dict[str, Any]:
        job = _bound_remote_job(request, worker_id, job_id)
        owns = execution_queue.heartbeat(
            job.job_id,
            worker_id=worker_id,
            pid=body.pid,
            lease_seconds=body.lease_seconds,
        )
        execution_queue.heartbeat_worker(worker_id)
        current = execution_queue.get(job.job_id)
        return {"ok": owns, "owns_lease": owns, "job": _public_queue_job(current.to_dict()) if current else None}

    @app.get("/remote-workers/{worker_id}/jobs/{job_id}/bundle")
    def remote_worker_bundle(worker_id: str, job_id: str, request: Request):
        job = _bound_remote_job(request, worker_id, job_id)
        if job.state not in {"CLAIMED", "RUNNING", "CANCEL_REQUESTED"}:
            raise HTTPException(status_code=409, detail={"reason_code": "REMOTE_BUNDLE_STATE_INVALID"})
        exchange = artifact_root / "remote_exchange"
        exchange.mkdir(parents=True, exist_ok=True)
        archive = Path(tempfile.mkstemp(prefix=f"{job.job_id}-", suffix=".zip", dir=exchange)[1])
        try:
            build_bundle_archive(job.bundle_root, archive)
        except Exception:
            archive.unlink(missing_ok=True)
            raise
        return FileResponse(
            archive,
            media_type="application/zip",
            filename=f"{job.run_id}.prepared.zip",
            headers={"X-Sat-Sim-Protocol": REMOTE_WORKER_PROTOCOL_VERSION},
            background=BackgroundTask(archive.unlink, missing_ok=True),
        )

    @app.post("/remote-workers/{worker_id}/jobs/{job_id}/result")
    async def remote_worker_result(worker_id: str, job_id: str, request: Request) -> dict[str, Any]:
        job = _bound_remote_job(request, worker_id, job_id)
        if request.headers.get("X-Sat-Sim-Protocol") != REMOTE_WORKER_PROTOCOL_VERSION:
            raise HTTPException(status_code=409, detail={"reason_code": "REMOTE_WORKER_PROTOCOL_MISMATCH"})
        exchange = artifact_root / "remote_exchange"
        exchange.mkdir(parents=True, exist_ok=True)
        temp_zip = Path(tempfile.mkstemp(prefix=f"{job.job_id}-result-", suffix=".zip", dir=exchange)[1])
        size = 0
        staged_parent: Path | None = None
        try:
            with temp_zip.open("wb") as handle:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > remote_upload_limit:
                        raise HTTPException(status_code=413, detail={"reason_code": "REMOTE_RESULT_TOO_LARGE"})
                    handle.write(chunk)
            staged_parent = Path(tempfile.mkdtemp(prefix=f".{job.run_id}.remote-stage-", dir=Path(job.bundle_root).parent))
            staged_root = staged_parent / job.run_id
            safe_extract_bundle_archive(temp_zip, staged_root)
            result = validate_remote_result_bundle(
                staged_root,
                expected_run_id=job.run_id,
                expected_plan_sha256=job.execution_plan_sha256,
            )
            installed = install_remote_result_bundle(staged_root, job.bundle_root)
            shutil.rmtree(staged_parent, ignore_errors=True)
            staged_parent = None
            finished = execution_queue.finish(
                job.job_id,
                state=result["state"],
                return_code=result.get("return_code"),
                validation_result=result.get("validation_result"),
                error=None if result["state"] == "SUCCEEDED" else result.get("terminal_reason_code"),
                worker_id=worker_id,
            )
            return {
                "ok": result["state"] == "SUCCEEDED" and result.get("validation_result") == "PASS",
                "job": _public_queue_job(finished.to_dict()),
                "result": result,
                "bundle_ref": installed.name,
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=422, detail={"reason_code": "REMOTE_RESULT_REJECTED", "message": str(exc)}) from exc
        finally:
            temp_zip.unlink(missing_ok=True)
            if staged_parent is not None:
                shutil.rmtree(staged_parent, ignore_errors=True)

    @app.get("/logs")
    def workbench_logs(
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=200, ge=1, le=2000),
        level: str | None = Query(default=None),
        category: str | None = Query(default=None),
        search: str | None = Query(default=None),
    ) -> dict[str, Any]:
        return {"ok": True, "logs": workbench_log.query(offset=offset, limit=limit, level=level, category=category, search=search)}

    @app.get("/logs/download")
    def download_workbench_logs():
        if not workbench_log.path.is_file():
            workbench_log.append(category="system", event="log_initialized", message="Workbench log initialized")
        return FileResponse(workbench_log.path, filename="sat_sim_workbench.jsonl", media_type="application/x-ndjson")

    @app.get("/diagnostics/pipelines")
    def diagnostic_pipelines() -> dict[str, Any]:
        bundle = build_astrograph_diagnostic_mapping_bundle()
        return {
            "ok": True,
            "runtime_contract": bundle.get("runtime_contract"),
            "pipelines": [item.to_dict() for item in list_diagnostic_pipelines()],
            "mappings": bundle.get("mappings"),
        }

    @app.get("/diagnostics/pipelines/{fault_id}")
    def diagnostic_pipeline(fault_id: str) -> dict[str, Any]:
        try:
            return {"ok": True, "mapping": get_astrograph_diagnostic_mapping(fault_id)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"reason_code": "DIAGNOSTIC_PIPELINE_NOT_FOUND", "message": str(exc)}) from exc

    @app.post("/diagnostics/pipelines/{fault_id}/execute")
    def execute_diagnostic_pipeline(fault_id: str, body: DiagnosticPipelineExecuteBody) -> dict[str, Any]:
        try:
            fault_root = _safe_diagnostic_case(diagnostic_root, body.fault_case_id)
            nominal_root = _safe_diagnostic_case(diagnostic_root, body.nominal_case_id)
            runtime = DiagnosticPipelineExecutor().execute(
                fault_root=fault_root,
                nominal_root=nominal_root,
                fault_id=fault_id,
                external_stage_outputs=body.external_stage_outputs,
                persist=body.persist,
            )
            return {
                "ok": runtime.get("execution_status") not in {"BLOCKED_PIPELINE_STATUS", "REJECTED_LOCAL_EVIDENCE"},
                "runtime": runtime,
            }
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"reason_code": "DIAGNOSTIC_PIPELINE_NOT_FOUND", "message": str(exc)}) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"reason_code": "DIAGNOSTIC_CASE_NOT_FOUND", "message": str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"reason_code": "DIAGNOSTIC_PIPELINE_REQUEST_INVALID", "message": str(exc)}) from exc

    @app.get("/diagnostics/summary")
    def diagnostics_summary() -> dict[str, Any]:
        doctor = run_environment_doctor(
            runs_root=run_root, artifacts_root=artifact_root, strict_assets=False, require_api=True, smoke=True,
        )
        return {
            "ok": doctor.ok,
            "schema_version": "release.diagnostic-summary.v1",
            "doctor": doctor.model_dump(mode="json"),
            "queue": execution_queue.health(),
            "task_center": task_center.health(),
            "experiments": experiment_store.health(),
            "providers": provider_registry().catalog(live_probe=False),
            "logs": workbench_log.query(limit=50),
        }

    @app.get("/diagnostics/bundle")
    def diagnostic_bundle(run_id: str | None = Query(default=None)):
        run_path = None
        if run_id:
            try:
                run_path = _safe_bundle(run_root, run_id)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if not run_path.exists():
                raise HTTPException(status_code=404, detail="run not found")
        output = artifact_root / "diagnostics" / f"sat_sim_diagnostic_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}.zip"
        path = create_diagnostic_bundle(
            output,
            release=release_manifest(),
            health=health(),
            provider_catalog=provider_registry().catalog(live_probe=False),
            task_center_health=task_center.health(),
            experiment_health=experiment_store.health(),
            queue_health=execution_queue.health(),
            log_text=workbench_log.tail_text(max_lines=500),
            audit_log_path=audit_logger.path,
            run_root=run_path,
        )
        workbench_log.append(category="diagnostic", event="bundle_created", message="Diagnostic bundle created", details={"run_id": run_id, "file_name": path.name})
        return FileResponse(path, filename=path.name, media_type="application/zip")

    @app.post("/experience-lessons/compile")
    def compile_experience_lesson(
        body: ExperienceLessonCompileBody,
        request: Request,
    ) -> dict[str, Any]:
        try:
            lesson, created = experience_lessons.compile(
                body.experience_ids,
                scope=ExperienceScope(
                    tenant_id=body.tenant_id,
                    project_id=body.project_id,
                ),
                actor=request_actor(request),
            )
            return {
                "ok": True,
                "created": created,
                "lesson": lesson.model_dump(mode="json"),
            }
        except (KeyError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail={"reason_code": "LESSON_COMPILE_REJECTED", "message": str(exc)},
            ) from exc

    @app.post("/experience-lessons/{lesson_id}/evaluate")
    def evaluate_experience_lesson(
        lesson_id: str,
        body: ExperienceLessonEvaluateBody,
        request: Request,
    ) -> dict[str, Any]:
        try:
            evaluation = experience_lessons.evaluate(
                lesson_id,
                scope=ExperienceScope(
                    tenant_id=body.tenant_id,
                    project_id=body.project_id,
                ),
                evaluator=request_actor(request),
                held_out_case_ids=body.held_out_case_ids,
                baseline_metrics=body.baseline_metrics,
                candidate_metrics=body.candidate_metrics,
                evaluation_evidence=body.evaluation_evidence,
            )
            return {"ok": True, "evaluation": evaluation}
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={"reason_code": "LESSON_NOT_FOUND"},
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"reason_code": "LESSON_EVALUATION_REJECTED", "message": str(exc)},
            ) from exc

    @app.post("/experience-lessons/{lesson_id}/review")
    def review_experience_lesson(
        lesson_id: str,
        body: ExperienceLessonReviewBody,
        request: Request,
    ) -> dict[str, Any]:
        try:
            lesson = experience_lessons.review(
                lesson_id,
                scope=ExperienceScope(
                    tenant_id=body.tenant_id,
                    project_id=body.project_id,
                ),
                reviewer=request_actor(request),
                decision=body.decision,
                reason=body.reason,
            )
            return {"ok": True, "lesson": lesson.model_dump(mode="json")}
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={"reason_code": "LESSON_NOT_FOUND"},
            ) from exc
        except (ValueError, sqlite3.IntegrityError) as exc:
            raise HTTPException(
                status_code=422,
                detail={"reason_code": "LESSON_REVIEW_REJECTED", "message": str(exc)},
            ) from exc

    @app.post("/experience-lessons/{lesson_id}/revoke")
    def revoke_experience_lesson(
        lesson_id: str,
        body: ExperienceRevokeBody,
        request: Request,
    ) -> dict[str, Any]:
        try:
            lesson = experience_lessons.revoke(
                lesson_id,
                scope=ExperienceScope(
                    tenant_id=body.tenant_id,
                    project_id=body.project_id,
                ),
                actor=request_actor(request),
                reason=body.reason,
            )
            return {"ok": True, "lesson": lesson.model_dump(mode="json")}
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={"reason_code": "LESSON_NOT_FOUND"},
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"reason_code": "LESSON_REVOKE_REJECTED", "message": str(exc)},
            ) from exc

    @app.get("/experience-lessons")
    def retrieve_experience_lessons(
        tenant_id: str = Query(min_length=1, max_length=128),
        project_id: str = Query(min_length=1, max_length=128),
        capability_id: str = Query(min_length=1),
        mode: str | None = Query(default=None),
        task_spec_version: str = Query(default="1.0.0"),
        limit: int = Query(default=10, ge=1, le=100),
    ) -> dict[str, Any]:
        lessons = experience_lessons.retrieve_approved(
            scope=ExperienceScope(tenant_id=tenant_id, project_id=project_id),
            capability_id=capability_id,
            mode=mode,
            task_spec_version=task_spec_version,
            limit=limit,
        )
        return {
            "ok": True,
            "count": len(lessons),
            "lessons": [lesson.model_dump(mode="json") for lesson in lessons],
        }

    @app.post("/experience-lessons/snapshots")
    def create_experience_lesson_snapshot(
        body: ExperienceLessonScopeBody,
        request: Request,
    ) -> dict[str, Any]:
        snapshot = experience_lessons.create_snapshot(
            scope=ExperienceScope(
                tenant_id=body.tenant_id,
                project_id=body.project_id,
            ),
            actor=request_actor(request),
        )
        return {"ok": True, "snapshot": snapshot}

    @app.post("/experience-lessons/reuse")
    def configure_experience_lesson_reuse(
        body: ExperienceLessonReuseBody,
        request: Request,
    ) -> dict[str, Any]:
        experience_lessons.set_reuse_enabled(
            scope=ExperienceScope(
                tenant_id=body.tenant_id,
                project_id=body.project_id,
            ),
            enabled=body.enabled,
            actor=request_actor(request),
        )
        return {"ok": True, "enabled": body.enabled}

    @app.post("/experience-lessons/rollback")
    def rollback_experience_lesson_snapshot(
        body: ExperienceLessonRollbackBody,
        request: Request,
    ) -> dict[str, Any]:
        try:
            experience_lessons.rollback_snapshot(
                body.snapshot_id,
                scope=ExperienceScope(
                    tenant_id=body.tenant_id,
                    project_id=body.project_id,
                ),
                actor=request_actor(request),
            )
            return {"ok": True, "active_snapshot_id": body.snapshot_id}
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={"reason_code": "LESSON_SNAPSHOT_NOT_FOUND"},
            ) from exc

    @app.get("/experiences")
    def search_experiences(
        request: Request,
        tenant_id: str = Query(min_length=1, max_length=128),
        project_id: str = Query(min_length=1, max_length=128),
        capability_id: str | None = Query(default=None),
        mode: str | None = Query(default=None),
        effect: str | None = Query(default=None),
        error_code: str | None = Query(default=None),
        validation_result: str | None = Query(default=None),
        trust_level: Literal["raw", "verified", "approved", "revoked"] | None = Query(default=None),
        include_revoked: bool = Query(default=False),
        limit: int = Query(default=100, ge=1, le=10_000),
    ) -> dict[str, Any]:
        scope = ExperienceScope(tenant_id=tenant_id, project_id=project_id)
        records = experience_store.search(
            scope=scope,
            capability_id=capability_id,
            mode=mode,
            effect=effect,
            error_code=error_code,
            validation_result=validation_result,
            trust_level=trust_level,
            include_revoked=include_revoked,
            limit=limit,
            actor=request_actor(request),
        )
        return {"ok": True, "count": len(records), "experiences": records}

    @app.post("/experiences/capture-run")
    def capture_run_experience(body: ExperienceCaptureBody, request: Request) -> dict[str, Any]:
        try:
            bundle = _safe_bundle(run_root, body.run_id)
            if not bundle.is_dir():
                raise FileNotFoundError(body.run_id)
            record, created = experience_store.capture_run_bundle(
                bundle,
                request_text=body.request_text,
                scope=ExperienceScope(
                    tenant_id=body.tenant_id,
                    project_id=body.project_id,
                ),
                actor=request_actor(request),
                model_identity=body.model_identity,
                feedback=body.feedback,
                environment_tags=body.environment_tags,
            )
            return {
                "ok": True,
                "created": created,
                "experience": record.model_dump(mode="json"),
            }
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail={"reason_code": "RUN_NOT_FOUND", "message": str(exc)},
            ) from exc
        except (ValueError, KeyError) as exc:
            raise HTTPException(
                status_code=422,
                detail={"reason_code": "EXPERIENCE_CAPTURE_REJECTED", "message": str(exc)},
            ) from exc

    @app.get("/experiences/{experience_id}/verify")
    def verify_experience(
        experience_id: str,
        request: Request,
        tenant_id: str = Query(min_length=1, max_length=128),
        project_id: str = Query(min_length=1, max_length=128),
    ) -> dict[str, Any]:
        try:
            result = experience_store.verify(
                experience_id,
                scope=ExperienceScope(tenant_id=tenant_id, project_id=project_id),
                actor=request_actor(request),
            )
            return {"ok": bool(result["ok"]), "verification": result}
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={"reason_code": "EXPERIENCE_NOT_FOUND"},
            ) from exc

    @app.get("/experiences/{experience_id}")
    def get_experience(
        experience_id: str,
        request: Request,
        tenant_id: str = Query(min_length=1, max_length=128),
        project_id: str = Query(min_length=1, max_length=128),
    ) -> dict[str, Any]:
        try:
            record = experience_store.get(
                experience_id,
                scope=ExperienceScope(tenant_id=tenant_id, project_id=project_id),
                actor=request_actor(request),
            )
            return {"ok": True, "experience": record.model_dump(mode="json")}
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={"reason_code": "EXPERIENCE_NOT_FOUND"},
            ) from exc

    @app.post("/experiences/{experience_id}/revoke")
    def revoke_experience(
        experience_id: str,
        body: ExperienceRevokeBody,
        request: Request,
    ) -> dict[str, Any]:
        try:
            experience_store.revoke(
                experience_id,
                scope=ExperienceScope(
                    tenant_id=body.tenant_id,
                    project_id=body.project_id,
                ),
                actor=request_actor(request),
                reason=body.reason,
            )
            return {"ok": True, "experience_id": experience_id, "revoked": True}
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={"reason_code": "EXPERIENCE_NOT_FOUND"},
            ) from exc

    @app.get("/runs")
    def list_runs(
        limit: int = Query(default=30, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        status: str | None = Query(default=None),
    ) -> dict[str, Any]:
        return {"ok": True, **_run_list_payload(run_root, limit=limit, offset=offset, status=status)}

    @app.post("/runs")
    def create_run(body: RunCreateBody) -> dict[str, Any]:
        try:
            canonical = canonicalize_task_spec(body.task_spec)
            task_record = task_center.save(
                canonical,
                status="QUEUED" if body.execute else "READY",
                source="run_submission",
            )
            prepared = prepare_run(
                canonical,
                output_root=run_root,
                run_id=body.run_id,
                supersedes_run_id=body.supersedes_run_id,
            )
            payload: dict[str, Any] = {
                "ok": True,
                "prepared_run": prepared.model_dump(mode="json"),
                "task_center_task": task_record.to_dict(include_task_spec=False),
            }
            if body.execute and body.execution_mode == "async":
                payload["execution_state"] = submit_execution(
                    prepared.run_id,
                    prepared.bundle_root,
                    prepared.execution_plan_sha256,
                    body.max_attempts,
                    body.hard_timeout,
                )
                task_center.mark_run(
                    task_record.task_id,
                    run_id=prepared.run_id,
                    run_status=str(payload["execution_state"].get("state") or "QUEUED"),
                )
            elif body.execute:
                executed = execute_prepared_run(
                    prepared.bundle_root,
                    expected_plan_sha256=prepared.execution_plan_sha256,
                    max_attempts=body.max_attempts,
                    hard_timeout=body.hard_timeout,
                )
                payload["execution"] = executed.model_dump(mode="json")
                payload["ok"] = executed.run_record.status.value == "SUCCEEDED" and executed.validation.result.value == "PASS"
                task_center.mark_run(
                    task_record.task_id,
                    run_id=prepared.run_id,
                    run_status=executed.run_record.status.value,
                    validation_result=executed.validation.result.value,
                )
            return payload
        except Exception as exc:
            raise HTTPException(status_code=422, detail={"reason_code": "RUN_CREATE_FAILED", "message": str(exc)}) from exc

    @app.post("/runs/{run_id}/execute")
    def execute_run(run_id: str, body: RunExecuteBody) -> dict[str, Any]:
        try:
            root = _safe_bundle(run_root, run_id)
            if not root.exists():
                raise HTTPException(status_code=404, detail="run not found")
            executed = execute_prepared_run(
                root,
                expected_plan_sha256=body.execution_plan_sha256,
                max_attempts=body.max_attempts,
                hard_timeout=body.hard_timeout,
            )
            ok = executed.run_record.status.value == "SUCCEEDED" and executed.validation.result.value == "PASS"
            return {"ok": ok, "execution": executed.model_dump(mode="json")}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=422, detail={"reason_code": "RUN_EXECUTION_FAILED", "message": str(exc)}) from exc

    @app.post("/runs/{run_id}/execute-async")
    def execute_run_async(run_id: str, body: RunExecuteBody) -> dict[str, Any]:
        try:
            root = _safe_bundle(run_root, run_id)
            if not root.exists():
                raise HTTPException(status_code=404, detail="run not found")
            state = submit_execution(
                run_id,
                str(root),
                body.execution_plan_sha256,
                body.max_attempts,
                body.hard_timeout,
            )
            return {"ok": True, "execution_state": state}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail={"reason_code": "RUN_ASYNC_SUBMIT_FAILED", "message": str(exc)},
            ) from exc

    @app.get("/runs/{run_id}/execution")
    def get_execution_state(run_id: str) -> dict[str, Any]:
        state = execution_snapshot(run_id)
        if state["state"] == "NOT_FOUND":
            raise HTTPException(status_code=404, detail="run not found")
        return {"ok": True, "execution_state": state}

    @app.post("/runs/{run_id}/cancel")
    def cancel_run(run_id: str, body: CancelBody) -> dict[str, Any]:
        try:
            root = _safe_bundle(run_root, run_id)
            if not root.exists():
                raise HTTPException(status_code=404, detail="run not found")
            path = request_cancel(root, reason=body.reason)
            job, cancelled_before_start = execution_queue.request_cancel(run_id=run_id, reason=body.reason)
            state = _public_queue_job(job.to_dict()) if job is not None else {
                "run_id": run_id,
                "state": "CANCEL_REQUESTED",
                "cancel_requested_at": now_iso(),
                "cancel_reason": body.reason,
                "persistent": True,
            }
            return {
                "ok": True,
                "cancel_request": str(path.relative_to(root)),
                "cancelled_before_start": cancelled_before_start,
                "execution_state": state,
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=422, detail={"reason_code": "RUN_CANCEL_FAILED", "message": str(exc)}) from exc

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        try:
            root = _safe_bundle(run_root, run_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not root.exists():
            raise HTTPException(status_code=404, detail="run not found")
        return {"ok": True, "run": _run_payload(root)}

    @app.get("/runs/{run_id}/metrics")
    def get_metrics(run_id: str) -> dict[str, Any]:
        root = _safe_bundle(run_root, run_id)
        payload = _json(root / "results" / "metrics.json")
        if payload is None:
            raise HTTPException(status_code=404, detail="metrics not found")
        metric_metadata, requested_qoi = _metric_presentation_payload(root, payload)
        return {
            "ok": True,
            "run_id": run_id,
            "metrics": payload,
            "metric_metadata": metric_metadata,
            "requested_qoi": requested_qoi,
        }

    @app.get("/runs/{run_id}/events")
    def get_events(run_id: str) -> dict[str, Any]:
        root = _safe_bundle(run_root, run_id)
        payload = _json(root / "results" / "events.json")
        if payload is None:
            raise HTTPException(status_code=404, detail="events not found")
        return {"ok": True, "run_id": run_id, "events": payload}

    @app.get("/runs/{run_id}/assertions")
    def get_assertions(run_id: str) -> dict[str, Any]:
        root = _safe_bundle(run_root, run_id)
        payload = _json(root / "results" / "assertions.json")
        if payload is None:
            raise HTTPException(status_code=404, detail="assertions not found")
        return {"ok": True, "run_id": run_id, "assertions": payload}

    @app.get("/runs/{run_id}/plots")
    def get_plots(run_id: str) -> dict[str, Any]:
        root = _safe_bundle(run_root, run_id)
        payload = _json(root / "results" / "plot_manifest.json")
        if payload is None:
            raise HTTPException(status_code=404, detail="plot manifest not found")
        return {"ok": True, "run_id": run_id, "plot_manifest": payload}

    @app.get("/runs/{run_id}/telemetry")
    def get_telemetry(
        run_id: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=1000, ge=1, le=_MAX_TELEMETRY_ROWS),
    ) -> dict[str, Any]:
        root = _safe_bundle(run_root, run_id)
        if not root.exists():
            raise HTTPException(status_code=404, detail="run not found")
        return {"ok": True, "run_id": run_id, **_read_telemetry(root, offset=offset, limit=limit)}

    @app.get("/runs/{run_id}/telemetry-streams")
    def get_telemetry_streams(run_id: str) -> dict[str, Any]:
        root = _safe_bundle(run_root, run_id)
        payload = _json(root / "results" / "dataset" / "telemetry" / "multi_rate_manifest.json")
        if payload is None:
            raise HTTPException(status_code=404, detail="multi-rate telemetry manifest not found")
        return {"ok": True, "run_id": run_id, "multi_rate_telemetry": payload}

    @app.get("/runs/{run_id}/telemetry-streams/{stream_id}")
    def get_telemetry_stream(
        run_id: str,
        stream_id: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=1000, ge=1, le=_MAX_TELEMETRY_ROWS),
        start_s: float | None = Query(default=None, ge=0),
        end_s: float | None = Query(default=None, ge=0),
    ) -> dict[str, Any]:
        root = _safe_bundle(run_root, run_id)
        if start_s is not None and end_s is not None and end_s < start_s:
            raise HTTPException(status_code=422, detail="end_s must be >= start_s")
        try:
            payload = _read_dataset_stream(
                root, stream_id, offset=offset, limit=limit, start_s=start_s, end_s=end_s
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"telemetry stream not found: {stream_id}") from exc
        return {"ok": True, "run_id": run_id, **payload}

    def _filtered_fmea_payload(
        run_id: str,
        *,
        category: str | None = None,
        evidence_status: str | None = None,
        rating_status: str | None = None,
        physical_effect_verified: bool | None = None,
        min_rpn: int | None = None,
        max_rpn: int | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        root = _safe_bundle(run_root, run_id)
        manifest = _json(root / "results" / "dataset" / "fmea" / "fmea_manifest.json")
        table = _json(root / "results" / "dataset" / "fmea" / "fmea.json")
        traceability = _json(root / "results" / "dataset" / "traceability" / "fault_traceability.json")
        if manifest is None:
            raise HTTPException(status_code=404, detail="FMEA output not found")
        source_rows = list(table.get("rows") or []) if isinstance(table, dict) else []
        query = str(search or "").strip().lower()

        def accepted(row: dict[str, Any]) -> bool:
            if category and str(row.get("category") or "") != category:
                return False
            if evidence_status and str(row.get("evidence_status") or "") != evidence_status:
                return False
            if rating_status and str(row.get("rating_status") or "") != rating_status:
                return False
            if physical_effect_verified is not None and bool(row.get("physical_effect_verified")) is not physical_effect_verified:
                return False
            rpn = row.get("rpn")
            if min_rpn is not None and (rpn is None or int(rpn) < min_rpn):
                return False
            if max_rpn is not None and (rpn is None or int(rpn) > max_rpn):
                return False
            if query:
                haystack = " ".join(str(row.get(key) or "") for key in (
                    "event_id", "failure_mode", "target", "local_effect", "system_effect",
                    "detection_method", "expected_observables", "telemetry_stream_ids",
                )).lower()
                if query not in haystack:
                    return False
            return True

        rows = [row for row in source_rows if isinstance(row, dict) and accepted(row)]
        filtered_table = dict(table or {})
        filtered_table["row_count"] = len(rows)
        filtered_table["source_row_count"] = len(source_rows)
        filtered_table["rows"] = rows
        filters = {
            "category": category,
            "evidence_status": evidence_status,
            "rating_status": rating_status,
            "physical_effect_verified": physical_effect_verified,
            "min_rpn": min_rpn,
            "max_rpn": max_rpn,
            "search": search,
        }
        return {
            "ok": True,
            "run_id": run_id,
            "manifest": manifest,
            "table": filtered_table,
            "filters": filters,
            "filtered_row_count": len(rows),
            "source_row_count": len(source_rows),
            "traceability_summary": {
                key: traceability.get(key)
                for key in (
                    "schema_version", "link_count", "episode_linked_count",
                    "telemetry_covered_count", "physical_effect_verified_count", "validation_result",
                )
            } if isinstance(traceability, dict) else None,
        }

    @app.get("/runs/{run_id}/fmea")
    def get_fmea(
        run_id: str,
        category: str | None = Query(default=None),
        evidence_status: str | None = Query(default=None),
        rating_status: str | None = Query(default=None),
        physical_effect_verified: bool | None = Query(default=None),
        min_rpn: int | None = Query(default=None, ge=1),
        max_rpn: int | None = Query(default=None, ge=1),
        search: str | None = Query(default=None, max_length=256),
    ) -> dict[str, Any]:
        if min_rpn is not None and max_rpn is not None and max_rpn < min_rpn:
            raise HTTPException(status_code=422, detail="max_rpn must be >= min_rpn")
        return _filtered_fmea_payload(
            run_id,
            category=category,
            evidence_status=evidence_status,
            rating_status=rating_status,
            physical_effect_verified=physical_effect_verified,
            min_rpn=min_rpn,
            max_rpn=max_rpn,
            search=search,
        )

    @app.get("/runs/{run_id}/fmea/download")
    def download_fmea(
        run_id: str,
        format: Literal["csv", "json"] = Query(default="csv"),
        category: str | None = Query(default=None),
        evidence_status: str | None = Query(default=None),
        rating_status: str | None = Query(default=None),
        physical_effect_verified: bool | None = Query(default=None),
        min_rpn: int | None = Query(default=None, ge=1),
        max_rpn: int | None = Query(default=None, ge=1),
        search: str | None = Query(default=None, max_length=256),
    ):
        if min_rpn is not None and max_rpn is not None and max_rpn < min_rpn:
            raise HTTPException(status_code=422, detail="max_rpn must be >= min_rpn")
        payload = _filtered_fmea_payload(
            run_id,
            category=category,
            evidence_status=evidence_status,
            rating_status=rating_status,
            physical_effect_verified=physical_effect_verified,
            min_rpn=min_rpn,
            max_rpn=max_rpn,
            search=search,
        )
        suffix = ".json" if format == "json" else ".csv"
        handle, temp_name = tempfile.mkstemp(prefix=f"fmea_{run_id}_", suffix=suffix)
        os.close(handle)
        path = Path(temp_name)
        if format == "json":
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            media_type = "application/json"
        else:
            rows = list((payload.get("table") or {}).get("rows") or [])
            fieldnames: list[str] = []
            for row in rows:
                for key in row:
                    if key not in fieldnames:
                        fieldnames.append(key)
            with path.open("w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.DictWriter(stream, fieldnames=fieldnames or ["event_id"])
                writer.writeheader()
                for row in rows:
                    writer.writerow(row)
            media_type = "text/csv"
        return FileResponse(
            path,
            filename=f"{run_id}_fmea_filtered{suffix}",
            media_type=media_type,
            background=BackgroundTask(path.unlink, missing_ok=True),
        )

    @app.get("/runs/{run_id}/fault-traceability")
    def get_fault_traceability(run_id: str) -> dict[str, Any]:
        root = _safe_bundle(run_root, run_id)
        payload = _json(root / "results" / "dataset" / "traceability" / "fault_traceability.json")
        if payload is None:
            raise HTTPException(status_code=404, detail="fault traceability output not found")
        return {"ok": True, "run_id": run_id, "traceability": payload}

    @app.get("/runs/{run_id}/fault-traceability/{event_id}")
    def get_fault_traceability_event(run_id: str, event_id: str) -> dict[str, Any]:
        root = _safe_bundle(run_root, run_id)
        payload = _json(root / "results" / "dataset" / "traceability" / "fault_traceability.json")
        if payload is None:
            raise HTTPException(status_code=404, detail="fault traceability output not found")
        link = next(
            (item for item in payload.get("links", []) if isinstance(item, dict) and str(item.get("event_id")) == event_id),
            None,
        )
        if link is None:
            raise HTTPException(status_code=404, detail=f"traceability event not found: {event_id}")
        return {"ok": True, "run_id": run_id, "link": link}

    @app.get("/runs/{run_id}/fault-traceability/{event_id}/evidence-window")
    def get_fault_traceability_evidence_window(
        run_id: str,
        event_id: str,
        limit_per_stream: int = Query(default=500, ge=1, le=_MAX_TELEMETRY_ROWS),
    ) -> dict[str, Any]:
        root = _safe_bundle(run_root, run_id)
        payload = _json(root / "results" / "dataset" / "traceability" / "fault_traceability.json")
        if payload is None:
            raise HTTPException(status_code=404, detail="fault traceability output not found")
        link = next(
            (item for item in payload.get("links", []) if isinstance(item, dict) and str(item.get("event_id")) == event_id),
            None,
        )
        if link is None:
            raise HTTPException(status_code=404, detail=f"traceability event not found: {event_id}")
        windows: list[dict[str, Any]] = []
        for ref in link.get("telemetry_evidence") or []:
            if not isinstance(ref, dict):
                continue
            windows.append(_read_dataset_stream(
                root,
                str(ref.get("stream_id")),
                offset=0,
                limit=limit_per_stream,
                start_s=float(ref.get("window_start_s")),
                end_s=float(ref.get("window_end_s")),
            ))
        return {"ok": True, "run_id": run_id, "link": link, "windows": windows}

    @app.get("/runs/{run_id}/artifacts")
    def get_artifacts(run_id: str) -> dict[str, Any]:
        try:
            root = _safe_bundle(run_root, run_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not root.exists():
            raise HTTPException(status_code=404, detail="run not found")
        return {"ok": True, "run_id": run_id, "artifacts": _artifact_inventory(root)}

    @app.get("/runs/{run_id}/dataset")
    def get_dataset_export(run_id: str) -> dict[str, Any]:
        """Describe the dataset emitted by the same execution path as ``sat-agent run``."""

        try:
            root = _safe_bundle(run_root, run_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not root.exists():
            raise HTTPException(status_code=404, detail="run not found")
        dataset_root = root / "results" / "dataset"
        files = _dataset_inventory(dataset_root)
        if not files:
            raise HTTPException(
                status_code=404,
                detail={"reason_code": "DATASET_NOT_FOUND", "message": "该运行尚未生成可导出的数据集"},
            )
        manifest = _json(dataset_root / "manifest.json")
        return {
            "ok": True,
            "run_id": run_id,
            "dataset": {
                "available": True,
                "file_count": len(files),
                "size_bytes": sum(int(item["size_bytes"]) for item in files),
                "formats": sorted({Path(str(item["path"])).suffix.lstrip(".").lower() for item in files if Path(str(item["path"])).suffix}),
                "manifest": manifest,
                "files": files,
                "download_url": f"/runs/{run_id}/dataset/download",
            },
        }

    @app.get("/runs/{run_id}/dataset/download")
    def download_dataset_export(run_id: str):
        """Package an existing immutable Run Bundle dataset as a disposable ZIP."""

        try:
            root = _safe_bundle(run_root, run_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not root.exists():
            raise HTTPException(status_code=404, detail="run not found")
        dataset_root = root / "results" / "dataset"
        files = _dataset_inventory(dataset_root)
        if not files:
            raise HTTPException(
                status_code=404,
                detail={"reason_code": "DATASET_NOT_FOUND", "message": "该运行尚未生成可导出的数据集"},
            )
        handle, temp_name = tempfile.mkstemp(prefix=f"dataset_{run_id}_", suffix=".zip")
        os.close(handle)
        archive_path = Path(temp_name)
        try:
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                for item in files:
                    relative = Path(str(item["path"]))
                    archive.write(dataset_root / relative, arcname=(Path("dataset") / relative).as_posix())
        except Exception:
            archive_path.unlink(missing_ok=True)
            raise
        return FileResponse(
            archive_path,
            filename=f"{run_id}_dataset.zip",
            media_type="application/zip",
            background=BackgroundTask(archive_path.unlink, missing_ok=True),
        )

    @app.get("/runs/{run_id}/artifacts/{artifact_path:path}")
    def download_artifact(run_id: str, artifact_path: str):
        try:
            root = _safe_bundle(run_root, run_id)
            path = _safe_artifact(root, artifact_path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="artifact not found") from exc
        return FileResponse(path, filename=path.name)

    @app.get("/runs/{run_id}/report.html")
    def get_report_html(run_id: str):
        try:
            root = _safe_bundle(run_root, run_id)
            path = _safe_artifact(root, "results/report.html")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="report not found") from exc
        return FileResponse(path, media_type="text/html", headers={"Cache-Control": "no-store"})

    @app.get("/runs/{run_id}/report")
    def get_report(run_id: str) -> dict[str, Any]:
        try:
            root = _safe_bundle(run_root, run_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not root.exists():
            raise HTTPException(status_code=404, detail="run not found")
        run = _run_payload(root)
        return {
            "ok": True,
            "run_id": run_id,
            "status": (run.get("run_record") or {}).get("status"),
            "validation": run.get("validation_outcome"),
            "claim_report": run.get("claim_report"),
            "summary": run.get("summary"),
            "metrics": run.get("metrics"),
            "events": run.get("events"),
            "assertions": run.get("assertions"),
            "plot_manifest": run.get("plot_manifest"),
            "integrity": run.get("integrity"),
        }

    return app


__all__ = ["API_VERSION", "WEB_WORKBENCH_VERSION", "create_app"]
