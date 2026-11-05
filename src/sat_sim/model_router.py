"""V23 local/remote model routing for TaskSpec creation.

The router decides *which draft generator may be used*.  It never decides
capability truth, supported effects, fidelity or claims; those remain registry
and guard responsibilities.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Mapping, Sequence

from .reason_codes import ReasonCode

MODEL_ROUTER_VERSION = "v23.model-router.v2"
MODEL_ROUTE_CALIBRATION_VERSION = "sat-sim.model-route-calibration.v1"
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
    calibration_file: str | None = None


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
    calibration: dict[str, object] | None = None
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


def _load_calibration(config: ModelRouteConfig) -> dict[str, Any] | None:
    path_value = (
        config.calibration_file
        or os.environ.get("SAT_SIM_MODEL_ROUTE_CALIBRATION_FILE")
        or ""
    ).strip()
    if not path_value:
        return None
    path = Path(path_value).expanduser().resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "status": "invalid",
            "source": str(path),
            "reason": "calibration_file_unreadable",
        }
    if not isinstance(payload, Mapping) or payload.get("schema_version") != MODEL_ROUTE_CALIBRATION_VERSION:
        return {
            "status": "invalid",
            "source": str(path),
            "reason": "calibration_schema_invalid",
        }
    return {"status": "loaded", "source": str(path), "payload": dict(payload)}


def _calibrated_tier(
    *,
    heuristic_tier: ModelTier,
    domains: tuple[str, ...],
    signals: Sequence[str],
    calibration: dict[str, Any] | None,
) -> tuple[ModelTier, dict[str, object] | None]:
    if not calibration or calibration.get("status") != "loaded":
        return heuristic_tier, (
            {key: value for key, value in calibration.items() if key != "payload"}
            if calibration
            else None
        )
    payload = calibration.get("payload")
    if not isinstance(payload, Mapping):
        return heuristic_tier, None
    default_min_samples = max(1, int(payload.get("min_samples") or 10))
    default_min_gain = max(0.0, float(payload.get("min_success_rate_gain") or 0.10))
    candidates: list[tuple[int, str, Mapping[str, Any]]] = []
    signal_set = set(str(item) for item in signals)
    domain_set = set(domains)
    for raw in payload.get("segments") or []:
        if not isinstance(raw, Mapping):
            continue
        match = raw.get("match") if isinstance(raw.get("match"), Mapping) else {}
        required_domains = {str(item) for item in (match.get("domains") or [])}
        required_signals = {str(item) for item in (match.get("signals") or [])}
        if required_domains and not required_domains.issubset(domain_set):
            continue
        if required_signals and not required_signals.issubset(signal_set):
            continue
        segment_id = str(raw.get("segment_id") or "").strip()
        if not segment_id:
            continue
        specificity = len(required_domains) + len(required_signals)
        candidates.append((specificity, segment_id, raw))
    if not candidates:
        return heuristic_tier, {
            "status": "no_matching_segment",
            "source": calibration.get("source"),
        }
    candidates.sort(key=lambda item: (-item[0], item[1]))
    _, segment_id, segment = candidates[0]
    sample_count = int(segment.get("sample_count") or 0)
    min_samples = max(1, int(segment.get("min_samples") or default_min_samples))
    l1_success = float(segment.get("l1_success_rate") or 0.0)
    l2_success = float(segment.get("l2_success_rate") or 0.0)
    rates_valid = 0.0 <= l1_success <= 1.0 and 0.0 <= l2_success <= 1.0
    gain = l2_success - l1_success
    min_gain = max(0.0, float(segment.get("min_success_rate_gain") or default_min_gain))
    recommended = str(segment.get("recommended_tier") or "").upper()
    evidence_sha256 = str(segment.get("evaluation_sha256") or "")
    evidence_valid = (
        len(evidence_sha256) == 64
        and all(ch in "0123456789abcdef" for ch in evidence_sha256)
    )
    matched_budget = segment.get("matched_budget") is True
    accepted = (
        sample_count >= min_samples
        and rates_valid
        and evidence_valid
        and matched_budget
        and recommended in {"L1", "L2"}
        and (
            (recommended == "L2" and gain >= min_gain)
            or (recommended == "L1" and -gain >= min_gain)
        )
    )
    result = {
        "status": "accepted" if accepted else "insufficient_evidence",
        "source": calibration.get("source"),
        "segment_id": segment_id,
        "sample_count": sample_count,
        "min_samples": min_samples,
        "l1_success_rate": l1_success,
        "l2_success_rate": l2_success,
        "success_rate_gain_l2_minus_l1": gain,
        "min_success_rate_gain": min_gain,
        "recommended_tier": recommended or None,
        "heuristic_tier": heuristic_tier,
        "matched_budget": matched_budget,
        "evaluation_sha256": evidence_sha256 or None,
        "rates_valid": rates_valid,
        "evidence_valid": evidence_valid,
    }
    if not accepted:
        return heuristic_tier, result
    return recommended, result  # type: ignore[return-value]


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
    heuristic_tier: ModelTier = "L2" if score >= 5 else "L1"
    calibration_state = _load_calibration(config)
    calibrated_tier, calibration_result = _calibrated_tier(
        heuristic_tier=heuristic_tier,
        domains=domains,
        signals=signals,
        calibration=calibration_state,
    )
    tier: ModelTier = forced or calibrated_tier
    if calibration_result and calibration_result.get("status") == "accepted":
        signals.append(
            "route_calibration:"
            + str(calibration_result.get("segment_id"))
            + ":"
            + str(calibration_result.get("recommended_tier"))
        )
    if tier == "L2" and remote_ok:
        backend = str(config.remote_backend)
        codes = (ReasonCode.MODEL_ROUTE_L2.value, ReasonCode.CAPABILITY_REGISTRY_AUTHORITATIVE.value)
        return ModelRouteDecision(
            MODEL_ROUTER_VERSION, input_kind, tier, backend, score, domains, tuple(signals), codes,
            fallback_backend=config.local_backend, remote_available=True,
            calibration=calibration_result,
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
            calibration=calibration_result,
        )
    return ModelRouteDecision(
        MODEL_ROUTER_VERSION, input_kind, "L1", config.local_backend, score, domains, tuple(signals),
        (ReasonCode.MODEL_ROUTE_L1.value, ReasonCode.CAPABILITY_REGISTRY_AUTHORITATIVE.value),
        remote_available=remote_ok,
        calibration=calibration_result,
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
    "MODEL_ROUTE_CALIBRATION_VERSION",
    "ModelRouteConfig",
    "ModelRouteDecision",
    "RAG_BOUNDARY_POLICY",
    "route_model",
]
