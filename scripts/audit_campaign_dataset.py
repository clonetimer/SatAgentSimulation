"""
用途：审计 Campaign 数据集完整性，并比较两次运行的稳定输出。
参数：主运行目录、可选对照运行目录和报告输出路径。
输出：生成数据集审计、重复性与失败或阻断案例 JSON 证据。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_case_index(root: Path) -> list[dict[str, str]]:
    with (root / "case_index.csv").open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def audit_dataset(run1: Path, run2: Path | None) -> dict[str, Any]:
    manifest = _read_json(run1 / "manifest.json")
    rows = _read_case_index(run1)
    case_checks: list[dict[str, Any]] = []
    blocked_or_failed: list[dict[str, str]] = []

    for row in rows:
        case_id = row["case_id"]
        trace_path = run1 / "cases" / case_id / "trace.csv"
        summary_path = run1 / "cases" / case_id / "summary.json"
        manifest_path = run1 / row["dataset_manifest"]
        with trace_path.open(encoding="utf-8", newline="") as f:
            trace_rows = list(csv.DictReader(f))
        check = {
            "case_id": case_id,
            "status": row["status"],
            "trace_rows_index": int(row["summary.trace_rows"]),
            "trace_rows_actual": len(trace_rows),
            "manifest_exists": manifest_path.exists(),
            "summary_exists": summary_path.exists(),
            "trace_exists": trace_path.exists(),
        }
        case_checks.append(check)
        if row["status"] != "complete":
            blocked_or_failed.append(row)

    repeatability: dict[str, Any] = {}
    stable_mismatches: list[str] = []
    if run2 is not None:
        files: list[dict[str, Any]] = []
        for row in rows:
            case_id = row["case_id"]
            for rel in (f"cases/{case_id}/trace.csv", f"cases/{case_id}/summary.json"):
                h1 = _sha256(run1 / rel)
                h2 = _sha256(run2 / rel)
                same = h1 == h2
                files.append({"relative_path": rel, "run1_sha256": h1, "run2_sha256": h2, "same": same})
                if not same:
                    stable_mismatches.append(rel)
        run2_rows = _read_case_index(run2)
        repeatability = {
            "run1": str(run1),
            "run2": str(run2),
            "stable_files": files,
            "stable_file_mismatches": stable_mismatches,
            "case_spec_hashes_equal_across_output_roots": [row["spec_hash"] for row in rows]
            == [row["spec_hash"] for row in run2_rows],
            "case_spec_hash_note": (
                "Current case spec_hash includes outputs.output_root, so cross-output-root runs have "
                "different case spec hashes even when simulation outputs match."
            ),
        }

    ok = (
        manifest.get("status") == "complete"
        and manifest.get("run", {}).get("success_count") == len(rows)
        and manifest.get("run", {}).get("failure_count") == 0
        and not blocked_or_failed
        and not stable_mismatches
        and all(
            check["trace_rows_index"] == check["trace_rows_actual"]
            and check["manifest_exists"]
            and check["summary_exists"]
            and check["trace_exists"]
            for check in case_checks
        )
    )
    return {
        "schema_version": "sat-sim.campaign-dataset-audit.v1",
        "ok": ok,
        "campaign_id": manifest.get("dataset_id"),
        "campaign_hash": manifest.get("provenance", {}).get("campaign_hash"),
        "case_count": manifest.get("run", {}).get("scenario_count"),
        "success_count": manifest.get("run", {}).get("success_count"),
        "failure_count": manifest.get("run", {}).get("failure_count"),
        "case_checks": case_checks,
        "repeatability": repeatability,
        "blocked_or_failed_cases": blocked_or_failed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run1", type=Path)
    parser.add_argument("--compare-run", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = audit_dataset(args.run1, args.compare_run)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
