#!/usr/bin/env python3
"""
用途：审计 24 部件、6 分系统和 1 整星的范围、候选场景与可执行合同覆盖。
参数：--scope、--scenarios、--json-output、--markdown-output；--strict 要求 G0 完整通过。
输出：生成机器可读 JSON 和中文 Markdown 覆盖报告，并打印门禁摘要。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sat_sim.object_scenario_coverage import (
    audit_object_scenario_coverage,
    default_scenario_path,
    default_scope_path,
    render_coverage_markdown,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", type=Path, default=default_scope_path())
    parser.add_argument("--scenarios", type=Path, default=default_scenario_path())
    parser.add_argument("--json-output", type=Path, default=Path("reports/object_scenario_coverage.json"))
    parser.add_argument("--markdown-output", type=Path, default=Path("reports/object_scenario_coverage.md"))
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)

    report = audit_object_scenario_coverage(scope_path=args.scope, scenario_path=args.scenarios)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.markdown_output.write_text(render_coverage_markdown(report), encoding="utf-8")
    summary = {
        "gate": report["gate"],
        "gate_status": report["gate_status"],
        "technical_baseline_status": report["technical_baseline_status"],
        "blockers": [item["code"] for item in report["blockers"]],
        "json_output": str(args.json_output),
        "markdown_output": str(args.markdown_output),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 1 if args.strict and report["gate_status"] != "PASS" else 0


if __name__ == "__main__":
    raise SystemExit(main())
