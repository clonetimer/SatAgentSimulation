#!/usr/bin/env python3
"""Fresh-process worker for the official Basilisk MonteCarloController.

This file is executed directly rather than with ``python -m`` so the spawn
start method and native thread limits are established before importing the
``sat_sim`` package or Basilisk.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import traceback
from pathlib import Path
from typing import Any


def _configure_process() -> dict[str, str]:
    limits: dict[str, str] = {}
    for name in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        value = os.environ.get(name, "1")
        os.environ[name] = value
        limits[name] = value
    mp.set_start_method("spawn", force=True)
    worker_dir = Path(__file__).resolve().parent
    # When this file is executed directly, Python prepends the worker directory
    # to sys.path. That directory contains ``statistics.py`` and can shadow the
    # standard-library module imported by scientific dependencies. Remove only
    # that script-directory entry, then prepend the governed project roots.
    filtered: list[str] = []
    for entry in sys.path:
        try:
            resolved = Path(entry or os.getcwd()).resolve()
        except OSError:
            filtered.append(entry)
            continue
        if resolved != worker_dir:
            filtered.append(entry)
    sys.path[:] = filtered

    source_root = Path(__file__).resolve().parents[2]
    project_root = source_root.parent
    for entry in (str(project_root), str(source_root)):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    return limits


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    args = parser.parse_args()
    limits = _configure_process()
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        if request.get("schema_version") != "sat-sim.basilisk-mc-worker-request.v1":
            raise ValueError("unsupported Basilisk Monte Carlo worker request schema")
        from sat_sim.experiments.basilisk_mc_adapter import (
            _execute_basilisk_controller_variants_in_process,
        )

        report = _execute_basilisk_controller_variants_in_process(
            request.get("base_task_spec") or {},
            request.get("variants") or [],
            archive_dir=request.get("archive_dir"),
            thread_count=int(request.get("thread_count") or 1),
        )
        _write(args.response, {
            "schema_version": "sat-sim.basilisk-mc-worker-response.v1",
            "status": "PASS",
            "worker_pid": os.getpid(),
            "multiprocessing_start_method": mp.get_start_method(),
            "native_thread_limits": limits,
            "report": report,
        })
        return 0
    except BaseException as exc:  # pragma: no cover - process boundary
        _write(args.response, {
            "schema_version": "sat-sim.basilisk-mc-worker-response.v1",
            "status": "FAIL",
            "worker_pid": os.getpid(),
            "multiprocessing_start_method": mp.get_start_method(allow_none=True),
            "native_thread_limits": limits,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "traceback": traceback.format_exc(),
        })
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
