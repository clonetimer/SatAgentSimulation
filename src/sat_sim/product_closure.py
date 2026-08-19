"""Product-entry closure decisions for the current release.

The Capability Registry remains authoritative for execution.  This module adds
an explicit product disposition so recommended, standard, explicit,
compatibility and blocked entries cannot silently leak into the default UI.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model_governance import capability_governance_matrix
from .release_closure import RELEASE_VERSION

PRODUCT_CLOSURE_SCHEMA_VERSION = "sat-sim.product-capability-closure.v1"

# Decisions for the physical-model migration backlog originally recorded in
# v0.5.5.1.  Completed items point at the promoted capability.  Deferred items
# remain supported but are not misrepresented as recommended native runtimes.
_BACKLOG_DECISIONS: tuple[dict[str, Any], ...] = (
    {
        "item": "propulsion_into_whole_unified_runtime",
        "decision": "migrated",
        "status": "closed",
        "capability_id": "whole_spacecraft.unified_native.v1",
        "reason": "Propulsion is wired into the recommended unified whole-spacecraft runtime.",
    },
    {
        "item": "eps_standalone_unified_runtime",
        "decision": "migrated",
        "status": "closed",
        "capability_id": "subsystem.eps.unified_native.v1",
        "reason": "Promoted to the recommended EPS unified runtime.",
    },
    {
        "item": "comm_data_standalone_unified_runtime",
        "decision": "migrated",
        "status": "closed",
        "capability_id": "subsystem.comm_data.unified_native.v1",
        "reason": "Promoted to the recommended communication/data unified runtime.",
    },
    {
        "item": "payload_standalone_unified_runtime",
        "decision": "retain_standard",
        "status": "closed_for_release",
        "capability_id": "subsystem.payload.source_native.v1",
        "reason": "Retained as a standard project runtime; no unsupported native promotion is claimed.",
    },
    {
        "item": "thermal_standalone_unified_runtime",
        "decision": "retain_standard",
        "status": "closed_for_release",
        "capability_id": "subsystem.thermal.source_native.v1",
        "reason": "Retained as a standard project runtime; native migration is deferred to a future evidence-backed release.",
    },
    {
        "item": "orbit_environment_governance_closure",
        "decision": "standard_plus_explicit",
        "status": "closed",
        "capability_id": "orbit_environment.orbit_fidelity.v1",
        "explicit_capability_id": "orbit_environment.basilisk_hf.v1",
        "reason": "The orbit-fidelity product model remains standard; the Basilisk high-fidelity candidate is explicit opt-in and legacy LEO/medium models are hidden compatibility paths.",
    },
    {
        "item": "additional_adcs_modes",
        "decision": "recommended_plus_standard",
        "status": "closed",
        "capability_id": "subsystem.adcs_unified_native.v1",
        "standard_capability_id": "subsystem.adcs_fidelity.v1",
        "reason": "Unified native ADCS is recommended; the engineering-fidelity model remains standard and legacy bridge/basic modes are compatibility-only.",
    },
)


def _disposition(row: dict[str, Any]) -> tuple[str, bool, str]:
    lifecycle = str(row.get("lifecycle_status") or "")
    tier = str(row.get("product_tier") or "")
    exposed_to_agent = bool(row.get("exposed_to_agent", True))
    target_level = str(row.get("level") or row.get("target_level") or "")
    backend_type = str(row.get("backend_type") or "")
    if lifecycle == "blocked" or tier == "blocked":
        return "blocked", False, "Blocked by governance; execution and product entry are disabled."
    if lifecycle in {"deprecated", "archived"}:
        return "compatibility", False, "Retained only for reproducibility; hidden from default product entry."
    if lifecycle == "internal" or target_level == "reference" or backend_type == "report_only":
        return "blocked_product_entry", False, "Internal/report-only building block; not a direct simulation product entry."
    if tier == "compatibility":
        return "compatibility", False, "Compatibility path; hidden from default product entry."
    if tier == "explicit" or not exposed_to_agent:
        return "explicit_opt_in", False, "Advanced or non-Agent capability; shown only when explicitly requested."
    if bool(row.get("recommended")):
        return "recommended_mainline", True, "Recommended default product path."
    return "supported_standard", True, "Supported standard capability; selectable but not represented as the default recommended path."


def product_capability_closure() -> dict[str, Any]:
    matrix = capability_governance_matrix()
    records: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for source in matrix.get("records", []):
        row = dict(source)
        disposition, default_visible, reason = _disposition(row)
        row.update(
            {
                "product_disposition": disposition,
                "default_product_visible": default_visible,
                "product_entry_reason": reason,
            }
        )
        counts[disposition] = counts.get(disposition, 0) + 1
        records.append(row)
    return {
        "schema_version": PRODUCT_CLOSURE_SCHEMA_VERSION,
        "release_version": RELEASE_VERSION,
        "closure_baseline_version": "0.5.5.9",
        "registry_authoritative": True,
        "default_entry_policy": "recommended_and_supported_standard_only",
        "counts": dict(sorted(counts.items())),
        "recommended_capability_ids": [r["capability_id"] for r in records if r["product_disposition"] == "recommended_mainline"],
        "default_visible_capability_ids": [r["capability_id"] for r in records if r["default_product_visible"]],
        "hidden_compatibility_capability_ids": [r["capability_id"] for r in records if r["product_disposition"] == "compatibility"],
        "blocked_product_entry_capability_ids": [r["capability_id"] for r in records if r["product_disposition"] in {"blocked", "blocked_product_entry"}],
        "migration_backlog_decisions": [dict(item) for item in _BACKLOG_DECISIONS],
        "records": records,
    }


def write_product_capability_closure(output_dir: str | Path) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    payload = product_capability_closure()
    json_path = root / "product_capability_closure.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path = root / "product_capability_closure.md"
    lines = [
        "# 产品能力治理最终清单",
        "",
        "默认入口仅展示推荐主线和受支持标准能力；显式、兼容、内部和阻断能力不得静默进入默认工作台或 Agent 路由。",
        "",
        "## 物理能力迁移遗留项",
        "",
        "| 遗留项 | 决策 | 状态 | 对应能力 | 说明 |",
        "|---|---|---|---|---|",
    ]
    for item in payload["migration_backlog_decisions"]:
        capability = item.get("capability_id") or "-"
        lines.append(f"| `{item['item']}` | {item['decision']} | {item['status']} | `{capability}` | {item['reason']} |")
    lines.extend([
        "",
        "## Capability 产品处置",
        "",
        "| Capability | 生命周期 | 产品等级 | 收口处置 | 默认入口 | 替代能力 |",
        "|---|---|---|---|---:|---|",
    ])
    for row in payload["records"]:
        lines.append(
            f"| `{row['capability_id']}` | {row['lifecycle_status']} | {row['product_tier']} | {row['product_disposition']} | {'是' if row['default_product_visible'] else '否'} | `{row.get('replacement_capability_id') or '-'}` |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


__all__ = [
    "PRODUCT_CLOSURE_SCHEMA_VERSION",
    "product_capability_closure",
    "write_product_capability_closure",
]
