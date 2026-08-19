#!/usr/bin/env python3
"""
用途：记录故障/名义配对数据的人工或大模型辅助人工审核，不允许审核覆盖技术门失败。
参数：dataset_root、pair_id、decision、reviewer-id、reviewer-type、notes 和可重复 evidence-ref。
输出：写入专家审核记录并更新数据合同与总索引；审核输入不完整或越权时返回非零。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from sat_sim.dataset_expert_review import record_dataset_pair_review  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_root")
    parser.add_argument("pair_id")
    parser.add_argument("decision", choices=("APPROVE", "REJECT"))
    parser.add_argument("--reviewer-id", required=True)
    parser.add_argument("--reviewer-type", choices=("human", "llm_assisted_human"), required=True)
    parser.add_argument("--notes", required=True)
    parser.add_argument("--evidence-ref", action="append", default=[])
    args = parser.parse_args()
    result = record_dataset_pair_review(
        Path(args.dataset_root),
        pair_id=args.pair_id,
        decision=args.decision,
        reviewer_id=args.reviewer_id,
        reviewer_type=args.reviewer_type,
        notes=args.notes,
        evidence_refs=args.evidence_ref,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
