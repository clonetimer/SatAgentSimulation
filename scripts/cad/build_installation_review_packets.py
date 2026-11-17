#!/usr/bin/env python3
"""用途：生成 P9-M3 CAD 候选的坐标与装配证据审查包。
参数：--candidate-mapping、--assembly-tree、--source-manifest、--output。
输出：CAD 源局部/全局变换数学核查、父子变换一致性、飞行单元/装配/基准证据缺口；
不签发实装坐标、不创建设备实例或空间锚点。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from sat_sim.cad_installation_review import build_installation_review_packets  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-mapping", type=Path, required=True)
    parser.add_argument("--assembly-tree", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, default=ROOT / "assets/cad-sources/birds4/birds4-cad-source-manifest.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    candidates = json.loads(args.candidate_mapping.read_text(encoding="utf-8"))
    assembly = json.loads(args.assembly_tree.read_text(encoding="utf-8"))
    manifest = json.loads(args.source_manifest.read_text(encoding="utf-8"))
    report = build_installation_review_packets(candidates, assembly, manifest)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
