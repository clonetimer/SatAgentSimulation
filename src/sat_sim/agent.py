"""Agent-facing helpers for TaskSpec generation and repair.

This module exposes compact, machine-readable context that an Agent can use to
produce TaskSpec documents without importing or understanding Basilisk runner
internals.  It does not execute simulations.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .task_spec import CANONICAL_TASK_SPEC_VERSION, TASK_SPEC_VERSION
from .agent_guards import agent_tool_policy_payload
from .task_validator import ValidationIssue, ValidationResult


SUPPORTED_TASK_TYPES = {
    "component": {
        "status": "validate_compile_run_for_import-safe_runner_modules",
        "required_blocks": ["target", "simulation", "outputs"],
        "recommended_templates": ["component_battery_nominal", "component_battery_fault_open_circuit"],
    },
    "subsystem": {
        "status": "validate_compile_run_for_import-safe_runner_modules; some runners may require Basilisk",
        "required_blocks": ["target", "simulation", "outputs"],
        "recommended_templates": ["subsystem_adcs_control_nominal", "subsystem_adcs_magnetic_detumble_nominal"],
    },
    "orbit_environment": {
        "status": "validate_compile_run_without_basilisk",
        "required_blocks": ["simulation", "orbit_environment", "outputs"],
        "recommended_templates": ["orbit_environment_nominal", "campaign_orbit_environment_grid"],
    },
    "whole_spacecraft": {
        "status": "validate_compile; run_requires_basilisk",
        "required_blocks": ["simulation", "spacecraft", "outputs"],
        "recommended_templates": ["whole_spacecraft_nominal", "whole_spacecraft_battery_fault", "whole_spacecraft_degradation"],
    },
    "campaign": {
        "status": "expand_run_child_tasks",
        "required_blocks": ["simulation", "campaign", "outputs"],
        "recommended_templates": ["campaign_orbit_environment_grid", "campaign_component_battery_fault_sweep", "campaign_whole_spacecraft_fault_sweep"],
    },
}

REPAIR_HINTS_BY_CODE = {
    "required": "补齐缺失字段；优先从相近模板复制完整块，再修改数值。",
    "schema": "字段名、类型或层级不符合 JSON Schema；检查路径是否拼错，避免添加未声明字段。",
    "range": "调整数值到允许范围；注意 SoC 是 0-1，百分比字段是 0-100。",
    "time": "检查 duration_s、sample_s、fault onset/duration；故障窗口不能超过仿真总时长。",
    "time_grid": "建议让 duration_s 成为 sample_s 的整数倍，方便时间序列对齐。",
    "enum": "使用白名单枚举值；不要凭空发明 task_type、mode、backend、fault_type。",
    "mode_contract": "target.mode 要和 faults/degradations 一致；nominal 不应包含故障或退化。",
    "campaign": "检查 campaign.base_spec、sampling、parameter_sweeps、randomizations、cases。",
    "writer_support": "训练数据优先使用 csv；parquet 需要 pandas 和 parquet engine。",
}


@dataclass(frozen=True)
class AgentRepairHint:
    """A repair hint derived from a validation issue."""

    path: str
    code: str
    message: str
    hint: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "code": self.code, "message": self.message, "hint": self.hint}


def task_spec_generation_contract() -> dict[str, Any]:
    """Return the stable contract an Agent should target."""

    return {
        "schema_version": CANONICAL_TASK_SPEC_VERSION,
        "output_format": "YAML or JSON TaskSpec only; do not emit arbitrary Python simulation scripts",
        "pipeline": ["draft", "canonicalize", "validate", "guard", "compile", "run"],
        "tool_policy": agent_tool_policy_payload(),
        "cli": {
            "validate": "sat-sim validate <spec.yaml> --json",
            "compile": "sat-sim compile <spec.yaml>",
            "expand_campaign": "sat-sim expand <campaign.yaml> --output <campaign_plan.json>",
            "run": "sat-sim run <spec.yaml> --output <dataset_dir>",
            "catalog": "sat-sim catalog",
            "agent_context": "sat-sim agent-context",
        },
        "supported_task_types": SUPPORTED_TASK_TYPES,
        "unit_rules": {
            "time": "seconds in TaskSpec; Basilisk nanoseconds are hidden behind the runner",
            "power": "watts",
            "energy": "Wh for capacity fields, J may appear in traces",
            "rates": "SI units, e.g. rad/s, bit/s, m/s",
            "soc": "0-1 fraction",
            "percent_degradation": "0-100 percent",
        },
        "agent_rules": [
            "Start from an existing example template whenever possible.",
            "Generate TaskSpec, then run validate; repair using path/code/message.",
            "Use sat-sim catalog to choose valid component/subsystem target.name values.",
            "Do not call Basilisk modules or internal builders directly from generated scripts.",
            "For data generation, prefer campaign TaskSpec with explicit seed and manifest output.",
            "Keep task_id stable, descriptive, and filesystem-safe.",
        ],
    }


def repair_hints_from_issues(issues: Sequence[ValidationIssue] | ValidationResult | Sequence[Mapping[str, Any]]) -> list[AgentRepairHint]:
    """Generate compact repair hints from validator issues."""

    raw: Sequence[Any]
    if isinstance(issues, ValidationResult):
        raw = issues.issues
    else:
        raw = issues
    hints: list[AgentRepairHint] = []
    for issue in raw:
        if isinstance(issue, ValidationIssue):
            path, code, message = issue.path, issue.code, issue.message
        elif isinstance(issue, Mapping):
            path, code, message = str(issue.get("path", "$")), str(issue.get("code", "validation")), str(issue.get("message", ""))
        else:
            continue
        hint = REPAIR_HINTS_BY_CODE.get(code, "根据 path 定位字段，结合 TaskSpec schema 和示例模板修正。")
        hints.append(AgentRepairHint(path=path, code=code, message=message, hint=hint))
    return hints


def example_template_index(examples_dir: str | Path = "examples") -> list[dict[str, Any]]:
    """Build a lightweight index of local example TaskSpecs."""

    from .task_spec import load_task_spec

    root = Path(examples_dir)
    out: list[dict[str, Any]] = []
    if not root.exists():
        return out
    for path in sorted(list(root.glob("*.yaml")) + list(root.glob("*.yml")) + list(root.glob("*.json"))):
        try:
            doc = load_task_spec(path)
            if doc.schema_version in {TASK_SPEC_VERSION, CANONICAL_TASK_SPEC_VERSION} and doc.task_id:
                task = doc.data.get("task") if isinstance(doc.data.get("task"), Mapping) else {}
                model = doc.data.get("model") if isinstance(doc.data.get("model"), Mapping) else {}
                out.append(
                    {
                        "path": str(path),
                        "task_id": doc.task_id,
                        "task_type": doc.task_type,
                        "template": model.get("template") or doc.data.get("template"),
                        "description": task.get("description") or doc.data.get("description"),
                        "tags": task.get("tags") or doc.data.get("tags", []),
                        "schema_version": doc.schema_version,
                    }
                )
        except Exception:
            continue
    return out


def agent_context_payload(examples_dir: str | Path = "examples") -> dict[str, Any]:
    """Return generation contract plus local example inventory."""

    from .catalog import catalog_payload

    payload = task_spec_generation_contract()
    payload["examples"] = example_template_index(examples_dir)
    payload["target_catalog"] = catalog_payload()
    return payload


__all__ = [
    "SUPPORTED_TASK_TYPES",
    "AgentRepairHint",
    "agent_context_payload",
    "example_template_index",
    "repair_hints_from_issues",
    "task_spec_generation_contract",
]
