#!/usr/bin/env python3
"""
用途：导出卫星仿真平台与 AstroGraph 共用的故障机理—特征—诊断 Pipeline 映射包。
参数：--output 指定 JSON 输出路径。
输出：包含映射哈希、泄漏防护、Basilisk 数据门和模型绑定状态的 JSON 协议包。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from sat_sim.diagnostic_mapping_bundle import write_astrograph_diagnostic_mapping_bundle  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("reports/astrograph_diagnostic_mapping_bundle.v1.json"))
    args = parser.parse_args()
    payload = write_astrograph_diagnostic_mapping_bundle(args.output)
    print(json.dumps({"output": str(args.output), "mapping_count": payload["mapping_count"], "bundle_sha256": payload["bundle_sha256"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
