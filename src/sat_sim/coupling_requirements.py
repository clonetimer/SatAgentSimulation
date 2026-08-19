"""Deterministic physical-causality requirements for Agent requests.

The Agent may choose only a capability whose integration contract declares every
causal link implied by the request.  Whole-spacecraft level is not a wildcard:
physical closure is capability-specific and registry-authoritative.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Iterable

from .physical_couplings import KNOWN_PHYSICAL_COUPLING_IDS, LEGACY_COUPLING_ALIASES, normalize_coupling_ids


@dataclass(frozen=True)
class CouplingRequirement:
    coupling_id: str
    description: str
    matched_terms: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


_RULES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("orbit_sun_attitude_eclipse_to_eps_solar_power", "食影/姿态必须影响太阳阵列发电和电池 SOC", ("食影", "背日", "太阳入射", "eclipse", "solar incidence")),
    ("eps_pdu_to_payload_activity", "低电量/PDU 切载必须停止载荷数据生成", ("低电量关闭载荷", "切载载荷", "soc 低于阈值后禁止科学载荷", "pdu payload", "load shed payload")),
    ("eps_pdu_to_comm_activity", "低电量/PDU 切载必须停止通信下行", ("低电量关闭通信", "切载通信", "soc 低于阈值后停止下行", "pdu comm", "load shed comm")),
    ("eps_pdu_to_thermal_heaters", "PDU 加热器许可必须控制热控加热器", ("关闭加热器", "切载加热器", "heater power permission")),
    ("payload_activity_to_eps_thermal", "载荷实际活动必须反馈到电功耗和热耗", ("载荷功耗", "载荷热耗", "payload power", "payload heat")),
    ("adcs_control_effort_to_eps_thermal", "姿控控制努力必须反馈到电功耗和热耗", ("姿控功耗", "反作用轮功耗", "control effort power", "rw power")),
    ("propulsion_activity_to_eps_thermal", "推进点火必须反馈到电功耗和热耗", ("推进功耗", "推进热耗", "thruster power", "propulsion heat")),
    ("propulsion_effector_to_spacecraft", "推进推力/偏心力矩必须进入航天器动力学", ("偏心推力", "推力器失效影响姿态", "asymmetric thrust", "thruster torque")),
    ("adcs_pointing_to_payload_gate", "ADCS 指向状态必须约束载荷任务", ("指向约束载荷", "姿态误差影响载荷", "pointing gates payload")),
    ("adcs_pointing_to_comm_gate", "ADCS 指向状态必须约束通信任务", ("指向约束通信", "姿态误差影响下行", "天线指向影响下行", "pointing gates comm", "pointing gates downlink")),
    ("ground_access_to_comm_downlink", "地面站访问几何必须约束通信下行", ("地面站可见性影响下行", "过站下行", "ground access gates downlink", "ground visibility")),
    ("native_rf_quality_to_comm_downlink", "原生 RF 质量必须约束实际数据交付", ("链路预算影响下行", "cnr影响下行", "ber/per", "rf quality gates downlink")),
    ("thermal_safety_to_payload_comm_gate", "过温必须停止载荷与通信任务", ("过温关闭载荷", "过温停止下行", "thermal safety gate", "overtemperature shutdown")),
)


def required_couplings_for_text(text: str) -> tuple[CouplingRequirement, ...]:
    lower = str(text or "").lower()
    lower = re.sub(r"\beffect\s*=\s*[a-z0-9_.:-]+", " ", lower)
    found: list[CouplingRequirement] = []
    for coupling_id, description, terms in _RULES:
        matched = tuple(term for term in terms if term.lower() in lower)
        if matched:
            found.append(CouplingRequirement(coupling_id, description, matched))
    return tuple(found)


def supported_couplings_for_capability(capability_id: str | None) -> frozenset[str]:
    if not capability_id:
        return frozenset()
    from .capability_registry import get_capability
    try:
        return frozenset(get_capability(capability_id).integration_contract.supported_couplings)
    except Exception:
        return frozenset()


def unsupported_couplings_for_capability(capability_id: str | None, requirements: Iterable[CouplingRequirement]) -> tuple[CouplingRequirement, ...]:
    items = tuple(requirements)
    supported = supported_couplings_for_capability(capability_id)
    return tuple(item for item in items if item.coupling_id not in supported)


def validate_declared_required_couplings(spec: dict, *, supported_couplings: set[str] | frozenset[str] | None = None) -> list[dict[str, str]]:
    mission = spec.get("mission") if isinstance(spec.get("mission"), dict) else {}
    required = mission.get("required_couplings") or []
    if not isinstance(required, list):
        return [{"path": "$.mission.required_couplings", "message": "must be a list", "code": "COUPLING_REQUIREMENT_TYPE"}]
    normalized = normalize_coupling_ids([str(item) for item in required])
    supported = KNOWN_PHYSICAL_COUPLING_IDS if supported_couplings is None else frozenset(supported_couplings)
    issues: list[dict[str, str]] = []
    for index, cid in enumerate(normalized):
        if cid not in KNOWN_PHYSICAL_COUPLING_IDS:
            issues.append({"path": f"$.mission.required_couplings[{index}]", "message": f"unknown coupling requirement {cid!r}", "code": "UNKNOWN_COUPLING_REQUIREMENT"})
        elif cid not in supported:
            issues.append({"path": f"$.mission.required_couplings[{index}]", "message": f"selected capability does not provide required coupling {cid!r}", "code": "MISSING_PHYSICAL_CAUSAL_LINK"})
    return issues


def attach_required_couplings(spec: dict, text: str) -> tuple[dict, tuple[str, ...]]:
    """Persist Agent-detected causal requirements in Canonical TaskSpec mission."""
    import copy
    out = copy.deepcopy(dict(spec))
    detected = tuple(item.coupling_id for item in required_couplings_for_text(text))
    if not detected:
        return out, ()
    mission = out.setdefault("mission", {})
    existing = mission.get("required_couplings") if isinstance(mission, dict) else []
    normalized = list(normalize_coupling_ids([*(existing or []), *detected]))
    mission["required_couplings"] = normalized
    return out, tuple(normalized)


__all__ = [
    "CouplingRequirement",
    "required_couplings_for_text",
    "supported_couplings_for_capability",
    "unsupported_couplings_for_capability",
    "validate_declared_required_couplings",
    "attach_required_couplings",
]
