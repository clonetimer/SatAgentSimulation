#!/usr/bin/env python3
"""
用途：串联 six-capability 严格遥测验收与逐文件全量回归，形成恢复阶段最终本地验收入口。
参数：--output-root 指定总证据目录；--allow-missing-basilisk 仅运行构造态检查；其余参数设置能力和回归超时。
输出：生成两套子验收证据和 final_recovery_acceptance.json；只有运行时可用且两套验收均通过才返回零退出码。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def _run(cmd: list[str], *, env: dict[str, str]) -> int:
    print("$", " ".join(cmd), flush=True)
    return subprocess.run(cmd, env=env).returncode


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, default=Path("reports/final_recovery_acceptance"))
    parser.add_argument("--allow-missing-basilisk", action="store_true", help="construction mode only; never produces a formal final PASS")
    parser.add_argument("--capability-timeout-s", type=float, default=300.0)
    parser.add_argument("--regression-file-timeout-s", "--regression-timeout-s", dest="regression_file_timeout_s", type=float, default=300.0)
    args = parser.parse_args()
    root = args.output_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    script_root = Path(__file__).resolve().parent
    env = dict(os.environ)
    project_src = str(script_root.parent / "src")
    env["PYTHONPATH"] = project_src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    gate_cmd = [
        sys.executable,
        str(script_root / "verify_recovery_third_party.py"),
        "--output",
        str(root / "third_party_runtime_gate.json"),
    ]
    if not args.allow_missing_basilisk:
        gate_cmd.append("--require-basilisk-runtime")

    capability_cmd = [
        sys.executable,
        str(script_root / "run_six_internal_capability_acceptance.py"),
        "--output-root",
        str(root / "six_capabilities"),
        "--timeout-s",
        str(args.capability_timeout_s),
    ]
    regression_cmd = [
        sys.executable,
        str(script_root / "run_full_regression_supervised.py"),
        "--output-root",
        str(root / "full_regression"),
        "--file-timeout-s",
        str(args.regression_file_timeout_s),
    ]
    if args.allow_missing_basilisk:
        capability_cmd.append("--allow-missing-basilisk")
        regression_cmd.append("--allow-missing-basilisk")

    gate_exit = _run(gate_cmd, env=env)
    capability_exit: int | None = None
    regression_exit: int | None = None
    if gate_exit == 0:
        capability_exit = _run(capability_cmd, env=env)
        regression_exit = _run(regression_cmd, env=env)

    gate_report_path = root / "third_party_runtime_gate.json"
    capability_report_path = root / "six_capabilities" / "six_capability_acceptance.json"
    regression_report_path = root / "full_regression" / "full_regression.json"
    gate_report = _load_json(gate_report_path)
    capability_report = _load_json(capability_report_path)
    regression_report = _load_json(regression_report_path)

    formal_pass = (
        gate_exit == 0
        and capability_exit == 0
        and regression_exit == 0
        and not args.allow_missing_basilisk
        and gate_report.get("passed") is True
        and capability_report.get("passed") is True
        and regression_report.get("passed") is True
    )
    construction_complete = (
        args.allow_missing_basilisk
        and gate_exit == 0
        and capability_exit == 0
        and regression_exit == 0
        and gate_report.get("passed") is True
        and regression_report.get("construction_subset_passed") is True
    )
    payload = {
        "schema_version": "sat-sim.final-recovery-acceptance.v2",
        "status": "PASS" if formal_pass else "CONSTRUCTION_COMPLETE_NOT_FORMAL_PASS" if construction_complete else "BLOCKED_RUNTIME_DEPENDENCY" if gate_exit != 0 else "FAIL",
        "passed": formal_pass,
        "formal_pass_issued": formal_pass,
        "construction_complete": construction_complete,
        "third_party_gate_exit_code": gate_exit,
        "capability_exit_code": capability_exit,
        "regression_exit_code": regression_exit,
        "allow_missing_basilisk": args.allow_missing_basilisk,
        "reports": {
            "third_party_gate": str(gate_report_path),
            "six_capabilities": str(capability_report_path),
            "full_regression": str(regression_report_path),
        },
        "child_status": {
            "third_party_gate": gate_report.get("status"),
            "six_capabilities": capability_report.get("status"),
            "six_capability_pass_count": capability_report.get("pass_count"),
            "full_regression": regression_report.get("status"),
            "full_regression_raw_junit": regression_report.get("junit"),
            "full_regression_runtime_blocked_file_count": regression_report.get("runtime_blocked_file_count"),
            "full_regression_unclassified_failure_count": regression_report.get("unclassified_failure_count"),
        },
        "formal_requirements": {
            "accepted_basilisk_runtime": True,
            "six_capabilities_pass": "6/6",
            "full_regression_failures": 0,
            "full_regression_errors": 0,
            "full_regression_skipped": 0,
            "excluded_test_files": 0,
        },
        "claim_boundary": "Construction mode never converts runtime-blocked capabilities or raw pytest failures/skips into formal PASS.",
    }
    (root / "final_recovery_acceptance.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if (formal_pass or construction_complete) else 2


if __name__ == "__main__":
    raise SystemExit(main())
