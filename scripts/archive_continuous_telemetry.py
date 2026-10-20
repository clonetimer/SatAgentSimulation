"""Offline, lossless and fail-closed compression of closed telemetry segments.

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
