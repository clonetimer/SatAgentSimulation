"""Formal, reproducible capacity benchmarking for sat-sim execution paths.

The benchmark runner deliberately executes every sample in a fresh child process.
This isolates Basilisk and multiprocessing lifetime effects, gives each run a
meaningful peak-RSS measurement, and prevents one failed run from corrupting the
remaining capacity matrix.
"""
from __future__ import annotations

import csv
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import time
import traceback
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

CAPACITY_SCHEMA_VERSION = "sat-sim.capacity.v1"
WORKER_RESULT_SCHEMA_VERSION = "sat-sim.capacity.worker-result.v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return ordered[low]
    weight = rank - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def summarize_samples(values: Iterable[float]) -> dict[str, float | int | None]:
    materialized = [float(value) for value in values]
    if not materialized:
        return {"count": 0, "min": None, "max": None, "mean": None, "p50": None, "p95": None}
    return {
        "count": len(materialized),
        "min": min(materialized),
        "max": max(materialized),
        "mean": sum(materialized) / len(materialized),
        "p50": _percentile(materialized, 0.50),
        "p95": _percentile(materialized, 0.95),
    }




def _project_root_for_plan(plan_path: Path) -> Path:
    resolved = plan_path.resolve()
    for parent in (resolved.parent, *resolved.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    cwd = Path.cwd().resolve()
    if (cwd / "pyproject.toml").is_file():
        return cwd
    return resolved.parent


def _resolve_template_path(plan_path: Path, template_value: str) -> Path:
    candidate = Path(template_value)
    if candidate.is_absolute():
        return candidate.resolve()
    return (_project_root_for_plan(plan_path) / candidate).resolve()


def _directory_size(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    total = 0
    count = 0
    for item in path.rglob("*"):
        if item.is_file():
            count += 1
            total += item.stat().st_size
    return total, count


def _peak_rss_mb() -> float | None:
    try:
        import resource

        value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        # Linux reports KiB, macOS reports bytes.
        if sys.platform == "darwin":
            return value / (1024.0 * 1024.0)
        return value / 1024.0
    except Exception:
        try:
            import psutil  # type: ignore

            return float(psutil.Process().memory_info().rss) / (1024.0 * 1024.0)
        except Exception:
            return None


def _task_id(spec: Mapping[str, Any]) -> str:
    task = spec.get("task")
    if isinstance(task, Mapping):
        return str(task.get("id") or "capacity-task")
    return str(spec.get("task_id") or "capacity-task")


def _capability_id(spec: Mapping[str, Any]) -> str | None:
    model = spec.get("model")
    if isinstance(model, Mapping) and model.get("capability_id"):
        return str(model["capability_id"])
    value = spec.get("capability_id")
    return str(value) if value else None


def _event_count(spec: Mapping[str, Any]) -> int:
    events = spec.get("events") if isinstance(spec.get("events"), Mapping) else {}
    modifiers = (
        spec.get("modifiers") if isinstance(spec.get("modifiers"), Mapping) else {}
    )
    return sum(
        len(rows)
        for source in (spec, events, modifiers)
        for category in ("faults", "degradations", "constraints")
        if isinstance((rows := source.get(category)), list)
    )


def _expand_event_repetitions(spec: dict[str, Any], repetitions: int) -> None:
    if repetitions < 1:
        raise ValueError("event_repetitions must be at least one")
    source_rows: list[dict[str, Any]] | None = None
    for source in (
        spec,
        spec.get("events") if isinstance(spec.get("events"), dict) else {},
        spec.get("modifiers") if isinstance(spec.get("modifiers"), dict) else {},
    ):
        for category in ("faults", "degradations", "constraints"):
            rows = source.get(category)
            if isinstance(rows, list) and rows:
                source_rows = rows
                break
        if source_rows is not None:
            break
    if source_rows is None:
        raise ValueError("event_repetitions requires a template with at least one event")
    base = deepcopy(source_rows[0])
    duration_s = float((spec.get("simulation") or {}).get("duration_s") or 1.0)
    spacing_s = duration_s / repetitions
    expanded = []
    for index in range(repetitions):
        event = deepcopy(base)
        for id_key in ("fault_id", "degradation_id", "constraint_id", "modifier_id"):
            if id_key in event:
                event[id_key] = f"capacity_dense_{index + 1:03d}"
        time_key = "start_s" if "start_s" in event else "onset_time_s"
        event[time_key] = index * spacing_s
        if "end_s" in event:
            event["end_s"] = min(duration_s, (index + 0.5) * spacing_s)
        if "duration_s" in event:
            event["duration_s"] = max(spacing_s * 0.5, 1e-6)
        expanded.append(event)
    source_rows[:] = expanded


def _prepare_spec(request: Mapping[str, Any]) -> tuple[dict[str, Any], Path]:
    # Keep controller mode stdlib-only. Production imports happen inside the
    # isolated worker process so the controller never forks after native
    # simulation libraries or BLAS thread pools have been initialized.
    from sat_sim.task_spec import load_task_spec

    template = Path(str(request["template_path"])).resolve()
    spec = deepcopy(load_task_spec(template).data)
    run_id = str(request["run_id"])
    simulation = spec.setdefault("simulation", {})
    simulation["duration_s"] = float(request["duration_s"])
    simulation["sample_s"] = float(request["sample_s"])
    if request.get("step_s") is not None:
        simulation["step_s"] = float(request["step_s"])
    if request.get("event_repetitions") is not None:
        _expand_event_repetitions(spec, int(request["event_repetitions"]))

    if isinstance(spec.get("task"), dict):
        spec["task"]["id"] = run_id
    else:
        spec["task_id"] = run_id

    overrides = request.get("parameter_overrides") or {}
    if isinstance(spec.get("parameters"), dict) and isinstance(spec["parameters"].get("values"), dict):
        spec["parameters"]["values"].update(overrides)
    else:
        spec.setdefault("parameters", {}).update(overrides)

    output_root = Path(str(request["output_root"])).resolve()
    spec.setdefault("outputs", {})["output_root"] = str(output_root)
    return spec, output_root


def execute_worker_request(request: Mapping[str, Any]) -> dict[str, Any]:
    from sat_sim.task_compiler import compile_task_spec
    from sat_sim.unified_execution import execute_compiled_task, reset_execution_composition_root_for_tests

    started_at = _utc_now()
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    output_root: Path | None = None
    result_payload: dict[str, Any]
    try:
        spec, output_root = _prepare_spec(request)
        reset_execution_composition_root_for_tests()
        compiled = compile_task_spec(spec)
        result = execute_compiled_task(
            compiled,
            task_spec=spec,
            write_dataset=bool(request.get("write_dataset", True)),
        )
        wall_time = time.perf_counter() - wall_start
        cpu_time = time.process_time() - cpu_start
        output_bytes, output_files = _directory_size(output_root)
        unified = dict(result.runtime_metadata.get("unified_execution") or {})
        model_assets = dict(result.runtime_metadata.get("model_asset_execution") or {})
        simulated_duration = float((spec.get("simulation") or {}).get("duration_s") or 0.0)
        result_payload = {
            "schema_version": WORKER_RESULT_SCHEMA_VERSION,
            "status": "PASS",
            "run_id": str(request["run_id"]),
            "workload_id": str(request["workload_id"]),
            "started_at": started_at,
            "finished_at": _utc_now(),
            "task_id": _task_id(spec),
            "capability_id": _capability_id(spec),
            "template_path": str(Path(str(request["template_path"])).resolve()),
            "duration_s": simulated_duration,
            "sample_s": float((spec.get("simulation") or {}).get("sample_s") or 0.0),
            "event_count": _event_count(spec),
            "wall_time_s": wall_time,
            "cpu_time_s": cpu_time,
            "peak_rss_mb": _peak_rss_mb(),
            "real_time_factor": (simulated_duration / wall_time) if wall_time > 0 else None,
            "trace_rows": len(result.trace_rows),
            "output_bytes": output_bytes,
            "output_files": output_files,
            "adapter_key": unified.get("adapter_key"),
            "legacy_mode": unified.get("legacy_mode"),
            "legacy_bridge_called": (
                unified.get("legacy_bridge_called")
                if unified.get("legacy_bridge_called") is not None
                else model_assets.get("legacy_bridge_called")
            ),
            "execution_plan_sha256": unified.get("execution_plan_sha256"),
            "model_graph_sha256": model_assets.get("model_graph_sha256") or model_assets.get("graph_sha256"),
            "parameter_set_sha256": model_assets.get("parameter_set_sha256"),
            "binding_set_sha256": model_assets.get("binding_set_sha256"),
            "capability_projection_sha256": model_assets.get("capability_projection_sha256"),
            "summary_status": result.summary.get("status") if isinstance(result.summary, dict) else None,
            "error_type": None,
            "error_message": None,
            "environment": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "pid": os.getpid(),
            },
        }
    except Exception as exc:  # pragma: no cover - exercised through subprocess integration
        wall_time = time.perf_counter() - wall_start
        cpu_time = time.process_time() - cpu_start
        output_bytes, output_files = _directory_size(output_root) if output_root else (0, 0)
        result_payload = {
            "schema_version": WORKER_RESULT_SCHEMA_VERSION,
            "status": "FAIL",
            "run_id": str(request.get("run_id") or "unknown"),
            "workload_id": str(request.get("workload_id") or "unknown"),
            "started_at": started_at,
            "finished_at": _utc_now(),
            "wall_time_s": wall_time,
            "cpu_time_s": cpu_time,
            "peak_rss_mb": _peak_rss_mb(),
            "trace_rows": 0,
            "output_bytes": output_bytes,
            "output_files": output_files,
            "adapter_key": None,
            "legacy_mode": None,
            "legacy_bridge_called": None,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "traceback": traceback.format_exc(),
            "environment": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "pid": os.getpid(),
            },
        }
    result_payload["content_sha256"] = _sha256_text(_canonical_json({k: v for k, v in result_payload.items() if k != "content_sha256"}))
    return result_payload


