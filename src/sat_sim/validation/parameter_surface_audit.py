"""Structural parameter audit. Textual source lookups are NOT physics proof."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Mapping

from sat_sim.capability_registry import list_capabilities
from sat_sim.form_schema import capability_form_schema
from sat_sim.interactive.continuous_initial_config import initial_configuration_catalog

SCHEMA_VERSION = "sat-sim.parameter-surface-traceability.v1"
WHOLE_ID = "whole_spacecraft.unified_native.v1"
SOURCE = "src/sat_sim/bsk_engine/unified_native.py"


def literal_runtime_keys(source: str) -> tuple[set[str], set[str]]:
    """Identify source-level values.get('key'), values['key'] and f-string prefixes."""
    tree = ast.parse(source)
    found: set[str] = set()
    prefixes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr != "get" or not isinstance(node.func.value, ast.Name):
                continue
            if node.func.value.id != "values" or not node.args:
                continue
            key = node.args[0]
        elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "values":
            key = node.slice
        else:
            continue
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            found.add(key.value)
        elif isinstance(key, ast.JoinedStr):
            prefix = "".join(v.value for v in key.values
                             if isinstance(v, ast.Constant) and isinstance(v.value, str))
            if prefix:
                prefixes.add(prefix)
    return found, prefixes


def build_parameter_surface_audit(*, root: Path | None = None) -> dict[str, Any]:
    repo = root or Path(__file__).resolve().parents[3]
    literals, prefixes = literal_runtime_keys((repo / SOURCE).read_text(encoding="utf-8"))
    groups = initial_configuration_catalog()["groups"]
    continuous: dict[str, str] = {}
    for group in groups:
        for item in group["fields"]:
            key = str(item["native_value_key"])
            if key in continuous:
                raise ValueError("continuous initial field duplicated: " + key)
            continuous[key] = str(group["group"])
    contracts = list_capabilities()
    selected = [c for c in contracts if (c.capability_id.startswith(("component.", "subsystem."))
                                       and c.is_active and c.exposed_to_agent)]
    whole = next(c for c in contracts if c.capability_id == WHOLE_ID)
    issues: list[dict[str, str]] = []
    capability_rows = []
    for contract in sorted([*selected, whole], key=lambda c: c.capability_id):
        cid = contract.capability_id
        declared = contract.data.get("parameters") or {}
        if not isinstance(declared, Mapping):
            issues.append({"capability_id": cid, "code": "REGISTRY_PARAMETER_SCHEMA_INVALID"})
            declared = {}
        try:
            form = capability_form_schema(cid)
        except Exception as exc:
            issues.append({"capability_id": cid, "code": "FORM_BUILD_FAILED", "detail": str(exc)})
            continue
        form_fields = {field["path"].removeprefix("parameters.values."): field
                       for field in form["fields"] if field["path"].startswith("parameters.values.")}
        for key in sorted(set(declared) - set(form_fields)):
            issues.append({"capability_id": cid, "parameter": key,
                           "code": "REGISTRY_PARAMETER_NOT_IN_FORM"})
        for key in sorted(set(form_fields) - set(declared)):
            issues.append({"capability_id": cid, "parameter": key,
                           "code": "FORM_PARAMETER_NOT_IN_REGISTRY"})
        entries = []
        for key in sorted(declared):
            field = form_fields.get(key)
            read = ("literal_lookup_found" if key in literals else
                    "dynamic_lookup_pattern_found" if any(key.startswith(p) for p in prefixes) else
                    "no_direct_source_lookup_found") if cid == WHOLE_ID else "independent_adapter_not_traced"
            entries.append({
                "key": key, "form_path": field["path"] if field else None,
                "registry_unit": declared[key].get("unit") if isinstance(declared[key], Mapping) else None,
                "form_unit": field.get("unit") if field else None,
                "native_source_lookup": read, "continuous_initial_exposed": key in continuous,
                "physical_perturbation_verified_by_this_audit": False,
            })
        implementation = contract.data.get("implementation") or {}
        capability_rows.append({
            "capability_id": cid, "level": contract.target_level,
            "basilisk_required": bool(implementation.get("basilisk_required", False)),
            "registered_parameter_count": len(declared),
            "form_parameter_count": len(form_fields),
            "parameters": entries,
        })
    whole_fields = set(whole.data.get("parameters") or {})
    no_literal = lambda key: key not in literals and not any(key.startswith(p) for p in prefixes)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "FAIL" if issues else "PASS",
        "status_scope": "registry_to_form_structure_only_not_physics",
        "physics_validated_by_this_audit": False,
        "native_source": SOURCE,
        "source_lookup_is_not_physical_proof": True,
        "component_count": sum(c["level"] == "component" for c in capability_rows),
        "subsystem_count": sum(c["level"] == "subsystem" for c in capability_rows),
        "capability_count": len(capability_rows),
        "total_registered_parameter_count": sum(c["registered_parameter_count"] for c in capability_rows),
        "form_issue_count": len(issues), "issues": issues, "capabilities": capability_rows,
        "whole_spacecraft": {
            "registered_parameter_count": len(whole_fields),
            "literal_source_key_count": len(literals),
            "native_source_keys_missing_whole_registry": sorted(literals - whole_fields),
            "whole_registry_keys_without_direct_lookup": sorted(k for k in whole_fields if no_literal(k)),
            "continuous_initial_field_count": len(continuous),
            "continuous_groups": {g["group"]: [x["native_value_key"] for x in g["fields"]]
                                  for g in groups},
            "continuous_fields_without_source_lookup": sorted(k for k in continuous if no_literal(k)),
            "native_keys_not_exposed_in_continuous_initial": sorted(literals - set(continuous)),
            "source_dynamic_prefixes": sorted(prefixes),
        },
        "limitations": [
            "Registry-to-form PASS is not approval of a native physical parameter.",
            "Source values lookup only means the key appears in code, not that a Basilisk object consumes it.",
            "Independent adapters and component builders are not dataflow verified by this report.",
            "Defaults and internal configuration keys are not automatically operator-editable.",
            "Continuous initial configuration is whole-spacecraft-only, distinct from the finite form.",
            "Native perturbation, message wiring and Run evidence are separately required.",
            "Whole-spacecraft cross-process physical restart remains unapproved.",
        ],
    }


def render_parameter_surface_markdown(report: Mapping[str, Any]) -> str:
    whole = report["whole_spacecraft"]
    lines = [
        "# 仿真参数能力合同—表单—原生源码覆盖审计", "",
        "结构门禁：" + report["status"] + "（不代表物理生效验收通过）", "",
        "单机：" + str(report["component_count"]) + "；分系统：" + str(report["subsystem_count"]) +
        "；总注册参数：" + str(report["total_registered_parameter_count"]), "",
        "| Capability | 层级 | 参数声明数 | 表单参数数 | 原生运行依赖 |",
        "|---|---|---:|---:|---|",
    ]
    for c in report["capabilities"]:
        lines.append("| " + c["capability_id"] + " | " + c["level"] + " | " +
                     str(c["registered_parameter_count"]) + " | " +
                     str(c["form_parameter_count"]) + " | " +
                     ("Basilisk" if c["basilisk_required"] else "独立项目模型/其它") + " |")
    lines += ["", "## 整星原生参数差集（需逐项判断是否该开放）", "",
              "整星登记：" + str(whole["registered_parameter_count"]) +
              "；源代码字面量参数键：" + str(whole["literal_source_key_count"]) +
              "；持续仿真初始字段：" + str(whole["continuous_initial_field_count"]), "",
              "### 原生源码引用但整星合同未登记", "",
              ", ".join(whole["native_source_keys_missing_whole_registry"]) or "无", "",
              "### 整星合同已登记但源码未直接读取", "",
              ", ".join(whole["whole_registry_keys_without_direct_lookup"]) or "无", "",
              "### 持续仿真未开放的原生源码键", "",
              ", ".join(whole["native_keys_not_exposed_in_continuous_initial"]) or "无", "",
              "## 证据边界", ""]
    lines += ["- " + note for note in report["limitations"]]
    return "\n".join(lines) + "\n"


__all__ = ["SCHEMA_VERSION", "WHOLE_ID", "SOURCE", "literal_runtime_keys",
           "build_parameter_surface_audit", "render_parameter_surface_markdown"]
