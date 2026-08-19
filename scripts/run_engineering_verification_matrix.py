#!/usr/bin/env python3
"""
用途：查询、导出或执行工程验证矩阵中的测试项。
参数：--project-root、--list、--stats、--export、--test-id、--level、--component、--execute-all、--execute-shell、--output、--output-dir、--limit。
输出：输出单项或批量工程验证报告。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from sat_sim.verification_matrix import (
    build_verification_matrix,
    export_matrix,
    item_by_id,
    matrix_stats,
    run_many,
    run_verification_item,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="engineering verification matrix runner")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--list", action="store_true", help="list matrix items")
    parser.add_argument("--stats", action="store_true", help="print matrix statistics")
    parser.add_argument("--export", help="export the full matrix JSON to this path")
    parser.add_argument("--test-id", help="run one test item")
    parser.add_argument("--level", help="run a selected level, e.g. component/subsystem")
    parser.add_argument("--component", help="filter selected items by object/component")
    parser.add_argument("--execute-all", action="store_true", help="run the selected matrix items")
    parser.add_argument("--execute-shell", action="store_true", help="allow shell-command items to execute instead of dry-run")
    parser.add_argument("--output", help="output path for one test report")
    parser.add_argument("--output-dir", default="reports/engineering_verification", help="directory for batch reports")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    if args.export:
        payload = export_matrix(args.export, project_root=args.project_root)
        print(json.dumps({"status": "PASS", "item_count": payload["item_count"], "output": args.export}, ensure_ascii=False, indent=2))
        return 0

    items = build_verification_matrix(project_root=args.project_root)

    if args.stats:
        print(json.dumps(matrix_stats(items), ensure_ascii=False, indent=2))
        return 0

    if args.list:
        for item in items:
            if args.level and item.level != args.level:
                continue
            if args.component and item.object != args.component:
                continue
            print(f"{item.test_id}\t{item.level}\t{item.object}\t{item.scenario_type}\t{item.command}")
        return 0

    if args.test_id:
        item = item_by_id(args.test_id)
        report = run_verification_item(item, project_root=args.project_root, execute_shell=args.execute_shell)
        if args.output:
            out = Path(args.output)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({k: report[k] for k in ("schema_version", "status", "reason", "elapsed_s")}, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "PASS" else 1

    if args.execute_all or args.level or args.component:
        summary = run_many(
            level=args.level,
            component=args.component,
            project_root=args.project_root,
            output_dir=args.output_dir,
            execute_shell=args.execute_shell,
            limit=args.limit,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["failed"] == 0 else 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
