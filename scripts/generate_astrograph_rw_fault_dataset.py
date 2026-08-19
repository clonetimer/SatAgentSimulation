#!/usr/bin/env python3
"""
用途：按受控配置批量生成 AstroGraph 反作用轮故障/名义配对数据集。
参数：--config 指定工厂配置，--output 覆盖输出目录，--dry-run 仅展开计划，--fail-fast 首个失败即退出。
输出：标准 sat-sim campaign 产物、逐案例 AstroGraph sidecar 契约和数据集总索引。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sat_sim.fault_dataset_factory import (
    FaultDatasetFactoryConfig,
    build_fault_dataset_campaign,
    run_fault_dataset_campaign,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default="configs/integration/rw_dataset_factory.json",
        help="fault-dataset factory JSON",
    )
    parser.add_argument("--output", help="override campaign output root")
    parser.add_argument("--dry-run", action="store_true", help="expand and validate without executing child simulations")
    parser.add_argument("--fail-fast", action="store_true", help="stop at the first failed child task")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = FaultDatasetFactoryConfig.from_json(args.config)
    campaign_spec = build_fault_dataset_campaign(config)
    result = run_fault_dataset_campaign(
        campaign_spec,
        output_root=Path(args.output) if args.output else None,
        continue_on_error=not args.fail_fast,
        dry_run=args.dry_run,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    failures = int(result.campaign.summary.get("failure_count", 0))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
