"""Coverage-completion reporting utilities for C8."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .source_inventory import build_coverage_index, write_inventory_artifacts
from .task_spec import write_json

REPORT_SCHEMA_VERSION = "c8.coverage_completion_report.v1"


def build_completion_report(repo_root: str | Path) -> dict[str, Any]:
    repo = Path(repo_root)
    # Refresh inventory artifacts first so docs and machine index match current contracts.
    write_inventory_artifacts(repo)
    index = build_coverage_index(repo)
    summary = dict(index.get("summary") or {})
    source_modules = index.get("source_modules") if isinstance(index.get("source_modules"), list) else []
    modifier_modules = [m for m in source_modules if m.get("recommended_exposure") == "modifier_layer_only"]
    excluded_modules = [m for m in source_modules if m.get("recommended_exposure") == "exclude_from_agent_coverage"]
    unresolved = list(index.get("uncovered_source_candidates") or [])
    source_native_caps = [c for c in index.get("capabilities", []) if isinstance(c, Mapping) and ((c.get("source_binding") or {}).get("mode") == "source_native")]
    synthetic_caps = [c for c in index.get("capabilities", []) if isinstance(c, Mapping) and ((c.get("source_binding") or {}).get("mode") == "synthetic")]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "summary": {
            **summary,
            "modifier_module_count": len(modifier_modules),
            "excluded_module_count": len(excluded_modules),
            "source_native_capabilities": len(source_native_caps),
            "synthetic_capabilities": len(synthetic_caps),
            "modifier_framework": "supported_as_scenario_modifier_layer",
            "whole_spacecraft_source_native_status": "readiness_documented_not_agent_ready",
        },
        "coverage_policy": index.get("coverage_policy", {}),
        "modifier_modules": [m.get("module") for m in modifier_modules],
        "excluded_modules": [m.get("module") for m in excluded_modules],
        "remaining_uncovered_source_candidates": unresolved,
        "source_native_capabilities": [c.get("capability_id") for c in source_native_caps],
        "synthetic_capabilities": [c.get("capability_id") for c in synthetic_caps],
        "recommended_next_steps": [
            "Keep faults/degradations as modifiers, not runnable capabilities.",
            "Do not force source-native wrappers for ADCS/whole_spacecraft until stable non-runner APIs exist.",
            "Expand source-grounded eval cases as user-request patterns grow.",
        ],
    }


def markdown_completion_report(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), Mapping) else {}
    remaining = report.get("remaining_uncovered_source_candidates") if isinstance(report.get("remaining_uncovered_source_candidates"), list) else []
    source_native = report.get("source_native_capabilities") if isinstance(report.get("source_native_capabilities"), list) else []
    synthetic = report.get("synthetic_capabilities") if isinstance(report.get("synthetic_capabilities"), list) else []
    modifier_modules = report.get("modifier_modules") if isinstance(report.get("modifier_modules"), list) else []
    lines = [
        "# C8 Source Coverage Completion Report",
        "",
        "本报告汇总 coverage-completion 阶段结束时 Agent 对原 `src` 能力的覆盖状态。覆盖口径是 capability + source_binding + modifier policy，不是简单目录计数。",
        "",
        "## Summary",
        "",
        f"- Agent capabilities: {summary.get('agent_capability_count')}",
        f"- Source-native capabilities: {summary.get('source_native_capability_count')}",
        f"- Synthetic capabilities: {summary.get('synthetic_capability_count')}",
        f"- Legacy-runner capabilities: {summary.get('legacy_runner_capability_count')}",
        f"- Demo-only capabilities: {summary.get('demo_only_capability_count')}",
        f"- Modifier module count: {summary.get('modifier_module_count')}",
        f"- Uncovered candidates under policy: {summary.get('uncovered_candidate_source_module_count')}",
        f"- Whole-spacecraft source-native status: {summary.get('whole_spacecraft_source_native_status')}",
        "",
        "## Modifier coverage",
        "",
        "Fault/degradation support is implemented as `modifiers.faults` and `modifiers.degradations`, not as runnable capabilities.",
        "",
    ]
    for item in modifier_modules:
        lines.append(f"- `{item}`")
    lines.extend(["", "## Source-native capabilities", ""])
    for cid in source_native:
        lines.append(f"- `{cid}`")
    lines.extend(["", "## Synthetic but supported Agent-facing capabilities", ""])
    for cid in synthetic:
        lines.append(f"- `{cid}`")
    lines.extend(["", "## Remaining uncovered source candidates", ""])
    if remaining:
        for item in remaining:
            lines.append(f"- `{item}`")
    else:
        lines.append("- None")
    lines.extend([
        "",
        "## Interpretation",
        "",
        "- `source_native` means the capability contract declares a direct or component-level source binding to original `src` public APIs.",
        "- `synthetic` means the capability remains a deterministic Agent-facing composition or simplification boundary.",
        "- `legacy_runner` remains 0 by design: demo runners were not promoted to production Agent boundaries.",
    ])
    return "\n".join(lines) + "\n"


def write_completion_artifacts(repo_root: str | Path, *, output_dir: str | Path = "reports/source_coverage_completion_eval") -> dict[str, Path]:
    repo = Path(repo_root)
    report = build_completion_report(repo)
    docs = repo / "docs"
    handoff = docs / "handoff"
    out = repo / output_dir
    docs.mkdir(exist_ok=True)
    handoff.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "report_json": out / "coverage_completion_report.json",
        "report_md": docs / "source_coverage_completion_report.md",
    }
    write_json(paths["report_json"], report)
    paths["report_md"].write_text(markdown_completion_report(report), encoding="utf-8")
    return paths


__all__ = ["REPORT_SCHEMA_VERSION", "build_completion_report", "markdown_completion_report", "write_completion_artifacts"]
