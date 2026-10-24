"""Conservative cross-level parameter owner inventory; never claims physics approval.

This audit identifies static parameter reads by each declared adapter and keeps
native implementation / physical perturbation evidence explicitly separate.
Dynamic access, helper handoffs and runtime wiring require manual review.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Mapping

from sat_sim.validation.parameter_surface_audit import build_parameter_surface_audit

SCHEMA_VERSION = "sat-sim.cross-level-owner-audit.v1"


def parameter_reads(source: str) -> set[str]:
    """Return literal params.get/key reads; exclude unrelated mappings."""
    tree = ast.parse(source)
    keys: set[str] = set()
    for node in ast.walk(tree):
        key: ast.AST | None = None
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "params" and node.func.attr == "get"
                and node.args):
            key = node.args[0]
        elif (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
              and node.value.id == "params"):
            key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            keys.add(key.value)
    return keys


def build_cross_level_owner_audit(*, root: Path | None = None) -> dict[str, Any]:
    repo = root or Path(__file__).resolve().parents[3]
    from sat_sim.capability_registry import list_capabilities

    contracts = {c.capability_id: c for c in list_capabilities()}
    original = build_parameter_surface_audit(root=repo)
    rows: list[dict[str, Any]] = []
    for cap in original["capabilities"]:
        cid = cap["capability_id"]
        contract = contracts[cid]
        adapter = contract.data.get("adapter") or {}
        class_path = adapter.get("class_path") if isinstance(adapter, Mapping) else None
        adapter_path = (Path("src") / (str(class_path).rsplit(".", 1)[0].replace(".", "/") + ".py")
                        if class_path and "." in str(class_path) else None)
        parsed: set[str] = set()
        adapter_status = "not_declared"
        if adapter_path:
            source_path = repo / adapter_path
            if source_path.is_file():
                try:
                    parsed = parameter_reads(source_path.read_text(encoding="utf-8"))
                    adapter_status = "static_reads_inspected"
                except (SyntaxError, UnicodeError):
                    adapter_status = "parse_failed"
            else:
                adapter_status = "file_missing"
        implementation = contract.data.get("implementation") or {}
        for item in cap["parameters"]:
            key = item["key"]
            read = ("literal_adapter_read" if key in parsed else "no_literal_adapter_read")
            if cid == "whole_spacecraft.unified_native.v1":
                read = item["native_source_lookup"]
            rows.append({
                "capability_id": cid,
                "level": cap["level"],
                "parameter": key,
                "unit": item["registry_unit"],
                "form_unit": item["form_unit"],
                "adapter_class": class_path,
                "adapter_source": str(adapter_path) if adapter_path else None,
                "adapter_inspection": adapter_status,
                "static_read": read,
                "implementation_requires_basilisk": bool(implementation.get("basilisk_required", False)),
                "native_model_owner": ("backend_dependent_project_reference_or_official_Basilisk"
                                       if cid == "component.reaction_wheel.v1" else "unverified"),
                "dynamic_effect_evidence": "not_provided",
                "cross_level_equivalence": "not_verified",
            })
    rw = next((c for c in original["capabilities"] if c["capability_id"] == "component.reaction_wheel.v1"), None)
    return {
        "schema_version": SCHEMA_VERSION,
        "structure_status": original["status"],
        "physics_status": "NOT_VERIFIED",
        "capability_count": len(original["capabilities"]),
        "parameter_rows": rows,
        "rw_independent_entry": {
            "capability_id": "component.reaction_wheel.v1",
            "basilisk_required": rw["basilisk_required"] if rw else None,
            "default_physics_backend": "project_reference",
            "native_opt_in_available": "basilisk_native" in
                ((contracts["component.reaction_wheel.v1"].data.get("parameters") or {})
                 .get("physics_backend") or {}).get("enum", []),
            "native_supported_wheel_counts": [1, 3, 4],
            "native_physics_verified_by_this_static_audit": False,
            "cross_level_physical_parity_verified": False,
            "reason": "Default remains project Python reference. Explicit basilisk_native nominal uses genuine SWIG 1/3/4 RW models; physical evidence lives in separate CI, and whole-spacecraft HR16 equivalence is unverified.",
        },
        "limitations": [
            "Literal adapter reads are evidence of source references only, not model consumption.",
            "An imported helper or dynamic dictionary alias may consume a key without appearing in this inventory.",
            "Native model owner, physical perturbation, and cross-level equivalence require separate executed evidence.",
            "No registry capability fidelity flag is modified by this audit.",
        ],
    }


def main() -> int:
    report = build_cross_level_owner_audit()
    out = Path("reports/cross_level_owner_audit")
    out.mkdir(parents=True, exist_ok=True)
    (out / "owner_matrix.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Cross-level parameter owner inventory", "",
        "Structural contract status: " + report["structure_status"],
        "Physics status: **NOT VERIFIED**", "",
        "| Capability | Parameter | Unit | Adapter static read | Basilisk required | Physics |",
        "|---|---|---|---|---|---|",
    ]
    for row in report["parameter_rows"]:
        lines.append("| " + " | ".join([
            str(row["capability_id"]), str(row["parameter"]), str(row["unit"]),
            str(row["static_read"]), str(row["implementation_requires_basilisk"]),
            str(row["dynamic_effect_evidence"]),
        ]) + " |")
    lines.extend(["", "## Limitations", ""])
    lines.extend("- " + x for x in report["limitations"])
    (out / "owner_matrix.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("capabilities:", report["capability_count"], "parameters:", len(report["parameter_rows"]),
          "physics:", report["physics_status"])
    return 0 if report["structure_status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
