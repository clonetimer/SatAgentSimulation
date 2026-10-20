"""用途：对已经终止的交互仿真工作区执行可验证的遥测分段无损压缩，拒绝在线压缩。
参数：--workspace 必填路径；--max-segments 可选；--repair-interrupted-compression 显式修复双份暂存。
输出：包含归档数量、压缩前后字节数与不变的逻辑遥测 SHA-256 的 JSON；失败时非零退出。
Offline, lossless and fail-closed compression of closed telemetry segments.

Never operate on a running simulator. Does not delete the active journal, the
verified Run Bundle, or any unverified old segment. Compression is optional and
not automatic; storage remains bounded only by a separately configured quota.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sat_sim.interactive.models import SessionState
from sat_sim.interactive.workspace import SessionWorkspace
from sat_sim.interactive import telemetry_segments


_TERMINAL = {
    SessionState.COMPLETED, SessionState.FAILED,
    SessionState.ABORTED, SessionState.INTERRUPTED,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--repair-interrupted-compression", action="store_true")
    group.add_argument("--max-segments", type=int, default=None)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    if args.repair_interrupted_compression:
        repaired = telemetry_segments.repair_interrupted_compression(workspace)
        inspection = SessionWorkspace.inspect(workspace)
        if not inspection.valid:
            raise ValueError(f"INTERACTIVE_WORKSPACE_INTEGRITY_FAILED:{inspection.reason_code}")
        print(json.dumps({"status": "REPAIRED", "duplicates_reconciled": repaired}))
        return 0

    inspection = SessionWorkspace.inspect(workspace)
    if not inspection.valid:
        raise ValueError(f"INTERACTIVE_WORKSPACE_INTEGRITY_FAILED:{inspection.reason_code}")
    if inspection.state not in _TERMINAL:
        raise ValueError("TELEMETRY_ARCHIVE_REQUIRES_TERMINAL_SESSION")

    before_hash = telemetry_segments.logical_sha256(workspace)
    # This reads the full history, but never materializes it in RAM.
    result = telemetry_segments.compress_closed(workspace, max_segments=args.max_segments)
    final_inspection = SessionWorkspace.inspect(workspace)
    if not final_inspection.valid:
        raise ValueError(f"INTERACTIVE_WORKSPACE_INTEGRITY_FAILED:{final_inspection.reason_code}")
    after_hash = telemetry_segments.logical_sha256(workspace)
    if before_hash != after_hash:
        raise ValueError("TELEMETRY_ARCHIVE_SEMANTIC_HASH_MISMATCH")
    print(json.dumps({
        "status": "VERIFIED", **result, "logical_telemetry_sha256": after_hash,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
