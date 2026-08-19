#!/usr/bin/env python3
"""
用途：检查工程验证矩阵的完整性和命令可执行性。
参数：--output、--min-items。
输出：生成工程验证矩阵检查 JSON。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from sat_sim.verification_matrix import COMPONENTS, MATRIX_VERSION, SUBSYSTEMS, build_verification_matrix, matrix_stats

REQUIRED_COMPONENT_SCENARIO_TYPES = {
    "nominal",
    "batch",
    "fault_default_set",
    "degradation_default_set",
    "combined_default_set",
    "duration_boundary",
    "negative",
    "determinism",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate engineering verification matrix")
    parser.add_argument("--output", default="reports/engineering_matrix_check.json")
    parser.add_argument("--min-items", type=int, default=320)
    args = parser.parse_args(argv)

    items = build_verification_matrix()
    issues: list[dict[str, str]] = []
    ids = set()
    for item in items:
        if item.test_id in ids:
            issues.append({"code": "DUPLICATE_TEST_ID", "test_id": item.test_id})
        ids.add(item.test_id)
        if not item.command:
            issues.append({"code": "MISSING_COMMAND", "test_id": item.test_id})
        elif not (item.command.startswith("python scripts/") or item.command.startswith("sat-agent") or item.command.startswith("ruff") or item.command.startswith("python -m")):
            issues.append({"code": "NON_EXECUTABLE_COMMAND_PREFIX", "test_id": item.test_id, "command": item.command})
        if not item.pass_rule or not item.failure_rule:
            issues.append({"code": "MISSING_RULE", "test_id": item.test_id})
        if not item.judge_parameters:
            issues.append({"code": "MISSING_JUDGE_PARAMETERS", "test_id": item.test_id})
    if len(items) < args.min_items:
        issues.append({"code": "TOO_FEW_ITEMS", "count": str(len(items)), "min": str(args.min_items)})

    for component in COMPONENTS:
        c_items = [item for item in items if item.level == "component" and item.object == component]
        found_types = {item.scenario_type for item in c_items}
        missing = REQUIRED_COMPONENT_SCENARIO_TYPES - found_types
        if missing:
            issues.append({"code": "COMPONENT_SCENARIO_TYPE_MISSING", "component": component, "missing": ",".join(sorted(missing))})
        if not any(item.scenario_type == "fault" for item in c_items):
            issues.append({"code": "COMPONENT_FAULT_SINGLE_MISSING", "component": component})
        if not any(item.scenario_type == "degradation" for item in c_items):
            issues.append({"code": "COMPONENT_DEGRADATION_SINGLE_MISSING", "component": component})

    for subsystem in SUBSYSTEMS:
        if not [item for item in items if item.level == "subsystem" and item.object == subsystem]:
            issues.append({"code": "SUBSYSTEM_MISSING", "subsystem": subsystem})

    payload = {
        "schema_version": MATRIX_VERSION,
        "status": "PASS" if not issues else "FAIL",
        "issues": issues,
        "stats": matrix_stats(items),
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "item_count": len(items), "issue_count": len(issues), "output": args.output}, indent=2, ensure_ascii=False))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
