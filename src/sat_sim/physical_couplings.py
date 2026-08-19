"""Authoritative physical-coupling catalog for TaskSpec and Capability contracts.

The catalog is intentionally independent from the registry loader so it can be
used by Pydantic models, Agent policy and registry validation without circular
imports.  A coupling ID describes one executable causal edge, not a broad
marketing claim about a whole-spacecraft capability.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class PhysicalCouplingDefinition:
    coupling_id: str
    description: str
    source_domain: str
    sink_domain: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


PHYSICAL_COUPLINGS: tuple[PhysicalCouplingDefinition, ...] = (
    PhysicalCouplingDefinition("orbit_sun_attitude_eclipse_to_eps_solar_power", "食影、太阳方向和航天器姿态共同影响太阳阵列发电及电池 SOC", "orbit_environment/adcs", "eps"),
    PhysicalCouplingDefinition("eps_pdu_to_payload_activity", "EPS PDU 许可控制载荷数据生成", "eps", "payload"),
    PhysicalCouplingDefinition("eps_pdu_to_comm_activity", "EPS PDU 许可控制通信下行", "eps", "comm_data"),
    PhysicalCouplingDefinition("eps_pdu_to_thermal_heaters", "EPS PDU 加热器许可控制热控加热器", "eps", "thermal"),
    PhysicalCouplingDefinition("payload_activity_to_eps_thermal", "载荷实际活动反馈到电功耗和热耗", "payload", "eps/thermal"),
    PhysicalCouplingDefinition("adcs_control_effort_to_eps_thermal", "姿控控制努力反馈到电功耗和热耗", "adcs", "eps/thermal"),
    PhysicalCouplingDefinition("propulsion_activity_to_eps_thermal", "推进活动反馈到电功耗和热耗", "propulsion", "eps/thermal"),
    PhysicalCouplingDefinition("propulsion_effector_to_spacecraft", "推进推力和偏心力矩进入航天器六自由度动力学", "propulsion", "spacecraft_dynamics"),
    PhysicalCouplingDefinition("adcs_pointing_to_payload_gate", "ADCS 指向状态约束载荷任务", "adcs", "payload"),
    PhysicalCouplingDefinition("adcs_pointing_to_comm_gate", "ADCS 指向状态约束通信任务", "adcs", "comm_data"),
    PhysicalCouplingDefinition("ground_access_to_comm_downlink", "地面站访问几何约束通信下行", "orbit_environment/ground_station", "comm_data"),
    PhysicalCouplingDefinition("native_rf_quality_to_comm_downlink", "原生链路预算的 CNR/BER/PER 约束实际下行交付", "comm_data.rf", "comm_data.odh"),
    PhysicalCouplingDefinition("thermal_safety_to_payload_comm_gate", "热安全状态反向约束载荷与通信任务", "thermal", "payload/comm_data"),
)

KNOWN_PHYSICAL_COUPLING_IDS = frozenset(item.coupling_id for item in PHYSICAL_COUPLINGS)

# Backward-compatible input accepted during v0.5.6.4 migration.  The old ID
# represented two distinct causal edges and is expanded deterministically.
LEGACY_COUPLING_ALIASES: dict[str, tuple[str, ...]] = {
    "adcs_pointing_to_payload_comm_gate": (
        "adcs_pointing_to_payload_gate",
        "adcs_pointing_to_comm_gate",
    ),
}


def normalize_coupling_ids(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    out: list[str] = []
    for raw in values:
        value = str(raw).strip()
        expanded = LEGACY_COUPLING_ALIASES.get(value, (value,))
        for item in expanded:
            if item not in out:
                out.append(item)
    return tuple(out)


def physical_coupling_catalog_payload() -> list[dict[str, str]]:
    return [item.to_dict() for item in PHYSICAL_COUPLINGS]


__all__ = [
    "PhysicalCouplingDefinition",
    "PHYSICAL_COUPLINGS",
    "KNOWN_PHYSICAL_COUPLING_IDS",
    "LEGACY_COUPLING_ALIASES",
    "normalize_coupling_ids",
    "physical_coupling_catalog_payload",
]
