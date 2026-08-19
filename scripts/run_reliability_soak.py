#!/usr/bin/env python3
"""
用途：执行长生命周期 Worker 与周期性进程回收的可靠性耐久测试。
参数：控制模式使用 --plan、--output；工作模式使用 --worker-request、--worker-result。
输出：逐 Worker 结果、资源增长证据和聚合可靠性报告。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "src" / "sat_sim" / "reliability_soak.py"
    spec = importlib.util.spec_from_file_location("_sat_sim_reliability_soak_standalone", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load reliability module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker-request", type=Path)
    parser.add_argument("--worker-result", type=Path)
    args = parser.parse_args()
    module = _load_module()
    if args.worker_request or args.worker_result:
        if not args.worker_request or not args.worker_result:
            parser.error("--worker-request and --worker-result are required together")
        return module.run_worker_file(args.worker_request.resolve(), args.worker_result.resolve())
    if not args.plan or not args.output:
        parser.error("--plan and --output are required")
    report = module.run_soak_plan(
        plan_path=args.plan.resolve(),
        output_dir=args.output.resolve(),
        script_path=Path(__file__).resolve(),
    )
    print(json.dumps({"status": report["status"], "summary": report["summary"]}, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
