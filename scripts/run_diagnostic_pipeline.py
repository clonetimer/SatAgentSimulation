#!/usr/bin/env python3
"""
用途：对一组故障/名义数据案例执行受控诊断 Pipeline 本地阶段，并导出 AstroGraph 交接包。
参数：--fault-root、--nominal-root 指定案例目录；--fault-id 可选校验故障；--no-persist 禁止写回案例目录。
输出：diagnostic_pipeline_runtime.json、simulation_evidence_graph.json，以及标准输出摘要。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from sat_sim.diagnostic_pipeline_runtime import DiagnosticPipelineExecutor  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fault-root", type=Path, required=True)
    parser.add_argument("--nominal-root", type=Path, required=True)
    parser.add_argument("--fault-id")
    parser.add_argument("--external-stage-outputs", type=Path)
    parser.add_argument("--no-persist", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    external = {}
    if args.external_stage_outputs:
        external = json.loads(args.external_stage_outputs.read_text(encoding="utf-8"))
        if not isinstance(external, dict):
            raise ValueError("external stage outputs must be a JSON object")
    payload = DiagnosticPipelineExecutor().execute(
        fault_root=args.fault_root,
        nominal_root=args.nominal_root,
        fault_id=args.fault_id,
        external_stage_outputs=external,
        persist=not args.no_persist,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "execution_status": payload["execution_status"],
        "fault_id": payload["fault_id"],
        "pair_id": payload.get("pair_id"),
        "runtime_sha256": payload["runtime_sha256"],
        "formal_diagnosis_publishable": payload["formal_diagnosis_publishable"],
    }, ensure_ascii=False, indent=2))
    return 1 if payload["execution_status"] in {"BLOCKED_PIPELINE_STATUS", "REJECTED_LOCAL_EVIDENCE"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
