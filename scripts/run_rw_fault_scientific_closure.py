#!/usr/bin/env python3
"""
用途：生成反作用轮故障/名义配对数据，并执行非泄漏物理特征、可分性、规则基线和机器学习基准分析。
参数：--output 指定输出目录；可选 --dataset-root、各分区样本数、仿真步长和 --basilisk。
输出：数据集索引、特征表、可分性报告、工程筛查模型与 AstroGraph 模型清单。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sat_sim.fault_dataset_factory import (
    FaultDatasetFactoryConfig,
    SplitCounts,
    build_fault_dataset_campaign,
    run_fault_dataset_campaign,
)
from sat_sim.rw_fault_scientific_closure import run_scientific_closure


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset-root", type=Path)
    parser.add_argument("--train-per-fault", type=int, default=12)
    parser.add_argument("--validation-per-fault", type=int, default=4)
    parser.add_argument("--test-per-fault", type=int, default=4)
    parser.add_argument("--duration-s", type=float, default=30.0)
    parser.add_argument("--sample-s", type=float, default=0.5)
    parser.add_argument("--solver-step-s", type=float, default=0.1)
    parser.add_argument("--basilisk", action="store_true", help="Require native Basilisk; never falls back")
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    dataset_root = args.dataset_root or (args.output / "dataset")
    if args.dataset_root is None:
        config = FaultDatasetFactoryConfig(
            campaign_id="phase3i_rw_fault_scientific_closure",
            counts=SplitCounts(
                train=args.train_per_fault,
                validation=args.validation_per_fault,
                test=args.test_per_fault,
            ),
            duration_s=args.duration_s,
            sample_s=args.sample_s,
            solver_step_s=args.solver_step_s,
            capability_id=("subsystem.adcs_unified_native.v1" if args.basilisk else "subsystem.adcs_fidelity.v1"),
            simulation_backend=("basilisk" if args.basilisk else "python"),
            allow_test_proxy=not args.basilisk,
        )
        execution = run_fault_dataset_campaign(
            build_fault_dataset_campaign(config),
            output_root=dataset_root,
            continue_on_error=False,
        )
        (args.output / "dataset_generation_summary.json").write_text(
            json.dumps(execution.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    result = run_scientific_closure(dataset_root, output_dir=args.output / "analysis")
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
