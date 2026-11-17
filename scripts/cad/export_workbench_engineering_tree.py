#!/usr/bin/env python3
"""用途：从当前正式 workbench 能力目录导出 P9-M2 候选映射使用的工程对象树。
参数：--output 指定输出 JSON 文件。
输出：workbench-object-tree.v2 JSON；不写入数据库、不创建实例或空间锚点。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from sat_sim.form_schema import capability_form_schema  # noqa: E402
from sat_sim.workbench_catalog import workbench_presentation_catalog  # noqa: E402
from sat_sim.workbench_object_tree import build_object_tree  # noqa: E402
from sat_sim.workbench_scene_catalog import build_scene_catalog  # noqa: E402


def export_engineering_tree() -> dict:
    presentation = workbench_presentation_catalog()
    scene = build_scene_catalog(presentation)
    schemas = {
        row["execution_capability_id"]: capability_form_schema(row["execution_capability_id"])
        for row in scene["objects"]
        if row.get("execution_capability_id")
    }
    return build_object_tree(scene, schemas)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = export_engineering_tree()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "schema_version": payload["schema_version"],
        "equipment_category_count": payload["totals"]["equipment"],
        "component_counts_are_inventory": payload["semantics"]["component_counts_are_inventory"],
        "spatial_anchors_require_asset_evidence": payload["semantics"]["spatial_anchors_require_asset_evidence"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
