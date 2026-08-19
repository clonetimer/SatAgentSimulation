"""Release audit for model-library to Capability integration contracts."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .capability_registry import list_capabilities, validate_capability_integration_registry
from .physical_couplings import physical_coupling_catalog_payload

SCHEMA_VERSION = "sat-sim.capability-integration-audit.v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_capability_integration_audit() -> dict[str, Any]:
    contracts = list_capabilities()
    issues = validate_capability_integration_registry()
    rows: list[dict[str, Any]] = []
    for contract in contracts:
        integration = contract.integration_contract
        rows.append({
            "capability_id": contract.capability_id,
            "level": contract.target_level,
            "domain": contract.data.get("domain"),
            "lifecycle_status": contract.lifecycle_status,
            "product_tier": contract.product_tier,
            "recommended": contract.recommended,
            "adapter": contract.adapter_class_path,
            "source_modules": list(integration.source_modules),
            "implementation_class": integration.implementation_class,
            "supported_couplings": list(integration.supported_couplings),
            "unsupported_couplings": list(integration.unsupported_couplings),
            "limitations": list(integration.limitations),
        })
    default_rows = [row for row in rows if row["lifecycle_status"] == "active" and row["product_tier"] in {"recommended", "standard"}]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": _utc_now(),
        "status": "PASS" if not issues else "FAIL",
        "capability_count": len(rows),
        "default_product_capability_count": len(default_rows),
        "recommended_capability_count": sum(1 for row in rows if row["recommended"]),
        "issue_count": len(issues),
        "issues": [item.to_dict() for item in issues],
        "physical_coupling_catalog": physical_coupling_catalog_payload(),
        "capabilities": rows,
        "policy": {
            "whole_spacecraft_is_not_coupling_wildcard": True,
            "source_modules_authority": "implementation.model_modules",
            "undeclared_coupling_is_rejected": True,
            "required_couplings_persisted_in_taskspec": True,
        },
    }


def render_capability_integration_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Capability—基础模型库一致性审计",
        "",
        f"- 状态：**{report['status']}**",
        f"- Capability：{report['capability_count']}",
        f"- 默认产品能力：{report['default_product_capability_count']}",
        f"- 推荐能力：{report['recommended_capability_count']}",
        f"- 问题：{report['issue_count']}",
        "",
        "| Capability | 层级 | 实现类别 | 基础模块数 | 支持耦合数 | 产品级别 |",
        "|---|---|---|---:|---:|---|",
    ]
    for row in report["capabilities"]:
        lines.append(
            f"| `{row['capability_id']}` | {row['level']} | {row['implementation_class']} | "
            f"{len(row['source_modules'])} | {len(row['supported_couplings'])} | {row['product_tier']} |"
        )
    lines += [
        "",
        "## 门禁原则",
        "",
        "- 整星层级不自动等于支持全部物理耦合。",
        "- TaskSpec 的 `mission.required_couplings` 必须由所选 Capability 显式支持。",
        "- 基础模块清单以 `implementation.model_modules` 为唯一来源。",
        "- 未声明、未知或缺失的因果链在运行前阻断。",
        "",
    ]
    return "\n".join(lines)


__all__ = ["SCHEMA_VERSION", "build_capability_integration_audit", "render_capability_integration_markdown"]
