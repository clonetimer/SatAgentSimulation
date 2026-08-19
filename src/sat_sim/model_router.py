"""V23 local/remote model routing for TaskSpec creation.

The router decides *which draft generator may be used*.  It never decides
capability truth, supported effects, fidelity or claims; those remain registry
and guard responsibilities.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Literal, Sequence

from .reason_codes import ReasonCode

MODEL_ROUTER_VERSION = "v23.model-router.v1"
InputKind = Literal["natural_language", "form", "task_spec", "patch"]
ModelTier = Literal["L0", "L1", "L2"]

_DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "orbit": ("轨道", "orbit", "j2", "drag", "srp", "spice", "wmm"),
    "adcs": ("姿态", "adcs", "反作用轮", "reaction wheel", "星敏", "陀螺", "pointing"),
    "eps": ("电源", "eps", "电池", "battery", "太阳阵", "solar panel", "功率"),
    "thermal": ("热控", "thermal", "温度", "heater", "散热"),
    "comm": ("通信", "comm", "链路", "downlink", "地面站", "rf"),
    "payload": ("载荷", "payload", "成像", "sensor"),
    "propulsion": ("推进", "propulsion", "推力器", "thruster", "机动", "maneuver"),
    "data": ("数据", "storage", "队列", "queue", "存储"),
}
_COMPLEX_KEYWORDS = (
    "整星", "whole spacecraft", "联合", "耦合", "combined", "多分系统", "同时",
    "组合故障", "多故障", "蒙特卡洛", "monte carlo", "参数扫描", "sweep",
    "先", "然后", "如果", "否则", "回滚", "对比", "基线",
)
_EVENT_KEYWORDS = ("故障", "fault", "退化", "degradation", "注入", "失效", "磨损", "漂移")


@dataclass(frozen=True)
class ModelRouteConfig:
    local_backend: str = "template"
    remote_backend: str | None = None
    remote_model: str | None = None
    remote_base_url: str | None = None
    remote_command: str | None = None
    force_tier: ModelTier | None = None


@dataclass(frozen=True)
class ModelRouteDecision:
    version: str
    input_kind: InputKind
    tier: ModelTier
    backend: str
    complexity_score: int
    detected_domains: tuple[str, ...] = field(default_factory=tuple)
    signals: tuple[str, ...] = field(default_factory=tuple)
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    fallback_backend: str | None = None
    remote_available: bool = False
    provider_id: str | None = None
    provider_location: str | None = None
    authority_boundary: str = "Capability Registry and deterministic guards remain authoritative."

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["detected_domains"] = list(self.detected_domains)
        payload["signals"] = list(self.signals)
        payload["reason_codes"] = list(self.reason_codes)
        return payload


def _contains(text: str, words: Sequence[str]) -> bool:
    lower = text.lower()
    return any(word.lower() in lower for word in words)


def _remote_available(config: ModelRouteConfig) -> bool:
    backend = (config.remote_backend or "").strip().lower()
    if not backend:
        return False
    if backend == "command":
        return bool(config.remote_command)
    if backend == "deepseek":
        return bool(config.remote_model or os.environ.get("SAT_SIM_DEEPSEEK_MODEL")) and bool(os.environ.get("DEEPSEEK_API_KEY"))
    if backend == "openai":
        return bool(config.remote_model or os.environ.get("SAT_SIM_OPENAI_MODEL")) and bool(os.environ.get("OPENAI_API_KEY"))
    if backend in {"openai_compatible", "qwen", "vllm"}:
        # Local/enterprise compatible gateways may intentionally use no key.
        return bool(config.remote_model and config.remote_base_url)
    return False


def route_model(
    *, input_kind: InputKind, request_text: str = "", config: ModelRouteConfig | None = None
) -> ModelRouteDecision:
    config = config or ModelRouteConfig()
    if input_kind != "natural_language":
        return ModelRouteDecision(
            MODEL_ROUTER_VERSION, input_kind, "L0", "deterministic", 0,
            reason_codes=(ReasonCode.MODEL_ROUTE_L0.value, ReasonCode.CAPABILITY_REGISTRY_AUTHORITATIVE.value),
        )

    text = request_text.strip()
    domains = tuple(name for name, words in _DOMAIN_KEYWORDS.items() if _contains(text, words))
    signals: list[str] = []
    score = 0
    if len(text) > 240:
        score += 2
        signals.append("long_request")
    if len(text) > 600:
        score += 2
        signals.append("very_long_request")
    if len(domains) >= 2:
        score += 2 + min(2, len(domains) - 2)
        signals.append("multi_domain")
    if _contains(text, _COMPLEX_KEYWORDS):
        score += 2
        signals.append("workflow_or_coupling")
    event_hits = sum(text.lower().count(word.lower()) for word in _EVENT_KEYWORDS)
    if event_hits >= 2:
        score += 2
        signals.append("multiple_event_cues")
    elif event_hits == 1:
        score += 1
        signals.append("event_cue")
    if text.count("，") + text.count(",") + text.count("；") + text.count(";") >= 4:
        score += 1
        signals.append("many_clauses")

    remote_ok = _remote_available(config)
    forced = config.force_tier
    tier: ModelTier = forced or ("L2" if score >= 5 else "L1")
    if tier == "L2" and remote_ok:
        backend = str(config.remote_backend)
        codes = (ReasonCode.MODEL_ROUTE_L2.value, ReasonCode.CAPABILITY_REGISTRY_AUTHORITATIVE.value)
        return ModelRouteDecision(
            MODEL_ROUTER_VERSION, input_kind, tier, backend, score, domains, tuple(signals), codes,
            fallback_backend=config.local_backend, remote_available=True,
        )
    if tier == "L2" and not remote_ok:
        return ModelRouteDecision(
            MODEL_ROUTER_VERSION, input_kind, "L1", config.local_backend, score, domains, tuple(signals),
            (
                ReasonCode.MODEL_ROUTE_L1.value,
                ReasonCode.REMOTE_MODEL_UNAVAILABLE_FALLBACK.value,
                ReasonCode.CAPABILITY_REGISTRY_AUTHORITATIVE.value,
            ),
            fallback_backend=config.local_backend, remote_available=False,
        )
    return ModelRouteDecision(
        MODEL_ROUTER_VERSION, input_kind, "L1", config.local_backend, score, domains, tuple(signals),
        (ReasonCode.MODEL_ROUTE_L1.value, ReasonCode.CAPABILITY_REGISTRY_AUTHORITATIVE.value),
        remote_available=remote_ok,
    )


RAG_BOUNDARY_POLICY = {
    "policy_version": "v23.rag-boundary.v1",
    "role": "advisory_only",
    "allowed_uses": [
        "retrieve_similar_examples",
        "retrieve_user_and_developer_documentation",
        "explain_parameters_and_validation_errors",
        "provide_non_authoritative_candidate terminology",
    ],
    "forbidden_authority": [
        "capability_support",
        "effect_owner",
        "builder_or_runner_binding",
        "native_proxy_classification",
        "parameter_profile",
        "claim_level",
        "qoi_observability_truth",
        "runtime_injection_success",
    ],
    "authoritative_sources": [
        "Capability Registry",
        "Operator Contract",
        "Parameter Registry",
        "Validation Registry",
        "Run evidence",
    ],
    "reason_code": ReasonCode.RAG_ADVISORY_ONLY.value,
}


__all__ = [
    "MODEL_ROUTER_VERSION",
    "ModelRouteConfig",
    "ModelRouteDecision",
    "RAG_BOUNDARY_POLICY",
    "route_model",
]
