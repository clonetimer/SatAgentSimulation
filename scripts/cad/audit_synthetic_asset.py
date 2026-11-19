#!/usr/bin/env python3
"""用途：审计生成式/参数化设备 GLB 并验证 synthetic asset manifest。
参数：--manifest JSON、--glb GLB、--output JSON。
输出：GLB 结构、尺寸包围盒、SHA-256 和 synthetic preview 验证结果；不批准工程尺寸或实装坐标。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sat_sim.synthetic_asset_generation import validate_synthetic_asset


def main() -> int:
    # argparse help includes the Chinese CLI description; force UTF-8 on Windows
    # consoles that otherwise default to a legacy code page such as cp1252.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--glb", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    record = json.loads(args.manifest.read_text(encoding="utf-8"))
    report = validate_synthetic_asset(record, args.glb)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "asset_id": report["asset_id"],
        "quality_grade": report["quality_grade"],
        "validation_status": report["validation_status"],
        "glb_sha256": report["glb_audit"]["sha256"],
        "dimensions": report["glb_audit"]["bounds"]["dimensions"],
        "unit": report["glb_audit"]["bounds"]["unit"],
        "as_built_eligible": False,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
