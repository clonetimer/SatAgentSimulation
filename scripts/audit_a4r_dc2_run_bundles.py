#!/usr/bin/env python3
"""Audit A4R-DC2 Composite Digital Twin Run Bundle evidence.

用途：逐项核验 A4R-DC2 实际运行的一手 Run Bundle 证据、非 legacy 执行状态和关键 SHA-256。
参数：--root 指定证据根目录；--output 指定 JSON 审计报告输出路径。
输出：JSON 报告；全部运行满足 adapter/legacy/hash/bundle 要求时返回 0。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


COMPOSITE_ADAPTER_KEY = "basilisk.composite_digital_twin_graph"
REQUIRED_HASH_KEYS = (
    "execution_plan_sha256",
    "model_graph_sha256",
    "parameter_set_sha256",
    "binding_set_sha256",
    "capability_projection_sha256",
)


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value.lower())


def _bundle_root_for_execution_port(path: Path) -> Path:
    if path.parent.name == "runtime":
        return path.parent.parent
    # .../<run>/attempts/attempt_001/execution_port.json
    return path.parents[2]


def audit(root: Path) -> dict[str, Any]:
    execution_ports = sorted(root.rglob("execution_port.json"))
    bundle_manifests = sorted(root.rglob("bundle_manifest.json"))
    runs: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    for port_path in execution_ports:
        payload = json.loads(port_path.read_text(encoding="utf-8"))
        bundle_root = _bundle_root_for_execution_port(port_path)
        bundle_manifest = bundle_root / "bundle_manifest.json"
        row = {
            "execution_port": port_path.relative_to(root).as_posix(),
            "bundle_manifest": bundle_manifest.relative_to(root).as_posix(),
            "adapter_key": payload.get("adapter_key"),
            "legacy_mode": payload.get("legacy_mode"),
            "legacy_bridge_called": payload.get("legacy_bridge_called"),
            **{key: payload.get(key) for key in REQUIRED_HASH_KEYS},
        }
        row_issues: list[str] = []
        if payload.get("adapter_key") != COMPOSITE_ADAPTER_KEY:
            row_issues.append("adapter_key")
        if payload.get("legacy_mode") is not False:
            row_issues.append("legacy_mode")
        if payload.get("legacy_bridge_called") is not False:
            row_issues.append("legacy_bridge_called")
        if not bundle_manifest.is_file():
            row_issues.append("bundle_manifest")
        for key in REQUIRED_HASH_KEYS:
            if not _is_sha256(payload.get(key)):
                row_issues.append(key)
        row["ok"] = not row_issues
        row["issues"] = row_issues
        runs.append(row)
        if row_issues:
            issues.append({"execution_port": row["execution_port"], "issues": row_issues})

    return {
        "schema_version": "sat-sim.a4r-dc2.run-bundle-audit.v1",
        "ok": bool(execution_ports) and not issues,
        "root": str(root),
        "execution_port_count": len(execution_ports),
        "bundle_manifest_count": len(bundle_manifests),
        "composite_execution_count": sum(1 for row in runs if row["adapter_key"] == COMPOSITE_ADAPTER_KEY),
        "issues": issues,
        "runs": runs,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    report = audit(Path(args.root))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": report["ok"],
        "execution_port_count": report["execution_port_count"],
        "bundle_manifest_count": report["bundle_manifest_count"],
        "composite_execution_count": report["composite_execution_count"],
        "issue_count": len(report["issues"]),
    }, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
