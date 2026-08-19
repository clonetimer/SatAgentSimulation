"""Authoritative 31-object scope and common-scenario coverage audit."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from .capability_registry import list_capabilities
from .workbench_catalog import WORKBENCH_CATALOG_VERSION, workbench_presentation_catalog

COVERAGE_REPORT_VERSION = "sat-sim.object-scenario-coverage.v1"
REQUIRED_LEVEL_COUNTS = {"component": 24, "subsystem": 6, "whole_spacecraft": 1}
REQUIRED_MODES = ("nominal", "fault", "degradation")


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_scope_path() -> Path:
    return project_root() / "configs" / "acceptance" / "object_scope.json"


def default_scenario_path() -> Path:
    return project_root() / "configs" / "acceptance" / "scenario_baseline.json"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _validate_schema(payload: Mapping[str, Any], schema_name: str) -> list[dict[str, str]]:
    schema = _read_json(Path(__file__).with_name("schemas") / schema_name)
    issues: list[dict[str, str]] = []
    for error in sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda item: list(item.path)):
        location = ".".join(str(part) for part in error.absolute_path) or "$"
        issues.append({"code": "SCHEMA_INVALID", "path": location, "detail": error.message})
    return issues


def _mode_supported(contract_data: Mapping[str, Any], mode: str) -> bool:
    modes = contract_data.get("modes")
    if not isinstance(modes, Mapping):
        return False
    payload = modes.get(mode)
    return isinstance(payload, Mapping) and payload.get("supported") is True


def _declared_effect_evidence(contract: Any, mode: str) -> tuple[list[str], bool]:
    effects = [effect for effect in contract.operator_contract.effects if effect.kind == mode]
    ids = [effect.effect_id for effect in effects]
    verified = bool(effects) and all(
        effect.verification == "declared" and bool(effect.evidence_fields)
        for effect in effects
    )
    return ids, verified


def audit_object_scenario_coverage(
    *,
    scope_path: Path | None = None,
    scenario_path: Path | None = None,
) -> dict[str, Any]:
    """Compare frozen product scope and candidate scenarios with executable contracts."""

    scope_path = scope_path or default_scope_path()
    scenario_path = scenario_path or default_scenario_path()
    scope = _read_json(scope_path)
    baseline = _read_json(scenario_path)
    issues = [
        *_validate_schema(scope, "object_scope.schema.json"),
        *_validate_schema(baseline, "scenario_baseline.schema.json"),
    ]

    scope_objects = {str(item["object_id"]): item for item in scope.get("objects", []) if isinstance(item, Mapping)}
    scenario_objects = {str(item["object_id"]): item for item in baseline.get("objects", []) if isinstance(item, Mapping)}
    catalog_objects = {
        str(item["object_id"]): item
        for item in workbench_presentation_catalog()["objects"]
        if item.get("level") in REQUIRED_LEVEL_COUNTS
    }
    contracts = {contract.capability_id: contract for contract in list_capabilities()}

    if len(scope_objects) != len(scope.get("objects", [])):
        issues.append({"code": "DUPLICATE_SCOPE_OBJECT", "path": "objects", "detail": "object_id values must be unique"})
    if len(scenario_objects) != len(baseline.get("objects", [])):
        issues.append({"code": "DUPLICATE_SCENARIO_OBJECT", "path": "objects", "detail": "object_id values must be unique"})
    if set(scope_objects) != set(catalog_objects):
        issues.append({
            "code": "SCOPE_CATALOG_MISMATCH",
            "path": "objects",
            "detail": f"missing={sorted(set(catalog_objects) - set(scope_objects))}; extra={sorted(set(scope_objects) - set(catalog_objects))}",
        })
    if set(scope_objects) != set(scenario_objects):
        issues.append({
            "code": "SCOPE_SCENARIO_MISMATCH",
            "path": "objects",
            "detail": f"missing={sorted(set(scope_objects) - set(scenario_objects))}; extra={sorted(set(scenario_objects) - set(scope_objects))}",
        })

    level_counts = Counter(str(item.get("level")) for item in scope_objects.values())
    for level, expected in REQUIRED_LEVEL_COUNTS.items():
        if level_counts[level] != expected:
            issues.append({
                "code": "LEVEL_COUNT_MISMATCH",
                "path": f"expected_counts.{level}",
                "detail": f"expected {expected}, got {level_counts[level]}",
            })
    if scope.get("catalog_version") != WORKBENCH_CATALOG_VERSION:
        issues.append({
            "code": "CATALOG_VERSION_MISMATCH",
            "path": "catalog_version",
            "detail": f"expected {WORKBENCH_CATALOG_VERSION}, got {scope.get('catalog_version')}",
        })

    rows: list[dict[str, Any]] = []
    for object_id in sorted(scope_objects, key=lambda value: (scope_objects[value].get("level", ""), value)):
        declared = scope_objects[object_id]
        catalog = catalog_objects.get(object_id, {})
        scenario = scenario_objects.get(object_id, {})
        primary_id = declared.get("primary_capability_id")
        catalog_primary = catalog.get("primary_capability_id")
        if primary_id != catalog_primary:
            issues.append({
                "code": "PRIMARY_CAPABILITY_DRIFT",
                "path": object_id,
                "detail": f"scope={primary_id!r}; catalog={catalog_primary!r}",
            })
        contract = contracts.get(str(primary_id)) if primary_id else None
        if primary_id and contract is None:
            issues.append({
                "code": "PRIMARY_CAPABILITY_UNKNOWN",
                "path": object_id,
                "detail": str(primary_id),
            })

        adapter = contract.data.get("adapter", {}) if contract else {}
        outputs = contract.data.get("outputs", {}) if contract else {}
        mode_rows: dict[str, Any] = {}
        scenario_block = scenario.get("scenarios", {}) if isinstance(scenario, Mapping) else {}
        for mode in REQUIRED_MODES:
            candidates = scenario_block.get(mode, []) if isinstance(scenario_block, Mapping) else []
            effect_ids, direct_evidence = _declared_effect_evidence(contract, mode) if contract else ([], False)
            mode_rows[mode] = {
                "scenario_ids": [item.get("scenario_id") for item in candidates if isinstance(item, Mapping)],
                "scenario_defined": bool(candidates),
                "approved": bool(candidates) and all(
                    isinstance(item, Mapping) and item.get("review_status") == "approved"
                    for item in candidates
                ),
                "contract_supported": bool(contract and _mode_supported(contract.data, mode)),
                "effect_ids": effect_ids,
                "direct_effect_evidence_declared": direct_evidence,
            }
        rows.append({
            "object_id": object_id,
            "level": declared.get("level"),
            "domain": declared.get("domain"),
            "primary_capability_id": primary_id,
            "primary_active": bool(contract and contract.is_active and contract.exposed_to_agent),
            "runtime_run": bool(isinstance(adapter, Mapping) and adapter.get("runtime_run") is True),
            "script_export": bool(isinstance(adapter, Mapping) and adapter.get("script_export") is True),
            "outputs_declared": bool(isinstance(outputs, Mapping) and outputs),
            "candidate_capability_ids": list(declared.get("candidate_capability_ids") or []),
            "modes": mode_rows,
        })

    counts: dict[str, Any] = {}
    for level in REQUIRED_LEVEL_COUNTS:
        selected = [row for row in rows if row["level"] == level]
        counts[level] = {
            "total": len(selected),
            "primary_active": sum(row["primary_active"] for row in selected),
            **{
                mode: sum(row["primary_active"] and row["modes"][mode]["contract_supported"] for row in selected)
                for mode in REQUIRED_MODES
            },
            "all_three_modes": sum(
                row["primary_active"]
                and all(row["modes"][mode]["contract_supported"] for mode in REQUIRED_MODES)
                for row in selected
            ),
        }

    scenario_set_complete = all(
        row["modes"][mode]["scenario_defined"]
        for row in rows
        for mode in REQUIRED_MODES
    )
    approval_complete = (
        baseline.get("status") == "approved"
        and baseline.get("approval", {}).get("status") == "APPROVED"
        and all(row["modes"][mode]["approved"] for row in rows for mode in REQUIRED_MODES)
    )
    technical_scope_complete = not issues and len(rows) == 31 and scenario_set_complete
    gate_status = "PASS" if technical_scope_complete and approval_complete else "BLOCKED"
    blockers: list[dict[str, str]] = []
    if issues:
        blockers.append({"code": "BASELINE_INTEGRITY_FAILED", "detail": f"{len(issues)} integrity issue(s)"})
    if not scenario_set_complete:
        blockers.append({"code": "SCENARIO_SET_INCOMPLETE", "detail": "Every object needs nominal, fault and degradation candidates."})
    if not approval_complete:
        blockers.append({
            "code": "PENDING_DOMAIN_REVIEW",
            "detail": "Candidate scenarios, parameter ranges and N/A decisions require named domain approval.",
        })

    return {
        "schema_version": COVERAGE_REPORT_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "gate": "G0_SCOPE_BASELINE",
        "gate_status": gate_status,
        "technical_baseline_status": "PASS" if technical_scope_complete else "FAIL",
        "scope_status": scope.get("scope_status"),
        "scenario_baseline_id": baseline.get("baseline_id"),
        "scenario_baseline_status": baseline.get("status"),
        "approval": baseline.get("approval"),
        "claim_boundary": scope.get("claim_boundary"),
        "performance_budget": baseline.get("performance_budget"),
        "counts": counts,
        "issues": issues,
        "blockers": blockers,
        "objects": rows,
    }


def render_coverage_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# 对象与场景覆盖审计",
        "",
        f"- Gate：`{report['gate']}` = **{report['gate_status']}**",
        f"- 技术基线：**{report['technical_baseline_status']}**",
        f"- 场景审批：`{report.get('approval', {}).get('status')}`",
        f"- 边界：{report['claim_boundary']}",
        "",
        "| 层级 | 对象 | 主能力 | 正常 | 故障 | 退化 | 三模式 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for level in ("component", "subsystem", "whole_spacecraft"):
        item = report["counts"][level]
        lines.append(
            f"| {level} | {item['total']} | {item['primary_active']} | "
            f"{item['nominal']} | {item['fault']} | {item['degradation']} | {item['all_three_modes']} |"
        )
    lines.extend(["", "## 堵塞项", ""])
    for blocker in report["blockers"]:
        lines.append(f"- `{blocker['code']}`：{blocker['detail']}")
    lines.extend([
        "",
        "## 对象矩阵",
        "",
        "| 对象 | 主 Capability | 运行 | 脚本 | 输出 | 正常/故障/退化 |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ])
    for row in report["objects"]:
        modes = "/".join("Y" if row["modes"][mode]["contract_supported"] else "N" for mode in REQUIRED_MODES)
        lines.append(
            f"| `{row['object_id']}` | `{row['primary_capability_id'] or '-'}` | "
            f"{'Y' if row['runtime_run'] else 'N'} | {'Y' if row['script_export'] else 'N'} | "
            f"{'Y' if row['outputs_declared'] else 'N'} | {modes} |"
        )
    lines.extend([
        "",
        "> 本报告只证明内部工程仿真范围及当前代码覆盖，不代表真实硬件、硬件标定或飞行验证。",
        "",
    ])
    return "\n".join(lines)


__all__ = [
    "COVERAGE_REPORT_VERSION",
    "audit_object_scenario_coverage",
    "default_scenario_path",
    "default_scope_path",
    "render_coverage_markdown",
]
