#!/usr/bin/env python3
"""
用途：应用 AstroGraph Phase 3-G 候选阶段输出并重放跨项目诊断 Pipeline。
参数：--fault-root、--nominal-root 指定配对案例目录；--closure 指定 AstroGraph closure JSON。
输出：在故障案例 astrograph 目录写出 astrograph_roundtrip.json，并打印外部阶段状态摘要。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sat_sim.astrograph_roundtrip import apply_astrograph_closure


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply AstroGraph Phase 3-G external stage outputs")
    parser.add_argument("--fault-root", required=True)
    parser.add_argument("--nominal-root", required=True)
    parser.add_argument("--closure", required=True)
    args = parser.parse_args()
    closure = json.loads(Path(args.closure).read_text(encoding="utf-8"))
    result = apply_astrograph_closure(
        fault_root=args.fault_root,
        nominal_root=args.nominal_root,
        closure=closure,
        persist=True,
    )
    print(json.dumps({
        "fault_id": result["fault_id"],
        "completed_external_stages": result["completed_external_stages"],
        "pending_expert_gate": result["pending_expert_gate"],
        "formal_diagnosis_publishable": result["formal_diagnosis_publishable"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
