#!/usr/bin/env python3
"""
用途：使用基线运行与单因素扰动运行验证跨分系统耦合的因果性，避免仅凭消息存在或有限数值声明物理耦合通过。
参数：--baseline 基线 Run Bundle 或遥测 CSV/JSONL；--perturbed 扰动 Run Bundle 或遥测文件；--expectations 因果期望 JSON；--output 输出报告路径。
输出：JSON 报告；只有源变化、目标响应、响应方向及可选时延约束全部满足时返回 PASS。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sat_sim.coupling_causality import CausalExpectation, evaluate_coupling_causality, load_telemetry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--perturbed", required=True, type=Path)
    parser.add_argument("--expectations", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    definitions = json.loads(args.expectations.read_text(encoding="utf-8"))
    expectations = [CausalExpectation(**item) for item in definitions]
    report = evaluate_coupling_causality(load_telemetry(args.baseline), load_telemetry(args.perturbed), expectations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
