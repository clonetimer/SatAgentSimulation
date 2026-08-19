"""Chinese product-facing workbench catalog.

The Capability Registry remains the executable source of truth.  This module
adds a presentation layer so users see physical simulation objects (one whole
spacecraft, six subsystems and twenty-four concrete components) instead of a
flat list of implementation variants.
"""
from __future__ import annotations

from typing import Any

from .capability_registry import active_capability_ids, list_capabilities

WORKBENCH_CATALOG_VERSION = "workbench-catalog.v2"

_VARIANT_LABELS = {
    "whole_spacecraft.unified_native.v1": "整星统一 Basilisk 运行图（推荐）",
    "subsystem.adcs_unified_native.v1": "ADCS 统一 Basilisk 闭环（推荐）",
    "subsystem.eps.unified_native.v1": "EPS 统一 Basilisk 运行图（推荐）",
    "subsystem.comm_data.unified_native.v1": "通信数据统一 Basilisk 运行图（推荐）",
    "subsystem.propulsion.unified_native.v1": "推进统一 Basilisk 运行图（推荐）",
    "whole_spacecraft.bsksim_foundation.v1": "Basilisk 原生基础参考场景",
    "whole_spacecraft.composite_digital_twin.v1": "综合整星数字样机",
    "whole_spacecraft.orbit_adcs_fidelity.v1": "轨道—姿态联合场景",
    "whole_spacecraft.power_thermal_orbit_coupled.v1": "电源—热控—轨道联合场景",
    "whole_spacecraft.comm_payload_mission_coupled.v1": "通信—载荷任务链场景",
    "whole_spacecraft.maneuver_orbit_attitude.v1": "推进—轨道—姿态机动场景",
    "subsystem.adcs_fidelity.v1": "姿态与轨道控制工程模型",
    "component.mtb.v1": "磁力矩器独立部件模型",
    "subsystem.propulsion.source_native.v1": "推进分系统原生模型",
    "subsystem.eps.basic.v1": "电源分系统基础模型",
    "subsystem.eps.source_native.v1": "电源分系统原生模型",
    "subsystem.thermal.basic_lumped.v1": "热控集中参数模型",
    "subsystem.thermal.source_native.v1": "热控分系统原生模型",
    "subsystem.payload.source_native.v1": "载荷分系统原生模型",
    "subsystem.comm.basic_ground_pass.v1": "通信地面过站模型",
    "subsystem.comm_data.source_native.v1": "通信与数据管理原生模型",
    "orbit_environment.orbit_fidelity.v1": "轨道与空间环境模型",
}

_COMPONENTS = (
    ("antenna", "天线", "通信/数据", "天线增益与指向损失"),
    ("battery", "蓄电池", "电源", "储能、充放电、容量与故障"),
    ("cmg", "控制力矩陀螺", "姿态", "CMG/VSCMG 执行机构"),
    ("data_queue", "数据队列", "通信/数据", "数据生成、排队与下传"),
    ("fuel_tank", "燃料贮箱", "推进", "推进剂质量、泄漏与供给"),
    ("ground_station", "地面站", "通信/数据", "地面站可见性与过站"),
    ("heater", "加热器", "热控", "加热功率与开关控制"),
    ("imu", "惯性测量单元", "姿态", "角速度与惯性测量"),
    ("link_budget", "链路预算", "通信/数据", "接收功率、损耗与链路余量"),
    ("magnetometer", "磁强计", "姿态", "地磁测量与噪声/偏置"),
    ("mtb", "磁力矩器", "姿态", "磁矩执行与卸载"),
    ("onboard_storage", "星上存储", "通信/数据", "存储容量、占用与溢出"),
    ("payload", "载荷仪器", "载荷", "载荷启停、功耗与数据生成"),
    ("payload_sensor", "载荷传感器", "载荷", "观测质量与数据率"),
    ("pdu", "配电单元", "电源", "配电、通道与负载卸载"),
    ("power_sink", "功率负载", "电源", "设备功耗与工作模式"),
    ("radiator", "散热器", "热控", "辐射散热与性能退化"),
    ("reaction_wheel", "反作用轮", "姿态", "轮速、力矩、摩擦与卡滞"),
    ("solar_panel", "太阳电池阵", "电源", "发电、阴影与效率退化"),
    ("star_tracker", "星敏感器", "姿态", "高精度姿态测量"),
    ("sun_sensor", "太阳敏感器", "姿态", "太阳矢量测量"),
    ("thermal_node", "热节点", "热控", "集中参数温度状态"),
    ("thruster", "推力器", "推进", "推力、点火、脉冲与故障"),
    ("transmitter", "发射机", "通信/数据", "发射功率、码率与下行"),
)

