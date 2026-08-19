#!/usr/bin/env python3
"""
用途：使用真实密封 Run Bundle 执行受控经验编译、审批、复用和回滚验收。
参数：--bundle 指定运行包，--output-dir 指定机器可读证据目录。
输出：写入 report.json 和隔离 Store 证据，并以退出码表示 PASS/FAIL。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sat_sim.experience_acceptance import run_controlled_experience_acceptance


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/controlled_experience_acceptance"),
    )
    args = parser.parse_args()
    report = run_controlled_experience_acceptance(
        bundle_root=args.bundle,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
