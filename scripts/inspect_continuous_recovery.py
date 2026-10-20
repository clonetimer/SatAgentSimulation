#!/usr/bin/env python3
"""用途：检查持续卫星仿真进程异常退出后的证据交接文件，拒绝将遥测当成可恢复的物理状态。
参数：--workspace 指向已有的中断会话工作区；默认只读，--create-missing 显式为已中断且经过验证的工作区补建缺失交接；不启动 Basilisk。
输出：以 JSON 输出经过重新验真的事件数、最后持久化遥测时刻及物理续跑能力边界。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sat_sim.interactive.recovery_handoff import (
    verify_interrupted_handoff, write_interrupted_handoff, HANDOFF_FILENAME,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument(
        "--create-missing", action="store_true",
        help="Explicitly attest an already interrupted, verified session with a missing handoff",
    )
    args = parser.parse_args()
    if args.create_missing and not (args.workspace / HANDOFF_FILENAME).exists():
        write_interrupted_handoff(args.workspace)
    payload = verify_interrupted_handoff(args.workspace)
    print(json.dumps({
        "status": "VERIFIED_SOURCE_EVIDENCE",
        "session_id": payload["session_id"],
        "evidence": payload["evidence"],
        "physical_recovery": payload["physical_recovery"],
        "record_sha256": payload["record_sha256"],
    }, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