def run_worker_file(request_path: Path, result_path: Path) -> int:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    result = execute_worker_request(request)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result.get("status") == "PASS" else 1


@dataclass(frozen=True)
class _ProcessRun:
    process: subprocess.Popen[str]
    request_path: Path
    result_path: Path
    stdout_path: Path
    stderr_path: Path
    stdout_handle: Any
    stderr_handle: Any


def _launch_worker(*, script_path: Path, request: Mapping[str, Any], work_dir: Path, env: Mapping[str, str]) -> _ProcessRun:
    run_id = str(request["run_id"])
    request_path = work_dir / "requests" / f"{run_id}.json"
    result_path = work_dir / "worker_results" / f"{run_id}.json"
    stdout_path = work_dir / "logs" / f"{run_id}.stdout.log"
    stderr_path = work_dir / "logs" / f"{run_id}.stderr.log"
    for path in (request_path.parent, result_path.parent, stdout_path.parent):
        path.mkdir(parents=True, exist_ok=True)
    request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    stdout_handle = stdout_path.open("w", encoding="utf-8")
    stderr_handle = stderr_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(script_path), "--worker-request", str(request_path), "--worker-result", str(result_path)],
        cwd=str(script_path.parent.parent),
        env=dict(env),
        stdout=stdout_handle,
        stderr=stderr_handle,
        text=True,
    )
    return _ProcessRun(process, request_path, result_path, stdout_path, stderr_path, stdout_handle, stderr_handle)


