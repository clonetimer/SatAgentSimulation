#!/usr/bin/env python3
"""
用途：执行 RT7 交互安全与可靠性专项矩阵并保存可追溯 pytest/JUnit 证据。
参数：--matrix 可选 security、reliability 或 all；--output-root 指定报告目录。
输出：security_acceptance.json、reliability_acceptance.json、逐矩阵日志和 JUnit；任何失败/错误/跳过均返回非零。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

MATRICES: dict[str, dict[str, Any]] = {
    "security": {
        "test_files": [
            "tests/interactive/test_interactive_api.py",
            "tests/interactive/test_command_candidate.py",
            "tests/interactive/test_command_catalog.py",
            "tests/interactive/test_command_lifecycle.py",
            "tests/interactive/test_contracts.py",
            "tests/interactive/test_session_workspace.py",
            "tests/interactive/test_interactive_recovery.py",
        ],
        "controls": {
            "RT-S-01": "REST/WebSocket authentication, origin and role enforcement",
            "RT-S-02": "unknown operations, arbitrary code-like fields, path-like fields and invalid parameters fail closed",
            "RT-S-03": "WebSocket size/rate limits plus HTTP command flood limit",
            "RT-S-04": "cross-session, revision, duplicate and conflicting command IDs fail closed",
            "RT-S-05": "fault operations require fault_operator/admin",
            "RT-S-06": "LLM output remains a candidate until deterministic validation and human confirmation",
            "RT-S-07": "workspace hash-chain tampering blocks sealing and recovery",
            "RT-S-08": "telemetry field allowlist prevents internal attribute reads",
        },
    },
    "reliability": {
        "test_files": [
            "tests/interactive/test_segmented_runtime.py",
            "tests/interactive/test_unified_segmented_execution.py",
            "tests/interactive/test_subsystem_persistent_runtimes.py",
            "tests/interactive/test_session_manager.py",
            "tests/interactive/test_session_state_machine.py",
            "tests/interactive/test_session_workspace.py",
            "tests/interactive/test_telecommand_mailbox.py",
            "tests/interactive/test_telemetry_bus.py",
            "tests/interactive/test_interactive_recovery.py",
        ],
        "controls": {
            "RT-R-01": "whole-spacecraft, ADCS, EPS and Comm/Data segmented/batch pairing",
            "RT-R-02": "concurrent controls terminate without deadlock or illegal terminal state",
            "RT-R-03": "disconnect/reconnect replay preserves sequences and reports gaps",
            "RT-R-04": "unfinished worker state becomes INTERRUPTED and deterministic recovery is explicit",
            "RT-R-05": "same TaskSpec, seed and acknowledged command log reproduces semantic trace hash",
            "RT-R-06": "queue full, expiry, ordering, duplicate and conflict outcomes are explicit",
            "RT-R-07": "terminal paths release worker/runtime resources and reclaim sessions",
            "RT-R-08": "ACKs map to telemetry effect windows and final validation evidence",
        },
    },
}


def _junit_totals(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    return {
        key: sum(int(float(suite.attrib.get(key, "0") or 0)) for suite in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }


def run_matrix(name: str, output_root: Path) -> dict[str, Any]:
    definition = MATRICES[name]
    junit = output_root / f"{name}_acceptance.junit.xml"
    log = output_root / f"{name}_acceptance.log"
    command = [sys.executable, "-m", "pytest", "-q", *definition["test_files"], f"--junitxml={junit}"]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src") + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=600.0,
    )
    log.write_text(completed.stdout or "", encoding="utf-8")
    totals = _junit_totals(junit) if junit.is_file() else {"tests": 0, "failures": 0, "errors": 1, "skipped": 0}
    passed = (
        completed.returncode == 0
        and totals["tests"] > 0
        and totals["failures"] == totals["errors"] == totals["skipped"] == 0
    )
    report = {
        "schema_version": "interactive-assurance-acceptance.v1",
        "gate": "RT7_PERFORMANCE_SECURITY",
        "matrix": name,
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "soft-realtime internal engineering simulation; not hardware or flight validation",
        "command": command,
        "exit_code": completed.returncode,
        "duration_s": round(time.perf_counter() - started, 6),
        "junit": totals,
        "junit_path": str(junit),
        "log_path": str(log),
        "test_files": definition["test_files"],
        "checks": {
            control_id: {"status": "PASS" if passed else "FAIL", "evidence": evidence}
            for control_id, evidence in definition["controls"].items()
        },
        "claim_boundary": "Every mapped test file must pass with zero skipped; a definition or stale prior report cannot close this matrix.",
    }
    path = output_root / f"{name}_acceptance.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{name}: {report['status']} tests={totals['tests']} failures={totals['failures']} errors={totals['errors']} skipped={totals['skipped']}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", choices=("security", "reliability", "all"), default="all")
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "reports" / "interactive")
    args = parser.parse_args()
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    selected = tuple(MATRICES) if args.matrix == "all" else (args.matrix,)
    reports = [run_matrix(name, output_root) for name in selected]
    return 0 if all(report["passed"] for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
