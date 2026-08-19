"""High-fidelity request guardrails for Agent script generation.

A5 does not add high-fidelity physics.  It adds a deterministic policy layer so
requests that ask for engineering/mission-grade high fidelity are not routed to
basic or synthetic capabilities as if they satisfied that fidelity level.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

HIGH_FIDELITY_CUES = (
    "high fidelity", "high-fidelity", "engineering grade", "flight grade",
    "mission grade", "certification", "certified", "validated against flight",
    "工程级", "高保真", "全卫星高保真", "整星高保真", "完整高保真",
    "飞控级", "认证级", "真实型号", "真实卫星", "全物理", "完整闭环",
)

DOMAIN_CUES: dict[str, tuple[str, ...]] = {
    "adcs_closed_loop": ("adcs", "姿控", "姿态控制", "星敏", "陀螺", "fsw", "闭环", "closed loop"),
    "whole_spacecraft": ("whole spacecraft", "spacecraft", "整星", "全星", "全卫星", "whole-spacecraft"),
    "propulsion_coupled": ("delta-v", "delta v", "变轨", "轨道机动", "连续推力", "力耦合"),
    "thermal_network": ("热网络", "finite element", "有限元", "多节点热", "thermal network"),
    "rf_high_fidelity": ("rf propagation", "多普勒", "doppler", "自适应编码", "调制编码", "天气衰减"),
}


@dataclass(frozen=True)
class FidelityDecision:
    requested_level: str
    resolved_level: str
    supported: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)
    requested_domains: tuple[str, ...] = field(default_factory=tuple)
    selected_capability_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _contains_any(text: str, words: Sequence[str]) -> tuple[str, ...]:
    lower = text.lower()
    return tuple(word for word in words if word.lower() in lower)


def evaluate_fidelity_request(request_text: str, *, selected_capability_id: str | None = None, capability_contract: Mapping[str, Any] | None = None) -> FidelityDecision:
    cues = _contains_any(request_text, HIGH_FIDELITY_CUES)
    domains: list[str] = []
    for domain, words in DOMAIN_CUES.items():
        if _contains_any(request_text, words):
            domains.append(domain)
    contract_level = str((capability_contract or {}).get("fidelity_level") or "basic")
    if cues:
        reasons = ["request uses high-fidelity/engineering-grade language"]
        if selected_capability_id:
            reasons.append(f"selected capability {selected_capability_id} is not declared high-fidelity")
        return FidelityDecision(
            requested_level="high",
            resolved_level="unsupported",
            supported=False,
            reasons=tuple(reasons),
            requested_domains=tuple(domains),
            selected_capability_id=selected_capability_id,
        )
    return FidelityDecision(
        requested_level="basic_or_unspecified",
        resolved_level=contract_level,
        supported=True,
        reasons=("no high-fidelity request cue detected",),
        requested_domains=tuple(domains),
        selected_capability_id=selected_capability_id,
    )


__all__ = ["FidelityDecision", "evaluate_fidelity_request", "HIGH_FIDELITY_CUES", "DOMAIN_CUES"]