def _collect_process(run: _ProcessRun, timeout_s: float) -> dict[str, Any]:
    try:
        return_code = run.process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        run.process.kill()
        run.process.wait(timeout=30)
        return_code = 124
    finally:
        run.stdout_handle.close()
        run.stderr_handle.close()
    if run.result_path.exists():
        payload = json.loads(run.result_path.read_text(encoding="utf-8"))
    else:
        payload = {
            "schema_version": WORKER_RESULT_SCHEMA_VERSION,
            "status": "FAIL",
            "run_id": run.request_path.stem,
            "error_type": "WorkerResultMissing",
            "error_message": f"worker return code {return_code}; result file missing",
        }
    payload["worker_return_code"] = return_code
    payload["stdout_log"] = str(run.stdout_path)
    payload["stderr_log"] = str(run.stderr_path)
    if return_code != 0 and payload.get("status") == "PASS":
        payload["status"] = "FAIL"
        payload["error_type"] = "WorkerReturnCode"
        payload["error_message"] = f"worker returned {return_code} after producing PASS payload"
    return payload


def _aggregate_workload(workload: Mapping[str, Any], results: Sequence[Mapping[str, Any]], wave_elapsed: Sequence[float]) -> dict[str, Any]:
    passed = [row for row in results if row.get("status") == "PASS"]
    non_legacy = [
        row for row in passed
        if row.get("legacy_mode") is False and row.get("legacy_bridge_called") is False
    ]
    jobs = len(results)
    return {
        "workload_id": workload["workload_id"],
        "dimension": workload.get("dimension"),
        "description": workload.get("description"),
        "template": workload["template"],
        "duration_s": workload["duration_s"],
        "sample_s": workload["sample_s"],
        "event_repetitions": workload.get("event_repetitions"),
        "concurrency": workload.get("concurrency", 1),
        "waves": workload.get("waves", 1),
        "job_count": jobs,
        "pass_count": len(passed),
        "success_rate": (len(passed) / jobs) if jobs else 0.0,
        "non_legacy_rate": (len(non_legacy) / len(passed)) if passed else 0.0,
        "wall_time_s": summarize_samples(row["wall_time_s"] for row in passed if row.get("wall_time_s") is not None),
        "cpu_time_s": summarize_samples(row["cpu_time_s"] for row in passed if row.get("cpu_time_s") is not None),
        "peak_rss_mb": summarize_samples(row["peak_rss_mb"] for row in passed if row.get("peak_rss_mb") is not None),
        "real_time_factor": summarize_samples(row["real_time_factor"] for row in passed if row.get("real_time_factor") is not None),
        "trace_rows": summarize_samples(row["trace_rows"] for row in passed if row.get("trace_rows") is not None),
        "output_bytes": summarize_samples(row["output_bytes"] for row in passed if row.get("output_bytes") is not None),
        "wave_elapsed_s": summarize_samples(wave_elapsed),
        "throughput_jobs_s": summarize_samples(
            float(workload.get("concurrency", 1)) / elapsed for elapsed in wave_elapsed if elapsed > 0
        ),
    }