_SUBSYSTEMS = (
    ("adcs", "姿态与轨道控制", ("subsystem.adcs_unified_native.v1", "subsystem.adcs_fidelity.v1")),
    ("propulsion", "推进", ("subsystem.propulsion.unified_native.v1", "subsystem.propulsion.source_native.v1")),
    ("eps", "电源", ("subsystem.eps.unified_native.v1", "subsystem.eps.basic.v1", "subsystem.eps.source_native.v1")),
    ("thermal", "热控", ("subsystem.thermal.basic_lumped.v1", "subsystem.thermal.source_native.v1")),
    ("payload", "载荷", ("subsystem.payload.source_native.v1",)),
    ("comm_data", "通信与数据管理", ("subsystem.comm_data.unified_native.v1", "subsystem.comm.basic_ground_pass.v1", "subsystem.comm_data.source_native.v1")),
)

_WHOLE_VARIANTS = (
    "whole_spacecraft.unified_native.v1",
    "whole_spacecraft.composite_digital_twin.v1",
    "whole_spacecraft.orbit_adcs_fidelity.v1",
    "whole_spacecraft.power_thermal_orbit_coupled.v1",
    "whole_spacecraft.comm_payload_mission_coupled.v1",
    "whole_spacecraft.maneuver_orbit_attitude.v1",
)

_COMPONENT_RELATED_SUBSYSTEM = {
    "antenna": "subsystem.comm_data.source_native.v1",
    "battery": "subsystem.eps.source_native.v1",
    "cmg": "subsystem.adcs_fidelity.v1",
    "data_queue": "subsystem.comm_data.source_native.v1",
    "fuel_tank": "subsystem.propulsion.source_native.v1",
    "ground_station": "subsystem.comm.basic_ground_pass.v1",
    "heater": "subsystem.thermal.source_native.v1",
    "imu": "subsystem.adcs_fidelity.v1",
    "link_budget": "subsystem.comm.basic_ground_pass.v1",
    "magnetometer": "subsystem.adcs_fidelity.v1",
    "mtb": "subsystem.adcs_fidelity.v1",
    "onboard_storage": "subsystem.comm_data.source_native.v1",
    "payload": "subsystem.payload.source_native.v1",
    "payload_sensor": "subsystem.payload.source_native.v1",
    "pdu": "subsystem.eps.basic.v1",
    "power_sink": "subsystem.eps.source_native.v1",
    "radiator": "subsystem.thermal.source_native.v1",
    "reaction_wheel": "subsystem.adcs_fidelity.v1",
    "solar_panel": "subsystem.eps.source_native.v1",
    "star_tracker": "subsystem.adcs_fidelity.v1",
    "sun_sensor": "subsystem.adcs_fidelity.v1",
    "thermal_node": "subsystem.thermal.source_native.v1",
    "thruster": "subsystem.propulsion.source_native.v1",
    "transmitter": "subsystem.comm_data.source_native.v1",
}


def _variant(
    capability_id: str,
    active: set[str],
    contracts: dict[str, Any],
    *,
    include_compatibility: bool = False,
    include_explicit: bool = False,
) -> dict[str, Any] | None:
    contract = contracts.get(capability_id)
    if contract is None:
        return None
    lifecycle = contract.lifecycle_status
    tier = contract.product_tier
    if lifecycle == "blocked" or tier == "blocked":
        return None
    if lifecycle in {"deprecated", "internal", "archived"} or tier == "compatibility":
        if not include_compatibility:
            return None
    if tier == "explicit" and not include_explicit:
        return None
    return {
        "capability_id": capability_id,
        "name_zh": _VARIANT_LABELS.get(capability_id) or str(contract.data.get("name") or capability_id),
        "name_en": contract.data.get("name") or capability_id,
        "summary": contract.data.get("summary") or "",
        "agent_exposed": capability_id in active,
        "independently_executable": True,
        "lifecycle_status": contract.lifecycle_status,
        "product_tier": contract.product_tier,
        "recommended": contract.recommended,
        "trust_level": contract.trust_level,
        "backend_type": (contract.data.get("implementation") or {}).get("backend_type") if isinstance(contract.data.get("implementation"), dict) else None,
        "requires_allow_proxy": bool((contract.data.get("implementation") or {}).get("requires_allow_proxy", False)) if isinstance(contract.data.get("implementation"), dict) else False,
    }


