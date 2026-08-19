"""Long-lived worker and process-recycling reliability soak tests.

The controller is standard-library only and launches fresh Python subprocesses.
Each worker executes a sequence of real TaskSpec runs in one interpreter, which
exposes memory, file-descriptor, thread, registry, and native-runtime lifecycle
issues that one-process-per-task functional tests cannot detect.
"""
from __future__ import annotations

import gc
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

SOAK_SCHEMA_VERSION = "sat-sim.reliability-soak.v1"
WORKER_SCHEMA_VERSION = "sat-sim.reliability-soak-worker.v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _content_sha(value: Mapping[str, Any]) -> str:
    payload = dict(value)
    payload.pop("content_sha256", None)
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _current_rss_mb() -> float | None:
    try:
        statm = Path("/proc/self/statm")
        if statm.is_file():
            resident_pages = int(statm.read_text(encoding="ascii").split()[1])
            return resident_pages * os.sysconf("SC_PAGE_SIZE") / (1024.0 * 1024.0)
    except Exception:
        pass
    try:
        import resource

        value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return value / (1024.0 * 1024.0) if sys.platform == "darwin" else value / 1024.0
    except Exception:
        return None


def _open_fd_count() -> int | None:
    try:
        path = Path("/proc/self/fd")
        return len(list(path.iterdir())) if path.is_dir() else None
    except Exception:
        return None


def _resource_snapshot(cycle: int) -> dict[str, Any]:
    return {
        "cycle": cycle,
        "rss_mb": _current_rss_mb(),
        "open_fd_count": _open_fd_count(),
        "thread_count": threading.active_count(),
    }


def _load_spec(template_path: Path) -> dict[str, Any]:
    from sat_sim.task_spec import load_task_spec

    return deepcopy(load_task_spec(template_path).data)


def _prepare_cycle_spec(base: Mapping[str, Any], *, run_id: str, duration_s: float, sample_s: float, output_root: Path) -> dict[str, Any]:
    spec = deepcopy(dict(base))
    if isinstance(spec.get("task"), dict):
        spec["task"]["id"] = run_id
    else:
        spec["task_id"] = run_id
    simulation = spec.setdefault("simulation", {})
    simulation["duration_s"] = float(duration_s)
    simulation["sample_s"] = float(sample_s)
    outputs = spec.setdefault("outputs", {})
    outputs["output_root"] = str(output_root)
    return spec


def execute_soak_worker(request: Mapping[str, Any]) -> dict[str, Any]:
    from sat_sim.task_compiler import compile_task_spec
    from sat_sim.unified_execution import execute_compiled_task, reset_execution_composition_root_for_tests

    worker_id = str(request["worker_id"])
    template_path = Path(str(request["template_path"])).resolve()
    cycles = int(request["cycles"])
    duration_s = float(request.get("duration_s", 1.0))
    sample_s = float(request.get("sample_s", 1.0))
    sample_interval = max(1, int(request.get("resource_sample_interval", 10)))
    cleanup_interval = max(1, int(request.get("cleanup_interval", 10)))
    write_dataset = bool(request.get("write_dataset", False))
    output_root = Path(str(request["output_root"])).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    started = _utc_now()
    wall_started = time.perf_counter()
    timings: list[float] = []
    resources: list[dict[str, Any]] = [_resource_snapshot(0)]
    failures: list[dict[str, Any]] = []
    routes: dict[str, int] = {}
    reset_execution_composition_root_for_tests()
    base = _load_spec(template_path)

    for cycle in range(1, cycles + 1):
        cycle_root = output_root / f"cycle_{cycle:06d}"
        spec = _prepare_cycle_spec(
            base,
            run_id=f"{worker_id}-cycle-{cycle:06d}",
            duration_s=duration_s,
            sample_s=sample_s,
            output_root=cycle_root,
        )
        started_cycle = time.perf_counter()
        try:
            compiled = compile_task_spec(spec)
            result = execute_compiled_task(compiled, task_spec=spec, write_dataset=write_dataset)
            unified = dict(result.runtime_metadata.get("unified_execution") or {})
            adapter_key = str(unified.get("adapter_key") or "unknown")
            routes[adapter_key] = routes.get(adapter_key, 0) + 1
            if unified.get("legacy_mode") is not False or unified.get("legacy_bridge_called") is not False:
                raise RuntimeError(f"non-legacy route invariant failed: {unified}")
        except BaseException as exc:
            failures.append({
                "cycle": cycle,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "traceback": traceback.format_exc(),
            })
        finally:
            timings.append(time.perf_counter() - started_cycle)
            if cycle_root.exists():
                shutil.rmtree(cycle_root, ignore_errors=True)
        if cycle % cleanup_interval == 0:
            gc.collect()
        if cycle % sample_interval == 0 or cycle == cycles:
            resources.append(_resource_snapshot(cycle))
        if failures and bool(request.get("fail_fast", True)):
            break

    gc.collect()
    resources.append(_resource_snapshot(len(timings)))
    first = resources[0]
    last = resources[-1]

    def delta(name: str) -> float | int | None:
        if first.get(name) is None or last.get(name) is None:
            return None
        return last[name] - first[name]

    payload: dict[str, Any] = {
        "schema_version": WORKER_SCHEMA_VERSION,
        "status": "PASS" if not failures and len(timings) == cycles else "FAIL",
        "worker_id": worker_id,
        "workload_id": str(request["workload_id"]),
        "started_at": started,
        "finished_at": _utc_now(),
        "requested_cycles": cycles,
        "completed_cycles": len(timings),
        "successful_cycles": len(timings) - len(failures),
        "failure_count": len(failures),
        "failures": failures,
        "routes": routes,
        "wall_time_s": time.perf_counter() - wall_started,
        "cycle_time_s": {
            "min": min(timings) if timings else None,
            "max": max(timings) if timings else None,
            "mean": sum(timings) / len(timings) if timings else None,
            "first": timings[0] if timings else None,
            "last": timings[-1] if timings else None,
        },
        "resource_samples": resources,
        "resource_delta": {
            "rss_mb": delta("rss_mb"),
            "open_fd_count": delta("open_fd_count"),
            "thread_count": delta("thread_count"),
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "pid": os.getpid(),
            "multiprocessing_start_method": __import__("multiprocessing").get_start_method(allow_none=True),
        },
    }
    payload["content_sha256"] = _content_sha(payload)
    return payload


