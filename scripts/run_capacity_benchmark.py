#!/usr/bin/env python3
"""
用途：执行正式单节点容量基准，或作为隔离子进程执行单个容量样本。
参数：控制模式使用 --plan 与 --output；工作进程模式使用 --worker-request 与 --worker-result。
输出：生成容量基准 JSON、CSV、Markdown、逐任务结果和标准输出/错误日志。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType


def _load_capacity_module() -> ModuleType:
    # Normal ``import sat_sim.capacity_benchmark`` executes sat_sim/__init__.py
    # and initializes the production stack in the controller. Direct loading
    # keeps controller/wave processes stdlib-only until a worker executes.
    path = Path(__file__).resolve().parents[1] / "src" / "sat_sim" / "capacity_benchmark.py"
    module_name = "_sat_sim_capacity_benchmark_standalone"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load capacity module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _wave_sequence(plan_path: Path) -> list[tuple[str, int]]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    sequence: list[tuple[str, int]] = []
    for workload in plan.get("workloads") or []:
        workload_id = str(workload["workload_id"])
        for wave in range(1, int(workload.get("waves", 1)) + 1):
            sequence.append((workload_id, wave))
    if not sequence:
        raise ValueError("capacity plan has no workload waves")
    return sequence


def _exec_resume(args: argparse.Namespace, next_index: int) -> None:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--plan", str(args.plan.resolve()),
        "--output", str(args.output.resolve()),
        "--resume-index", str(next_index),
    ]
    os.execve(sys.executable, command, dict(os.environ))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker-request", type=Path)
    parser.add_argument("--worker-result", type=Path)
    parser.add_argument("--workload-id")
    parser.add_argument("--wave", type=int)
    parser.add_argument("--finalize-only", action="store_true")
    parser.add_argument("--resume-index", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.worker_request or args.worker_result:
        if not args.worker_request or not args.worker_result:
            parser.error("--worker-request and --worker-result must be supplied together")
        capacity = _load_capacity_module()
        return capacity.run_worker_file(args.worker_request, args.worker_result)

    if not args.plan or not args.output:
        parser.error("--plan and --output are required in controller mode")

    capacity = _load_capacity_module()
    if args.finalize_only:
        report = capacity.finalize_capacity_plan(plan_path=args.plan.resolve(), output_dir=args.output.resolve())
    elif args.workload_id:
        if args.wave is None:
            parser.error("--wave is required with --workload-id")
        fragment = capacity.run_capacity_wave(
            plan_path=args.plan.resolve(),
            output_dir=args.output.resolve(),
            script_path=Path(__file__).resolve(),
            workload_id=args.workload_id,
            wave_number=args.wave,
        )
        passed = sum(1 for row in fragment["results"] if row.get("status") == "PASS")
        return 0 if passed == len(fragment["results"]) else 1
    else:
        # One command remains user-facing, while os.execve replaces the
        # controller after every wave. This guarantees that no controller
        # accumulates native-library/process state across the matrix.
        sequence = _wave_sequence(args.plan.resolve())
        index = 0 if args.resume_index is None else args.resume_index
        if index < 0 or index > len(sequence):
            parser.error("invalid internal resume index")
        if index < len(sequence):
            workload_id, wave = sequence[index]
            fragment = capacity.run_capacity_wave(
                plan_path=args.plan.resolve(),
                output_dir=args.output.resolve(),
                script_path=Path(__file__).resolve(),
                workload_id=workload_id,
                wave_number=wave,
            )
            passed = sum(1 for row in fragment["results"] if row.get("status") == "PASS")
            if passed != len(fragment["results"]):
                return 1
            _exec_resume(args, index + 1)
            raise AssertionError("os.execve returned unexpectedly")
        report = capacity.finalize_capacity_plan(plan_path=args.plan.resolve(), output_dir=args.output.resolve())

    print(json.dumps({"status": report["status"], "summary": report["summary"], "tested_envelope": report["tested_envelope"]}, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
