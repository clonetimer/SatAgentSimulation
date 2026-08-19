#!/usr/bin/env python3
"""执行跨分系统 QoI 并生成物理耦合发布门禁。

用途：运行 pairwise/dynamic 耦合检查，阻断未知 FAIL/PARTIAL 链路。
参数：无；从当前项目配置与模型运行器读取验收输入。
输出：reports/v<release>_coupling_completeness_gate.json。
"""
from __future__ import annotations

import json
from pathlib import Path

from integration.qoi_profiles import run_dynamic_profiles, run_pairwise_profiles, run_resource_closure_profiles
from sat_sim.coupling_gate import build_coupling_completeness_gate
from sat_sim.release_closure import RELEASE_VERSION


def main() -> int:
    reports = [run_pairwise_profiles(), run_dynamic_profiles(), run_resource_closure_profiles()]
    gate = build_coupling_completeness_gate(reports)
    output = Path("reports") / f"v{RELEASE_VERSION}_coupling_completeness_gate.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(gate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(gate, ensure_ascii=False, indent=2))
    return 0 if gate["release_blocking_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