def _evaluate_guardrails(plan: Mapping[str, Any], results: Sequence[Mapping[str, Any]], aggregates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    guardrails = dict(plan.get("guardrails") or {})
    findings: list[dict[str, Any]] = []
    minimum_success = float(guardrails.get("minimum_success_rate", 1.0))
    require_non_legacy = bool(guardrails.get("require_non_legacy", True))
    max_peak = guardrails.get("max_peak_rss_mb")
    max_wall = guardrails.get("max_worker_wall_time_s")
    for aggregate in aggregates:
        if float(aggregate["success_rate"]) < minimum_success:
            findings.append({"code": "CAPACITY_SUCCESS_RATE_BELOW_MINIMUM", "workload_id": aggregate["workload_id"], "actual": aggregate["success_rate"], "required": minimum_success})
        if require_non_legacy and float(aggregate["non_legacy_rate"]) < 1.0:
            findings.append({"code": "CAPACITY_NON_LEGACY_ROUTE_INCOMPLETE", "workload_id": aggregate["workload_id"], "actual": aggregate["non_legacy_rate"], "required": 1.0})
    if max_peak is not None:
        for row in results:
            if row.get("status") == "PASS" and row.get("peak_rss_mb") is not None and float(row["peak_rss_mb"]) > float(max_peak):
                findings.append({"code": "CAPACITY_PEAK_RSS_EXCEEDED", "run_id": row.get("run_id"), "actual": row["peak_rss_mb"], "required_max": max_peak})
    if max_wall is not None:
        for row in results:
            if row.get("status") == "PASS" and row.get("wall_time_s") is not None and float(row["wall_time_s"]) > float(max_wall):
                findings.append({"code": "CAPACITY_WORKER_WALL_TIME_EXCEEDED", "run_id": row.get("run_id"), "actual": row["wall_time_s"], "required_max": max_wall})
    return findings


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    columns = [
        "workload_id", "run_id", "status", "capability_id", "duration_s", "sample_s",
        "wall_time_s", "cpu_time_s", "peak_rss_mb", "real_time_factor", "trace_rows",
        "output_bytes", "adapter_key", "legacy_mode", "legacy_bridge_called", "worker_return_code",
        "error_type", "error_message",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_markdown(path: Path, report: Mapping[str, Any]) -> None:
    lines = [
        "# 卫星仿真 Agent 平台正式容量基准",
        "",
        f"- 状态：**{report['status']}**",
        f"- 基准版本：`{report['benchmark_id']}`",
        f"- 生成时间：`{report['generated_at']}`",
        f"- 总任务：{report['summary']['job_count']}",
        f"- 成功：{report['summary']['pass_count']}",
        f"- 失败：{report['summary']['fail_count']}",
        "",
        "## 已验证容量边界",
        "",
    ]
    for key, value in report["tested_envelope"].items():
        lines.append(f"- {key}: `{value}`")
    lines.extend([
        "",
        "## 工作负载结果",
        "",
        "| 工作负载 | 维度 | 并发 | 任务数 | 成功率 | P50 墙钟(s) | P95 墙钟(s) | 峰值内存最大(MiB) | P50 实时倍率 | P50 吞吐(job/s) |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for row in report["workloads"]:
        lines.append(
            f"| {row['workload_id']} | {row.get('dimension') or ''} | {row['concurrency']} | {row['job_count']} | "
            f"{row['success_rate']:.3f} | {row['wall_time_s']['p50'] or 0:.4f} | {row['wall_time_s']['p95'] or 0:.4f} | "
            f"{row['peak_rss_mb']['max'] or 0:.2f} | {row['real_time_factor']['p50'] or 0:.2f} | {row['throughput_jobs_s']['p50'] or 0:.3f} |"
        )
    lines.extend([
        "",
        "## 门禁发现",
        "",
    ])
    if report["findings"]:
        for finding in report["findings"]:
            lines.append(f"- `{finding['code']}`：`{json.dumps(finding, ensure_ascii=False)}`")
    else:
        lines.append("- 无。所有配置的容量门禁均通过。")
    lines.extend([
        "",
        "## 明确未验证范围",
        "",
    ])
    for item in report.get("not_validated", []):
        lines.append(f"- {item}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_capacity_plan(*, plan_path: Path, output_dir: Path, script_path: Path, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("schema_version") != CAPACITY_SCHEMA_VERSION:
        raise ValueError(f"unsupported capacity schema: {plan.get('schema_version')!r}")
    workloads = plan.get("workloads")
    if not isinstance(workloads, list) or not workloads:
        raise ValueError("capacity plan must contain non-empty workloads")
    output_dir.mkdir(parents=True, exist_ok=True)
    worker_root = output_dir / "work"
    worker_env = dict(os.environ if env is None else env)
    worker_env.setdefault("PYTHONHASHSEED", "0")
    # Capacity workers execute the repository script in a fresh interpreter.
    # Ensure source-tree executions remain importable without requiring an
    # editable installation; installed-wheel executions keep their existing
    # environment unchanged apart from the harmless project src prefix.
    project_root = _project_root_for_plan(plan_path)
    source_root = project_root / "src"
    python_path_entries = [str(source_root), str(project_root)]
    existing_python_path = worker_env.get("PYTHONPATH")
    if existing_python_path:
        python_path_entries.append(existing_python_path)
    worker_env["PYTHONPATH"] = os.pathsep.join(python_path_entries)
    all_results: list[dict[str, Any]] = []
    aggregates: list[dict[str, Any]] = []
    timeout_s = float(plan.get("worker_timeout_s", 900.0))

    for workload in workloads:
        workload_id = str(workload["workload_id"])
        concurrency = int(workload.get("concurrency", 1))
        waves = int(workload.get("waves", 1))
        if concurrency < 1 or waves < 1:
            raise ValueError(f"invalid concurrency/waves for {workload_id}")
        template = _resolve_template_path(plan_path, str(workload["template"]))
        if not template.is_file():
            raise FileNotFoundError(template)
        wave_elapsed: list[float] = []
        workload_results: list[dict[str, Any]] = []
        for wave in range(waves):
            launched: list[_ProcessRun] = []
            wave_start = time.perf_counter()
            for slot in range(concurrency):
                run_id = f"{workload_id}-w{wave + 1:02d}-s{slot + 1:02d}"
                request = {
                    "schema_version": CAPACITY_SCHEMA_VERSION,
                    "workload_id": workload_id,
                    "run_id": run_id,
                    "template_path": str(template),
                    "duration_s": float(workload["duration_s"]),
                    "sample_s": float(workload["sample_s"]),
                    "step_s": workload.get("step_s"),
                    "event_repetitions": workload.get("event_repetitions"),
                    "parameter_overrides": dict(workload.get("parameter_overrides") or {}),
                    "write_dataset": bool(workload.get("write_dataset", True)),
                    "output_root": str((output_dir / "datasets" / workload_id / run_id).resolve()),
                }
                launched.append(_launch_worker(script_path=script_path, request=request, work_dir=worker_root, env=worker_env))
            wave_rows = [_collect_process(item, timeout_s) for item in launched]
            elapsed = time.perf_counter() - wave_start
            wave_elapsed.append(elapsed)
            workload_results.extend(wave_rows)
            all_results.extend(wave_rows)
            passed_count = sum(1 for row in wave_rows if row.get("status") == "PASS")
            print(
                f"[capacity] {workload_id} wave {wave + 1}/{waves}: "
                f"{passed_count}/{len(wave_rows)} PASS, {elapsed:.3f}s",
                flush=True,
            )
        aggregates.append(_aggregate_workload(workload, workload_results, wave_elapsed))

    findings = _evaluate_guardrails(plan, all_results, aggregates)
    passed = [row for row in all_results if row.get("status") == "PASS"]
    durations = [float(row["duration_s"]) for row in passed if row.get("duration_s") is not None]
    sample_periods = [float(row["sample_s"]) for row in passed if row.get("sample_s") is not None]
    trace_rows = [int(row["trace_rows"]) for row in passed if row.get("trace_rows") is not None]
    output_bytes = [int(row["output_bytes"]) for row in passed if row.get("output_bytes") is not None]
    tested_envelope = {
        "maximum_concurrency": max(int(row.get("concurrency", 1)) for row in workloads),
        "maximum_jobs_in_one_workload": max(int(row.get("concurrency", 1)) * int(row.get("waves", 1)) for row in workloads),
        "maximum_simulated_duration_s": max(durations) if durations else 0,
        "minimum_sample_period_s": min(sample_periods) if sample_periods else None,
        "maximum_events_per_run": max(
            (int(row.get("event_count") or 0) for row in passed),
            default=0,
        ),
        "maximum_trace_rows_per_run": max(trace_rows) if trace_rows else 0,
        "maximum_output_bytes_per_run": max(output_bytes) if output_bytes else 0,
        "capabilities": sorted({str(row.get("capability_id")) for row in passed if row.get("capability_id")}),
        "adapter_keys": sorted({str(row.get("adapter_key")) for row in passed if row.get("adapter_key")}),
    }
    report = {
        "schema_version": CAPACITY_SCHEMA_VERSION,
        "benchmark_id": str(plan.get("benchmark_id") or "capacity-baseline"),
        "generated_at": _utc_now(),
        "status": "PASS" if not findings else "FAIL",
        "plan_sha256": _sha256_text(_canonical_json(plan)),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor(),
            "cpu_count": os.cpu_count(),
        },
        "summary": {
            "job_count": len(all_results),
            "pass_count": len(passed),
            "fail_count": len(all_results) - len(passed),
            "success_rate": len(passed) / len(all_results) if all_results else 0.0,
        },
        "tested_envelope": tested_envelope,
        "workloads": aggregates,
        "findings": findings,
        "not_validated": list(plan.get("not_validated") or []),
        "raw_results_file": "capacity_raw_results.json",
        "csv_file": "capacity_results.csv",
    }
    report["content_sha256"] = _sha256_text(_canonical_json({k: v for k, v in report.items() if k != "content_sha256"}))
    (output_dir / "capacity_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "capacity_raw_results.json").write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "capacity_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(output_dir / "capacity_results.csv", all_results)
    _write_markdown(output_dir / "capacity_report.md", report)
    return report


def clean_capacity_work_directory(output_dir: Path) -> None:
    work = output_dir / "work"
    if work.exists():
        shutil.rmtree(work)


def _load_capacity_plan(plan_path: Path) -> dict[str, Any]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("schema_version") != CAPACITY_SCHEMA_VERSION:
        raise ValueError(f"unsupported capacity schema: {plan.get('schema_version')!r}")
    workloads = plan.get("workloads")
    if not isinstance(workloads, list) or not workloads:
        raise ValueError("capacity plan must contain non-empty workloads")
    return plan


def run_capacity_wave(
    *,
    plan_path: Path,
    output_dir: Path,
    script_path: Path,
    workload_id: str,
    wave_number: int,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Execute exactly one workload wave and persist a resumable fragment.

    Wave isolation is the preferred production mode for native-engine capacity
    testing. It bounds the number of child processes created by one controller
    and avoids cumulative native-library/process-lifetime effects.
    """
    plan = _load_capacity_plan(plan_path)
    workload = next((item for item in plan["workloads"] if str(item["workload_id"]) == workload_id), None)
    if workload is None:
        raise KeyError(f"unknown capacity workload: {workload_id}")
    waves = int(workload.get("waves", 1))
    if wave_number < 1 or wave_number > waves:
        raise ValueError(f"wave {wave_number} outside 1..{waves} for {workload_id}")
    concurrency = int(workload.get("concurrency", 1))
    if concurrency < 1:
        raise ValueError(f"invalid concurrency for {workload_id}")
    template = _resolve_template_path(plan_path, str(workload["template"]))
    if not template.is_file():
        raise FileNotFoundError(template)
    output_dir.mkdir(parents=True, exist_ok=True)
    worker_root = output_dir / "work"
    worker_env = dict(os.environ if env is None else env)
    worker_env.setdefault("PYTHONHASHSEED", "0")
    timeout_s = float(plan.get("worker_timeout_s", 900.0))
    launched: list[_ProcessRun] = []
    wave_start = time.perf_counter()
    for slot in range(concurrency):
        run_id = f"{workload_id}-w{wave_number:02d}-s{slot + 1:02d}"
        request = {
            "schema_version": CAPACITY_SCHEMA_VERSION,
            "workload_id": workload_id,
            "run_id": run_id,
            "template_path": str(template),
            "duration_s": float(workload["duration_s"]),
            "sample_s": float(workload["sample_s"]),
            "step_s": workload.get("step_s"),
            "event_repetitions": workload.get("event_repetitions"),
            "parameter_overrides": dict(workload.get("parameter_overrides") or {}),
            "write_dataset": bool(workload.get("write_dataset", True)),
            "output_root": str((output_dir / "datasets" / workload_id / run_id).resolve()),
        }
        launched.append(_launch_worker(script_path=script_path, request=request, work_dir=worker_root, env=worker_env))
    rows = [_collect_process(item, timeout_s) for item in launched]
    elapsed = time.perf_counter() - wave_start
    fragment = {
        "schema_version": CAPACITY_SCHEMA_VERSION,
        "benchmark_id": plan.get("benchmark_id"),
        "plan_sha256": _sha256_text(_canonical_json(plan)),
        "workload_id": workload_id,
        "wave_number": wave_number,
        "concurrency": concurrency,
        "elapsed_s": elapsed,
        "results": rows,
        "generated_at": _utc_now(),
    }
    fragment["content_sha256"] = _sha256_text(_canonical_json({k: v for k, v in fragment.items() if k != "content_sha256"}))
    fragment_path = output_dir / "work" / "waves" / workload_id / f"wave_{wave_number:02d}.json"
    fragment_path.parent.mkdir(parents=True, exist_ok=True)
    fragment_path.write_text(json.dumps(fragment, ensure_ascii=False, indent=2), encoding="utf-8")
    passed_count = sum(1 for row in rows if row.get("status") == "PASS")
    print(
        f"[capacity] {workload_id} wave {wave_number}/{waves}: "
        f"{passed_count}/{len(rows)} PASS, {elapsed:.3f}s",
        flush=True,
    )
    return fragment


def finalize_capacity_plan(*, plan_path: Path, output_dir: Path) -> dict[str, Any]:
    """Aggregate previously completed wave fragments into the formal report."""
    plan = _load_capacity_plan(plan_path)
    all_results: list[dict[str, Any]] = []
    aggregates: list[dict[str, Any]] = []
    missing: list[str] = []
    for workload in plan["workloads"]:
        workload_id = str(workload["workload_id"])
        workload_results: list[dict[str, Any]] = []
        wave_elapsed: list[float] = []
        for wave_number in range(1, int(workload.get("waves", 1)) + 1):
            fragment_path = output_dir / "work" / "waves" / workload_id / f"wave_{wave_number:02d}.json"
            if not fragment_path.is_file():
                missing.append(f"{workload_id}:wave_{wave_number:02d}")
                continue
            fragment = json.loads(fragment_path.read_text(encoding="utf-8"))
            expected_sha = fragment.get("content_sha256")
            actual_sha = _sha256_text(_canonical_json({k: v for k, v in fragment.items() if k != "content_sha256"}))
            if expected_sha != actual_sha:
                raise ValueError(f"capacity wave fragment hash mismatch: {fragment_path}")
            rows = list(fragment.get("results") or [])
            workload_results.extend(rows)
            all_results.extend(rows)
            wave_elapsed.append(float(fragment["elapsed_s"]))
        if workload_results:
            aggregates.append(_aggregate_workload(workload, workload_results, wave_elapsed))
    if missing:
        raise RuntimeError("capacity wave fragments missing: " + ", ".join(missing))

    findings = _evaluate_guardrails(plan, all_results, aggregates)
    passed = [row for row in all_results if row.get("status") == "PASS"]
    durations = [float(row["duration_s"]) for row in passed if row.get("duration_s") is not None]
    sample_periods = [float(row["sample_s"]) for row in passed if row.get("sample_s") is not None]
    trace_rows = [int(row["trace_rows"]) for row in passed if row.get("trace_rows") is not None]
    output_bytes = [int(row["output_bytes"]) for row in passed if row.get("output_bytes") is not None]
    tested_envelope = {
        "maximum_concurrency": max(int(row.get("concurrency", 1)) for row in plan["workloads"]),
        "maximum_jobs_in_one_workload": max(int(row.get("concurrency", 1)) * int(row.get("waves", 1)) for row in plan["workloads"]),
        "maximum_simulated_duration_s": max(durations) if durations else 0,
        "minimum_sample_period_s": min(sample_periods) if sample_periods else None,
        "maximum_events_per_run": max(
            (int(row.get("event_count") or 0) for row in all_results if row.get("status") == "PASS"),
            default=0,
        ),
        "maximum_trace_rows_per_run": max(trace_rows) if trace_rows else 0,
        "maximum_output_bytes_per_run": max(output_bytes) if output_bytes else 0,
        "capabilities": sorted({str(row.get("capability_id")) for row in passed if row.get("capability_id")}),
        "adapter_keys": sorted({str(row.get("adapter_key")) for row in passed if row.get("adapter_key")}),
    }
    report = {
        "schema_version": CAPACITY_SCHEMA_VERSION,
        "benchmark_id": str(plan.get("benchmark_id") or "capacity-baseline"),
        "generated_at": _utc_now(),
        "status": "PASS" if not findings else "FAIL",
        "execution_mode": "isolated_resumable_waves",
        "plan_sha256": _sha256_text(_canonical_json(plan)),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor(),
            "cpu_count": os.cpu_count(),
        },
        "summary": {
            "job_count": len(all_results),
            "pass_count": len(passed),
            "fail_count": len(all_results) - len(passed),
            "success_rate": len(passed) / len(all_results) if all_results else 0.0,
        },
        "tested_envelope": tested_envelope,
        "workloads": aggregates,
        "findings": findings,
        "not_validated": list(plan.get("not_validated") or []),
        "raw_results_file": "capacity_raw_results.json",
        "csv_file": "capacity_results.csv",
    }
    report["content_sha256"] = _sha256_text(_canonical_json({k: v for k, v in report.items() if k != "content_sha256"}))
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "capacity_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "capacity_raw_results.json").write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "capacity_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(output_dir / "capacity_results.csv", all_results)
    _write_markdown(output_dir / "capacity_report.md", report)
    return report
