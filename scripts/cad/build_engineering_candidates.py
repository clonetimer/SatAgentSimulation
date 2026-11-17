#!/usr/bin/env python3
"""用途：将 workbench 工程设备类别与 CAD 装配节点生成可审计候选映射。\n参数：--engineering-tree、--assembly-tree、--source-model、--source-revision、--output。\n输出：候选 JSON、命中依据、歧义/型号审查标记；不生成装机实例、空间锚点或 verified 关系。\n"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from sat_sim.cad_candidate_mapping import main


if __name__ == "__main__":
    raise SystemExit(main())
