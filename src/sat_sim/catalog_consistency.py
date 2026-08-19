"""Cross-check product catalog, templates and release-facing documentation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .capability_registry import list_capabilities
from .product_closure import product_capability_closure
from .release_closure import RELEASE_VERSION
from .scenario_templates import list_scenario_templates
from .workbench_catalog import workbench_presentation_catalog

CATALOG_CONSISTENCY_SCHEMA_VERSION = "sat-sim.catalog-document-consistency.v1"


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _issue(code: str, severity: str, message: str, **evidence: Any) -> dict[str, Any]:
    return {"code": code, "severity": severity, "message": message, "evidence": evidence}


def catalog_document_consistency(root: str | Path | None = None) -> dict[str, Any]:
    repo = Path(root).resolve() if root else _repository_root()
    contracts = {item.capability_id: item for item in list_capabilities()}
    known_ids = set(contracts)
    issues: list[dict[str, Any]] = []

    default_catalog = workbench_presentation_catalog()
    full_catalog = workbench_presentation_catalog(include_compatibility=True, include_explicit=True)
    default_variants = [
        variant
        for obj in default_catalog.get("objects", [])
        for variant in obj.get("variants", [])
    ]
    full_variants = [
        variant
        for obj in full_catalog.get("objects", [])
        for variant in obj.get("variants", [])
    ]
    unknown_catalog_ids = sorted({item.get("capability_id") for item in full_variants if item.get("capability_id") not in known_ids})
    if unknown_catalog_ids:
        issues.append(_issue("UNKNOWN_WORKBENCH_CAPABILITY", "error", "Workbench references unknown Capability IDs.", capability_ids=unknown_catalog_ids))

    default_leaks = sorted(
        item["capability_id"]
        for item in default_variants
        if item.get("lifecycle_status") in {"deprecated", "internal", "archived", "blocked"}
        or item.get("product_tier") in {"compatibility", "explicit", "blocked"}
    )
    if default_leaks:
        issues.append(_issue("NON_DEFAULT_CAPABILITY_LEAK", "error", "Compatibility, explicit or blocked capability leaked into the default workbench.", capability_ids=default_leaks))

    templates_all = list_scenario_templates().get("templates", [])
    templates_default = list_scenario_templates(product_visible_only=True).get("templates", [])
    unknown_template_ids = sorted({item.get("capability_id") for item in templates_all if item.get("capability_id") not in known_ids})
    if unknown_template_ids:
        issues.append(_issue("UNKNOWN_TEMPLATE_CAPABILITY", "error", "Scenario template references unknown Capability IDs.", capability_ids=unknown_template_ids))

    default_template_leaks: list[str] = []
    for item in templates_default:
        contract = contracts.get(str(item.get("capability_id") or ""))
        if contract and (
            contract.lifecycle_status in {"deprecated", "internal", "archived", "blocked"}
            or contract.product_tier in {"compatibility", "explicit", "blocked"}
        ):
            default_template_leaks.append(str(item.get("template_id")))
    if default_template_leaks:
        issues.append(_issue("NON_DEFAULT_TEMPLATE_LEAK", "error", "Historical/explicit template leaked into the default template list.", template_ids=sorted(default_template_leaks)))

    expected_primary = {
        "whole_spacecraft": "whole_spacecraft.unified_native.v1",
        "subsystem.adcs": "subsystem.adcs_unified_native.v1",
        "subsystem.eps": "subsystem.eps.unified_native.v1",
        "subsystem.comm_data": "subsystem.comm_data.unified_native.v1",
        "subsystem.propulsion": "subsystem.propulsion.unified_native.v1",
    }
    object_map = {item.get("object_id"): item for item in default_catalog.get("objects", [])}
    wrong_primary = {
        object_id: {
            "expected": capability_id,
            "actual": (object_map.get(object_id) or {}).get("primary_capability_id"),
        }
        for object_id, capability_id in expected_primary.items()
        if (object_map.get(object_id) or {}).get("primary_capability_id") != capability_id
    }
    if wrong_primary:
        issues.append(_issue("RECOMMENDED_PRIMARY_MISMATCH", "error", "Recommended unified capability is not the default object entry.", mismatches=wrong_primary))

    closure = product_capability_closure()
    recommended_ids = list(closure.get("recommended_capability_ids", []))
    readme_path = repo / "README.md"
    readme_text = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""
    missing_readme_ids = sorted(capability_id for capability_id in recommended_ids if capability_id not in readme_text)
    if not readme_path.exists():
        issues.append(_issue("README_MISSING", "error", "README.md is missing."))
    elif missing_readme_ids:
        issues.append(_issue("README_RECOMMENDED_CAPABILITY_GAP", "error", "README does not name every recommended Capability ID.", capability_ids=missing_readme_ids))

    errors = [item for item in issues if item["severity"] == "error"]
    warnings = [item for item in issues if item["severity"] == "warning"]
    return {
        "schema_version": CATALOG_CONSISTENCY_SCHEMA_VERSION,
        "release_version": RELEASE_VERSION,
        "status": "PASS" if not errors else "FAIL",
        "registry_capability_count": len(known_ids),
        "default_workbench_variant_count": len(default_variants),
        "full_workbench_variant_count": len(full_variants),
        "all_template_count": len(templates_all),
        "default_template_count": len(templates_default),
        "recommended_capability_ids": recommended_ids,
        "expected_primary_capability_ids": expected_primary,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "issues": issues,
    }


def write_catalog_document_consistency(output_dir: str | Path, root: str | Path | None = None) -> dict[str, str]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    payload = catalog_document_consistency(root)
    json_path = target / "catalog_document_consistency.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path = target / "catalog_document_consistency.md"
    lines = [
        "# 能力目录、场景与文档一致性检查",
        "",
        f"结论：**{payload['status']}**",
        "",
        f"- Registry Capability：{payload['registry_capability_count']}",
        f"- 默认工作台变体：{payload['default_workbench_variant_count']}",
        f"- 完整兼容视图变体：{payload['full_workbench_variant_count']}",
        f"- 场景模板：{payload['all_template_count']}（默认可见 {payload['default_template_count']}）",
        f"- 错误：{payload['error_count']}；警告：{payload['warning_count']}",
        "",
        "## 检查项",
        "",
    ]
    if payload["issues"]:
        lines.extend(["| 级别 | 编码 | 说明 |", "|---|---|---|"])
        for item in payload["issues"]:
            lines.append(f"| {item['severity']} | `{item['code']}` | {item['message']} |")
    else:
        lines.append("未发现目录、模板和发布说明之间的不一致。")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


__all__ = [
    "CATALOG_CONSISTENCY_SCHEMA_VERSION",
    "catalog_document_consistency",
    "write_catalog_document_consistency",
]
