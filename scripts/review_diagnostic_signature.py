#!/usr/bin/env python3
"""
用途：对已通过 Basilisk 诊断候选门的故障签名执行具名专家冻结或拒绝。
参数：dataset_root、fault_id、decision、reviewer_id、reviewer_type、notes。
输出：签名审核记录、更新后的诊断资格索引与数据集索引；不会自动批准具体数据 Pair。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from sat_sim.diagnostic_signature_review import record_diagnostic_signature_review  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_root", type=Path)
    parser.add_argument("fault_id")
    parser.add_argument("--decision", choices=("FREEZE", "REJECT"), required=True)
    parser.add_argument("--reviewer-id", required=True)
    parser.add_argument("--reviewer-type", choices=("human", "llm_assisted_human"), required=True)
    parser.add_argument("--notes", required=True)
    parser.add_argument("--evidence-ref", action="append", default=[])
    args = parser.parse_args()
    result = record_diagnostic_signature_review(
        args.dataset_root,
        fault_id=args.fault_id,
        decision=args.decision,
        reviewer_id=args.reviewer_id,
        reviewer_type=args.reviewer_type,
        notes=args.notes,
        evidence_refs=tuple(args.evidence_ref),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
