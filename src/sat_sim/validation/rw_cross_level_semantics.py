"""Trace RW component ↔ whole native semantics without claiming trajectory parity.

Unlike simple identical-name matching, this inventory preserves hardware
families, native mode, units, defaults, and source consumer for each parameter.
It is a structural claim, not a physical A/B equivalence certificate.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sat_sim.capability_registry import list_capabilities

SCHEMA_VERSION = "sat-sim.rw-cross-level-semantics.v1"

# Operator-level semantic relationships, NOT blindly interchangeable aliases.
PARAMETER_RELATIONSHIPS = (
    ("physics_backend", (), "distinct_backend_option",
     "Component default is project_reference; opt-in Basilisk native is real only for nominal 1/3/4 RW."),
    ("num_wheels", ("rw_configuration",), "topology_conditional",
     "Component 1/3/4; whole uses orthogonal_3 or pyramid_4, and no one-wheel whole graph."),
    ("wheel_inertia_kg_m2", ("rw_max_momentum_nms_0",), "different_hardware_family",
     "Component custom rwFactory Js direct; whole Honeywell_HR16 derives Js from rated H/Omega_max."),
    ("max_motor_torque_nm", ("rw_max_torque_nm", "rw_max_torque_nm_0"),
     "shared_native_attribute_different_default",
     "Both may reach native u_max, but whole supports per-RW override and downstream FSW/electrical limits."),
    ("max_speed_rad_s", ("rw_max_momentum_nms_0",), "different_hardware_family",
     "Component custom Omega_max direct; whole HR16 factory uses rated hardware and fixed speed."),
    ("initial_wheel_speeds_rad_s", ("rw_initial_speed_rad_s_0",),
     "same_units_different_defaults",
     "Both reach initial wheel Omega via rad/s->RPM factory conversion; whole default RW1=100 RPM."),
    ("command_torque_nm", (), "control_chain_not_equivalent",
     "Component constant open-loop motor command; whole uses native FSW, allocator and project command/electrical fault bridge."),
    ("torque_profile_nm", (), "native_component_not_supported",
     "Project reference supports sampled profiles; native component explicitly rejects them."),
    ("damping_nms", ("rw_viscous_friction_nms", "rw_viscous_friction_nms_0",
                     "rw_friction_mode"), "native_cviscous_conditional",
     "Both can reach native cViscous; whole requires explicit friction mode and may include Coulomb friction."),
)


def build_rw_cross_level_semantics(*, root: Path | None = None) -> dict[str, Any]:
    repo = root or Path(__file__).resolve().parents[3]
    caps = {c.capability_id: c for c in list_capabilities()}
    component = caps["component.reaction_wheel.v1"].data
    whole = caps["whole_spacecraft.unified_native.v1"].data
    component_params = component["parameters"]
    whole_params = whole["parameters"]
    whole_source = (repo / "src/sat_sim/bsk_engine/unified_native.py").read_text(encoding="utf-8")
    component_source = (repo / "src/sat_sim/bsk_engine/native_rw_component.py").read_text(encoding="utf-8")
    cluster_source = (repo / "src/sat_sim/bsk_engine/native_rw_cluster.py").read_text(encoding="utf-8")
    rows: list[dict[str, Any]] = []
    for key, related, relationship, explanation in PARAMETER_RELATIONSHIPS:
        component_field = component_params.get(key)
        whole_fields = {k: whole_params.get(k) for k in related}
        rows.append({
            "component_parameter": key,
            "component_registered": component_field is not None,
            "component_default": component_field.get("default") if component_field else None,
            "component_unit": component_field.get("unit") if component_field else None,
            "whole_parameter_candidates": list(related),
            "whole_parameter_registered": {k: whole_fields[k] is not None for k in related},
            "whole_registered_defaults": {
                k: v.get("default") if isinstance(v, dict) else None
                for k, v in whole_fields.items()
            },
            "relationship": relationship,
            "explanation": explanation,
            "physical_parity": "NOT_TESTED",
        })
    native_option = component_params.get("physics_backend", {}).get("enum", [])
    native_sources_present = all(token in src for src, token in (
        (component_source, '"custom"'),
        (cluster_source, '"custom"'),
        (whole_source, '"Honeywell_HR16"'),
        (whole_source, "rw_factory.addToSpacecraft"),
    ))
    return {
        "schema_version": SCHEMA_VERSION,
        "structure_status": "PASS" if all(row["component_registered"] for row in rows)
            and "basilisk_native" in native_option and native_sources_present else "FAIL",
        "physical_parity_status": "NOT_VERIFIED",
        "default_component_backend": component_params["physics_backend"].get("default"),
        "native_component_option_registered": "basilisk_native" in native_option,
        "supported_native_component_counts": [1, 3, 4],
        "native_whole_spacecraft_counts": [3, 4],
        "component_hardware_family": "rwFactory.custom",
        "whole_hardware_family": "rwFactory.Honeywell_HR16",
        "native_source_checks_pass": native_sources_present,
        "reference_backend_is_basilisk": False,
        "component_native_physics_verified_by_this_audit": False,
        "physical_cross_level_parity_verified_by_this_audit": False,
        "relationships": rows,
        "blocking_semantics": [
            "Js is directly configurable for custom component wheels but derived from HR16 rated momentum in whole.",
            "Whole FSW allocates commands; component accepts constant open-loop torque, so equivalent trajectory is not established.",
            "Whole current/Kt clipping and EPS power bridge are not present in standalone component.",
            "Friction default/mode and initial wheel speeds differ by level.",
        ],
        "evidence_tests": [
            "tests/test_native_rw_component_probe.py",
            "tests/test_native_rw_cluster.py",
            "tests/test_component_rw_native_entry.py",
            "tests/test_component_rw_native_cluster_entry.py",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# RW 跨层级参数语义和物理验收差集", "",
        "结构状态：" + report["structure_status"] + "；物理跨层一致性：**NOT_VERIFIED**", "",
        "| 单机字段 | 单机默认值 | 整星相关字段 | 关系 | 物理等价 |",
        "|---|---|---|---|---|",
    ]
    for item in report["relationships"]:
        lines.append("| " + " | ".join((
            item["component_parameter"], str(item["component_default"]),
            ", ".join(item["whole_parameter_candidates"]) or "无",
            item["relationship"], item["physical_parity"],
        )) + " |")
    lines.extend(["", "## 必须收敛的物理差异", ""])
    lines.extend("- " + x for x in report["blocking_semantics"])
    lines.extend(["", "当前有真实 Basilisk 部件运行证据，但本审计**不**进行跨层动力学对照。"])
    return "\n".join(lines) + "\n"


def main() -> int:
    report = build_rw_cross_level_semantics()
    out = Path("reports/rw_cross_level_semantics")
    out.mkdir(parents=True, exist_ok=True)
    (out / "matrix.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "matrix.md").write_text(render_markdown(report), encoding="utf-8")
    print("RW cross-level structural status:", report["structure_status"],
          "physical parity:", report["physical_parity_status"])
    return 0 if report["structure_status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