def run_worker_file(request_path: Path, result_path: Path) -> int:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    result = execute_soak_worker(request)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if result["status"] == "PASS" else 1


def _project_root(plan_path: Path) -> Path:
    for candidate in (plan_path.parent, *plan_path.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return Path.cwd().resolve()


def _worker_environment(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        env[name] = "1"
    entries = [str(root / "src"), str(root)]
    if env.get("PYTHONPATH"):
        entries.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(entries)
    return env


def _valid_existing(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if payload.get("content_sha256") == _content_sha(payload) else None


def run_soak_plan(*, plan_path: Path, output_dir: Path, script_path: Path) -> dict[str, Any]:
    plan_path = plan_path.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    root = _project_root(plan_path)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    workers_dir = output_dir / "workers"
    workers_dir.mkdir(exist_ok=True)
    pending: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    for workload in plan.get("workloads") or []:
        template = Path(str(workload["template"]))
        if not template.is_absolute():
            template = root / template
        worker_count = int(workload["worker_count"])
        for index in range(1, worker_count + 1):
            worker_id = f"{workload['workload_id']}-worker-{index:03d}"
            result_path = workers_dir / f"{worker_id}.json"
            existing = _valid_existing(result_path)
            if existing is not None:
                results.append(existing)
                continue
            request = {
                "schema_version": "sat-sim.reliability-soak-request.v1",
                "worker_id": worker_id,
                "workload_id": workload["workload_id"],
                "template_path": str(template.resolve()),
                "cycles": int(workload["cycles_per_worker"]),
                "duration_s": float(workload.get("duration_s", 1.0)),
                "sample_s": float(workload.get("sample_s", 1.0)),
                "resource_sample_interval": int(workload.get("resource_sample_interval", 10)),
                "cleanup_interval": int(workload.get("cleanup_interval", 10)),
                "write_dataset": bool(workload.get("write_dataset", False)),
                "fail_fast": True,
                "output_root": str((output_dir / "runtime" / worker_id).resolve()),
            }
            request_path = workers_dir / f"{worker_id}.request.json"
            request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            pending.append({
                "workload": workload,
                "worker_id": worker_id,
                "request_path": request_path,
                "result_path": result_path,
                "stdout_path": workers_dir / f"{worker_id}.stdout.log",
                "stderr_path": workers_dir / f"{worker_id}.stderr.log",
            })

    # Launch at most the highest requested concurrency; per-workload limits are
    # applied by selecting only entries whose workload has available slots.
    running: list[dict[str, Any]] = []
    workload_running: dict[str, int] = {}
    timeout_s = float(plan.get("worker_timeout_s", 1800.0))
    env = _worker_environment(root)
    while pending or running:
        launched = True
        while launched:
            launched = False
            for item in list(pending):
                workload_id = str(item["workload"]["workload_id"])
                concurrency = int(item["workload"].get("concurrency", 1))
                if workload_running.get(workload_id, 0) >= concurrency:
                    continue
                stdout_handle = item["stdout_path"].open("w", encoding="utf-8")
                stderr_handle = item["stderr_path"].open("w", encoding="utf-8")
                process = subprocess.Popen(
                    [
                        sys.executable,
                        str(script_path.resolve()),
                        "--worker-request", str(item["request_path"]),
                        "--worker-result", str(item["result_path"]),
                    ],
                    cwd=root,
                    env=env,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    text=True,
                )
                item.update({
                    "process": process,
                    "stdout_handle": stdout_handle,
                    "stderr_handle": stderr_handle,
                    "started_monotonic": time.monotonic(),
                })
                running.append(item)
                pending.remove(item)
                workload_running[workload_id] = workload_running.get(workload_id, 0) + 1
                launched = True
        if not running:
            continue
        time.sleep(0.1)
        for item in list(running):
            process = item["process"]
            return_code = process.poll()
            elapsed = time.monotonic() - item["started_monotonic"]
            if return_code is None and elapsed <= timeout_s:
                continue
            if return_code is None:
                process.kill()
                process.wait(timeout=10)
            item["stdout_handle"].close()
            item["stderr_handle"].close()
            workload_id = str(item["workload"]["workload_id"])
            workload_running[workload_id] -= 1
            running.remove(item)
            payload = _valid_existing(item["result_path"])
            if payload is None:
                payload = {
                    "schema_version": WORKER_SCHEMA_VERSION,
                    "status": "FAIL",
                    "worker_id": item["worker_id"],
                    "workload_id": workload_id,
                    "requested_cycles": int(item["workload"]["cycles_per_worker"]),
                    "completed_cycles": 0,
                    "successful_cycles": 0,
                    "failure_count": 1,
                    "failures": [{"error_type": "WorkerTimeout" if elapsed > timeout_s else "WorkerExit", "error_message": f"return_code={return_code} elapsed={elapsed}"}],
                    "resource_delta": {},
                    "routes": {},
                }
                payload["content_sha256"] = _content_sha(payload)
                item["result_path"].write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            results.append(payload)

    workloads: list[dict[str, Any]] = []
    guardrails = dict(plan.get("guardrails") or {})
    findings: list[dict[str, Any]] = []
    for workload in plan.get("workloads") or []:
        rows = [row for row in results if row.get("workload_id") == workload["workload_id"]]
        requested = int(workload["worker_count"]) * int(workload["cycles_per_worker"])
        successful = sum(int(row.get("successful_cycles") or 0) for row in rows)
        rss_deltas = [float(row["resource_delta"]["rss_mb"]) for row in rows if row.get("resource_delta", {}).get("rss_mb") is not None]
        fd_deltas = [int(row["resource_delta"]["open_fd_count"]) for row in rows if row.get("resource_delta", {}).get("open_fd_count") is not None]
        thread_deltas = [int(row["resource_delta"]["thread_count"]) for row in rows if row.get("resource_delta", {}).get("thread_count") is not None]
        summary = {
            "workload_id": workload["workload_id"],
            "requested_cycles": requested,
            "successful_cycles": successful,
            "success_rate": successful / requested if requested else 0.0,
            "worker_count": len(rows),
            "worker_pass_count": sum(1 for row in rows if row.get("status") == "PASS"),
            "max_rss_growth_mb": max(rss_deltas) if rss_deltas else None,
            "max_fd_growth": max(fd_deltas) if fd_deltas else None,
            "max_thread_growth": max(thread_deltas) if thread_deltas else None,
            "routes": sorted({key for row in rows for key in (row.get("routes") or {})}),
        }
        workloads.append(summary)
        if summary["success_rate"] < float(guardrails.get("minimum_success_rate", 1.0)):
            findings.append({"code": "SOAK_SUCCESS_RATE_BELOW_MINIMUM", "workload_id": workload["workload_id"], "actual": summary["success_rate"]})
        if summary["max_rss_growth_mb"] is not None and summary["max_rss_growth_mb"] > float(guardrails.get("max_worker_rss_growth_mb", 128.0)):
            findings.append({"code": "SOAK_RSS_GROWTH_EXCEEDED", "workload_id": workload["workload_id"], "actual": summary["max_rss_growth_mb"]})
        if summary["max_fd_growth"] is not None and summary["max_fd_growth"] > int(guardrails.get("max_fd_growth", 4)):
            findings.append({"code": "SOAK_FD_GROWTH_EXCEEDED", "workload_id": workload["workload_id"], "actual": summary["max_fd_growth"]})
        if summary["max_thread_growth"] is not None and summary["max_thread_growth"] > int(guardrails.get("max_thread_growth", 2)):
            findings.append({"code": "SOAK_THREAD_GROWTH_EXCEEDED", "workload_id": workload["workload_id"], "actual": summary["max_thread_growth"]})

    report = {
        "schema_version": SOAK_SCHEMA_VERSION,
        "benchmark_id": plan.get("benchmark_id"),
        "generated_at": _utc_now(),
        "status": "PASS" if not findings else "FAIL",
        "summary": {
            "requested_cycles": sum(item["requested_cycles"] for item in workloads),
            "successful_cycles": sum(item["successful_cycles"] for item in workloads),
            "worker_count": len(results),
            "worker_pass_count": sum(1 for row in results if row.get("status") == "PASS"),
            "finding_count": len(findings),
        },
        "workloads": workloads,
        "guardrails": guardrails,
        "findings": findings,
        "boundary": {
            "validated": "Repeated real TaskSpec execution in long-lived workers plus scheduled worker recycling.",
            "not_validated": "24-hour wall-clock endurance, host reboot recovery, disk-full recovery, and multi-node failover remain separate gates.",
        },
    }
    report["content_sha256"] = _content_sha(report)
    (output_dir / "reliability_soak_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shutil.rmtree(output_dir / "runtime", ignore_errors=True)
    return report


__all__ = ["execute_soak_worker", "run_worker_file", "run_soak_plan"]