def workbench_presentation_catalog(*, include_compatibility: bool = False, include_explicit: bool = False) -> dict[str, Any]:
    active = set(active_capability_ids())
    contracts = {item.capability_id: item for item in list_capabilities()}
    objects: list[dict[str, Any]] = []

    whole_variants = [item for cid in _WHOLE_VARIANTS if (item := _variant(cid, active, contracts, include_compatibility=include_compatibility, include_explicit=include_explicit))]
    objects.append({
        "object_id": "whole_spacecraft",
        "level": "whole_spacecraft",
        "name_zh": "整星数字样机",
        "name_en": "Whole-spacecraft digital model",
        "summary_zh": "一个整星对象，按任务选择综合、姿轨、电热轨、通信载荷或推进机动场景。",
        "variants": whole_variants,
        "primary_capability_id": "whole_spacecraft.unified_native.v1",
        "configuration_capability_id": "whole_spacecraft.unified_native.v1",
        "independently_executable": True,
    })

    for key, name_zh, variant_ids in _SUBSYSTEMS:
        variants = [item for cid in variant_ids if (item := _variant(cid, active, contracts, include_compatibility=include_compatibility, include_explicit=include_explicit))]
        objects.append({
            "object_id": f"subsystem.{key}",
            "level": "subsystem",
            "name_zh": name_zh,
            "name_en": key,
            "summary_zh": f"{name_zh}分系统；多个能力合同作为模型变体展示，不重复计数。",
            "variants": variants,
            "primary_capability_id": variants[0]["capability_id"] if variants else None,
            "configuration_capability_id": variants[0]["capability_id"] if variants else None,
            "independently_executable": bool(variants),
        })

    for key, name_zh, subsystem_zh, summary_zh in _COMPONENTS:
        cid = f"component.{key}.v1"
        variants = [item] if (item := _variant(cid, active, contracts, include_compatibility=include_compatibility, include_explicit=include_explicit)) else []
        objects.append({
            "object_id": f"component.{key}",
            "level": "component",
            "name_zh": name_zh,
            "name_en": key,
            "subsystem_zh": subsystem_zh,
            "summary_zh": summary_zh,
            "variants": variants,
            "primary_capability_id": variants[0]["capability_id"] if variants else None,
            "related_capability_id": _COMPONENT_RELATED_SUBSYSTEM[key],
            "configuration_capability_id": variants[0]["capability_id"] if variants else _COMPONENT_RELATED_SUBSYSTEM[key],
            "independently_executable": bool(variants),
            "integration_only": not bool(variants),
            "execution_scope_zh": "独立部件" if variants else "所属分系统组合",
        })

    orbit_variant = _variant("orbit_environment.orbit_fidelity.v1", active, contracts, include_compatibility=include_compatibility, include_explicit=include_explicit)
    objects.append({
        "object_id": "orbit_environment",
        "level": "orbit_environment",
        "name_zh": "轨道与空间环境",
        "name_en": "Orbit and space environment",
        "summary_zh": "轨道传播、J2、大气阻力和太阳光压等环境模型。",
        "variants": [orbit_variant] if orbit_variant else [],
        "primary_capability_id": orbit_variant["capability_id"] if orbit_variant else None,
        "configuration_capability_id": orbit_variant["capability_id"] if orbit_variant else None,
        "independently_executable": bool(orbit_variant),
    })

    visible_counts = {
        "whole_spacecraft": 1,
        "subsystem": 6,
        "component": 24,
        "orbit_environment": 1,
    }
    component_objects = [item for item in objects if item["level"] == "component"]
    default_visible_independent = sum(1 for item in component_objects if item["independently_executable"])
    default_visible_agent_exposed = sum(
        1
        for item in component_objects
        if any(bool(variant.get("agent_exposed")) for variant in item.get("variants", []))
    )

    # Report physical component capability facts independently from current
    # presentation filtering. All 24 product components now have standalone,
    # Agent-exposed source-native entries.
    all_component_contracts = [
        contracts[f"component.{key}.v1"]
        for key, *_ in _COMPONENTS
        if f"component.{key}.v1" in contracts
    ]
    independent_components = len(all_component_contracts)
    agent_exposed_components = sum(1 for item in all_component_contracts if item.capability_id in active)
    return {
        "version": WORKBENCH_CATALOG_VERSION,
        "objects": objects,
        "visible_counts": visible_counts,
        "visible_total": sum(visible_counts.values()),
        "active_agent_capability_contracts": len(active),
        "component_execution_summary": {
            "total": 24,
            "independently_executable": independent_components,
            "agent_exposed_independent": agent_exposed_components,
            "internal_independent": independent_components - agent_exposed_components,
            "integration_only": 24 - independent_components,
        },
        "default_component_visibility_summary": {
            "independently_executable": default_visible_independent,
            "agent_exposed_independent": default_visible_agent_exposed,
            "compatibility_hidden": independent_components - default_visible_independent,
            "integration_only_visible": 24 - default_visible_independent,
        },
        "visibility_policy": {
            "default": "recommended_and_supported_standard_only",
            "include_compatibility": include_compatibility,
            "include_explicit": include_explicit,
            "legacy_bridge_default_entry": False,
        },
        "semantics": {
            "object_count": "physical/product simulation objects",
            "variant_count": "executable capability contracts and fidelity/scenario variants",
            "registry_authoritative": True,
        },
    }


__all__ = ["WORKBENCH_CATALOG_VERSION", "workbench_presentation_catalog"]
