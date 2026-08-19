#!/usr/bin/env python3
"""执行基础模型库—Capability 一致性与因果协议门禁。

用途：核验全部 Capability 的基础模块映射、实现类别、支持/不支持耦合和限制声明。
参数：无；读取 src/sat_sim/capabilities 下的正式能力合同。
输出：reports/v<release>_capability_integration_audit.json/.md。
"""
from __future__ import annotations

import json
from pathlib import Path

from sat_sim.capability_integration_audit import build_capability_integration_audit, render_capability_integration_markdown
from sat_sim.release_closure import RELEASE_VERSION


def main() -> int:
    report = build_capability_integration_audit()
    root = Path("reports")
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / f"v{RELEASE_VERSION}_capability_integration_audit.json"
    md_path = root / f"v{RELEASE_VERSION}_capability_integration_audit.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_capability_integration_markdown(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
