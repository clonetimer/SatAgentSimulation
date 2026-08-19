#!/usr/bin/env python3
"""
用途：测量交互式软实时 quantum、倍速、漂移、指令、遥测、并发、背压和长稳边界。
参数：默认 --profile ci 仅作短时回归；--profile formal 实际执行 30 分钟墙钟和 4 小时仿真门。
输出：写入机器可读 JSON；CI 合格但正式时长未满足时明确保留 BLOCKED，不作外推 PASS。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from sat_sim.bsk_engine.unified_native import WHOLE_UNIFIED_CAPABILITY_ID, UnifiedRuntimeConfig
from sat_sim.interactive.performance import load_budget, resume_formal_capacity, run_performance_benchmark
from sat_sim.interactive.unified_runtime import UnifiedPersistentRuntime


def _runtime_factory(duration_s: float, quantum_s: float) -> UnifiedPersistentRuntime:
    return UnifiedPersistentRuntime(UnifiedRuntimeConfig(
        capability_id=WHOLE_UNIFIED_CAPABILITY_ID,
        duration_s=duration_s,
        step_s=min(0.1, quantum_s),
        sample_s=quantum_s,
        adcs_only=False,
        values={},
        events=(),
    ))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("ci", "formal"), default="ci")
    parser.add_argument(
        "--budget",
        type=Path,
        default=PROJECT_ROOT / "configs" / "interactive" / "performance_budget.json",
    )
    parser.add_argument(
        "--resume-formal-capacity",
        type=Path,
        help="rerun only capacity from a complete formal report after the audited final-stop conversion fix",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "interactive" / "performance_benchmark.json",
    )
    args = parser.parse_args()
    budget = load_budget(args.budget)
    if args.resume_formal_capacity:
        prior = json.loads(args.resume_formal_capacity.read_text(encoding="utf-8"))
        report = resume_formal_capacity(_runtime_factory, budget, prior)
    else:
        report = run_performance_benchmark(
            _runtime_factory,
            budget,
            formal=args.profile == "formal",
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if args.profile == "formal":
        return 0 if report["formal_passed"] else 1
    return 0 if report["qualification_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
