"""Capability source, lifecycle and runtime-governance matrix.

The executable Capability Registry remains authoritative.  This module derives a
single product-facing governance view used by the workbench, Agent routing,
release evidence and audits.  It deliberately distinguishes official Basilisk
modules, project SysModels in one Basilisk runtime, legacy bridges and project
equation engines.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .capability_registry import CapabilityContract, list_capabilities

LIFECYCLE_STATUSES = {"active", "deprecated", "internal", "archived", "blocked"}
PRODUCT_TIERS = {"recommended", "standard", "explicit", "compatibility", "blocked"}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def lifecycle_product_tier(contract: CapabilityContract) -> str:
    lifecycle = contract.lifecycle
    explicit = str(lifecycle.get("product_tier") or "").strip().lower()
    if explicit:
        return explicit
    status = contract.lifecycle_status
    if status == "active":
        return "recommended" if bool(lifecycle.get("recommended")) else "standard"
    if status in {"deprecated", "internal", "archived"}:
        return "compatibility"
    if status == "blocked":
        return "blocked"
    return "explicit"


def is_recommended(contract: CapabilityContract) -> bool:
    return contract.lifecycle_status == "active" and lifecycle_product_tier(contract) == "recommended"


def runtime_origin(contract: CapabilityContract) -> str:
    implementation = _mapping(contract.data.get("implementation"))
    backend = str(implementation.get("backend_type") or "").strip().lower()
    boundary = str(implementation.get("source_boundary") or "").strip().lower()
    if backend == "basilisk_native":
        return "basilisk_official_native"
    if backend == "basilisk_native_mixed" or "project_sysmodel" in boundary:
        return "basilisk_official_plus_project_sysmodels"
    if backend == "local_physics_bridge" or bool(implementation.get("requires_allow_proxy")):
        return "legacy_bridge"
    if backend == "project_coupled_equation_engine":
        return "project_equation_engine"
    if bool(implementation.get("uses_legacy_runner")):
        return "legacy_runner"
    if bool(implementation.get("basilisk_required")):
        return "basilisk_native_candidate"
    model_modules = implementation.get("model_modules")
    if isinstance(model_modules, list) and model_modules:
        return "project_runtime"
    return "project_adapter"


def _official_mc_payload(capability_id: str) -> dict[str, Any]:
    try:
        from .experiments.native_controller_registry import native_controller_contract
        contract = native_controller_contract(capability_id)
        return contract.to_dict() if contract else {"supported": False, "parameter_paths": []}
    except Exception:
        return {"supported": False, "parameter_paths": []}


def capability_governance_record(contract: CapabilityContract) -> dict[str, Any]:
    implementation = _mapping(contract.data.get("implementation"))
    lifecycle = contract.lifecycle
    mc = _official_mc_payload(contract.capability_id)
    return {
        "capability_id": contract.capability_id,
        "name": contract.data.get("name") or contract.capability_id,
        "level": contract.target_level,
        "domain": contract.data.get("domain"),
        "target": contract.target_name,
        "trust_level": contract.trust_level,
        "lifecycle_status": contract.lifecycle_status,
        "product_tier": lifecycle_product_tier(contract),
        "recommended": is_recommended(contract),
        "replacement_capability_id": contract.replacement_capability_id,
        "exposed_to_agent": contract.exposed_to_agent,
        "exposed_to_real_llm_eval": contract.exposed_to_real_llm_eval,
        "lifecycle_reason": lifecycle.get("reason"),
        "runtime_origin": runtime_origin(contract),
        "backend_type": implementation.get("backend_type"),
        "basilisk_required": bool(implementation.get("basilisk_required", False)),
        "uses_legacy_runner": bool(implementation.get("uses_legacy_runner", False)),
        "requires_allow_proxy": bool(implementation.get("requires_allow_proxy", False)),
        "modifier_application": implementation.get("modifier_application"),
        "source_boundary": implementation.get("source_boundary"),
        "official_monte_carlo": mc,
        "fault_supported": bool(_mapping(contract.data.get("modes")).get("fault", {}).get("supported", False)) if isinstance(_mapping(contract.data.get("modes")).get("fault"), Mapping) else False,
        "degradation_supported": bool(_mapping(contract.data.get("modes")).get("degradation", {}).get("supported", False)) if isinstance(_mapping(contract.data.get("modes")).get("degradation"), Mapping) else False,
        "constraint_supported": bool(_mapping(contract.data.get("modes")).get("constraint", {}).get("supported", False)) if isinstance(_mapping(contract.data.get("modes")).get("constraint"), Mapping) else False,
    }


def capability_governance_matrix() -> dict[str, Any]:
    records = [capability_governance_record(item) for item in list_capabilities()]
    counts: dict[str, int] = {}
    for row in records:
        for key in (
            f"lifecycle:{row['lifecycle_status']}",
            f"tier:{row['product_tier']}",
            f"runtime:{row['runtime_origin']}",
        ):
            counts[key] = counts.get(key, 0) + 1
    return {
        "schema_version": "capability-governance.v1",
        "count": len(records),
        "recommended_capability_ids": [row["capability_id"] for row in records if row["recommended"]],
        "agent_exposed_count": sum(1 for row in records if row["exposed_to_agent"]),
        "official_monte_carlo_capability_ids": [row["capability_id"] for row in records if row["official_monte_carlo"].get("supported")],
        "counts": dict(sorted(counts.items())),
        "records": records,
    }


def recommended_capability_ids() -> tuple[str, ...]:
    return tuple(row["capability_id"] for row in capability_governance_matrix()["records"] if row["recommended"])


def ordered_product_capability_ids() -> tuple[str, ...]:
    rows = [row for row in capability_governance_matrix()["records"] if row["lifecycle_status"] == "active" and row["exposed_to_agent"]]
    order = {"recommended": 0, "standard": 1, "explicit": 2, "compatibility": 3, "blocked": 4}
    rows.sort(key=lambda row: (order.get(str(row["product_tier"]), 9), str(row["capability_id"])))
    return tuple(str(row["capability_id"]) for row in rows)


def validate_capability_governance() -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for contract in list_capabilities():
        lifecycle_raw = contract.data.get("lifecycle")
        if not isinstance(lifecycle_raw, Mapping):
            issues.append({"severity": "error", "capability_id": contract.capability_id, "code": "LIFECYCLE_MISSING", "message": "capability must define lifecycle metadata"})
            continue
        status = contract.lifecycle_status
        tier = lifecycle_product_tier(contract)
        implementation = _mapping(contract.data.get("implementation"))
        if status not in LIFECYCLE_STATUSES:
            issues.append({"severity": "error", "capability_id": contract.capability_id, "code": "LIFECYCLE_STATUS_INVALID", "message": status})
        if tier not in PRODUCT_TIERS:
            issues.append({"severity": "error", "capability_id": contract.capability_id, "code": "PRODUCT_TIER_INVALID", "message": tier})
        if is_recommended(contract):
            if not contract.exposed_to_agent:
                issues.append({"severity": "error", "capability_id": contract.capability_id, "code": "RECOMMENDED_NOT_AGENT_EXPOSED", "message": "recommended capability must be Agent-exposed"})
            if bool(implementation.get("uses_legacy_runner")) or bool(implementation.get("requires_allow_proxy")):
                issues.append({"severity": "error", "capability_id": contract.capability_id, "code": "RECOMMENDED_USES_COMPATIBILITY_RUNTIME", "message": "recommended capability cannot require a legacy/proxy path"})
        if status in {"deprecated", "internal"} and not contract.replacement_capability_id:
            issues.append({"severity": "warning", "capability_id": contract.capability_id, "code": "COMPATIBILITY_WITHOUT_REPLACEMENT", "message": "compatibility capability has no replacement"})
        if status == "blocked" and contract.exposed_to_agent:
            issues.append({"severity": "error", "capability_id": contract.capability_id, "code": "BLOCKED_AGENT_EXPOSED", "message": "blocked capability cannot be exposed"})
        try:
            from .experiments.native_controller_registry import native_controller_contract
            native_contract = native_controller_contract(contract.capability_id)
            if native_contract is not None:
                from .form_schema import capability_form_schema
                allowed_paths = {str(field.get("path") or "") for field in capability_form_schema(contract.capability_id).get("fields", []) if isinstance(field, Mapping)}
                missing_paths = sorted(set(native_contract.path_map) - allowed_paths)
                if missing_paths:
                    issues.append({
                        "severity": "error",
                        "capability_id": contract.capability_id,
                        "code": "MONTE_CARLO_PATH_NOT_SCHEMA_EXPOSED",
                        "message": ", ".join(missing_paths),
                    })
                if runtime_origin(contract) not in {"basilisk_official_native", "basilisk_official_plus_project_sysmodels"}:
                    issues.append({
                        "severity": "error",
                        "capability_id": contract.capability_id,
                        "code": "MONTE_CARLO_NON_NATIVE_CAPABILITY",
                        "message": "official Controller may only be registered for verified native Basilisk runtimes",
                    })
        except Exception as exc:
            issues.append({
                "severity": "error",
                "capability_id": contract.capability_id,
                "code": "MONTE_CARLO_GOVERNANCE_CHECK_FAILED",
                "message": str(exc),
            })
    return issues


def write_capability_governance(output_dir: str | Path) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    matrix = capability_governance_matrix()
    issues = validate_capability_governance()
    json_path = root / "capability_governance_matrix.json"
    json_path.write_text(json.dumps({**matrix, "validation_issues": issues}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path = root / "capability_governance_matrix.md"
    lines = [
        "# Capability 来源、生命周期与运行后端矩阵",
        "",
        f"能力总数：**{matrix['count']}**；推荐主线：**{len(matrix['recommended_capability_ids'])}**；官方 MonteCarloController：**{len(matrix['official_monte_carlo_capability_ids'])}**。",
        "",
        "| Capability | 层级 | 生命周期 | 产品等级 | 运行来源 | 后端 | Agent | 官方MC | 替代能力 |",
        "|---|---|---|---|---|---|---:|---:|---|",
    ]
    for row in matrix["records"]:
        lines.append(
            f"| `{row['capability_id']}` | {row['level']} | {row['lifecycle_status']} | {row['product_tier']} | {row['runtime_origin']} | {row['backend_type'] or '-'} | {'是' if row['exposed_to_agent'] else '否'} | {'是' if row['official_monte_carlo'].get('supported') else '否'} | `{row['replacement_capability_id'] or '-'}` |"
        )
    lines.extend(["", "## 治理校验", ""])
    if issues:
        for issue in issues:
            lines.append(f"- **{issue['severity'].upper()}** `{issue['capability_id']}` `{issue['code']}`：{issue['message']}")
    else:
        lines.append("- 无治理错误或警告。")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


__all__ = [
    "capability_governance_record",
    "capability_governance_matrix",
    "recommended_capability_ids",
    "ordered_product_capability_ids",
    "runtime_origin",
    "lifecycle_product_tier",
    "validate_capability_governance",
    "write_capability_governance",
]
