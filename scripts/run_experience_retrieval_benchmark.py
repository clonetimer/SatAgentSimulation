#!/usr/bin/env python3
"""
用途：执行 1k/10k/100k 经验元数据检索时延和存储增长基准。
参数：可指定规模、重复次数和机器可读报告路径。
输出：写入 p50/p95、插入耗时、存储增长和预算判定 JSON。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sat_sim.experience_benchmark import run_experience_retrieval_benchmark


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", type=int, action="append")
    parser.add_argument("--repetitions", type=int, default=100)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/experience_retrieval_benchmark.json"),
    )
    args = parser.parse_args()
    report = run_experience_retrieval_benchmark(
        output_path=args.output,
        scales=args.scale or (1_000, 10_000, 100_000),
        repetitions=args.repetitions,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
