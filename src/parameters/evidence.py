"""Evidence source and surrogate-calibration gates.

V19 intentionally supports engineering-estimate profiles when real calibration
truth data are unavailable, but it prevents surrogate evidence from being
misrepresented as ground-calibrated or flight-correlated evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CONFIDENCE_ORDER: tuple[str, ...] = (
    "demo",
    "engineering_estimate",
    "ground_calibrated",
    "flight_correlated",
)

CONFIDENCE_RANK = {name: idx for idx, name in enumerate(CONFIDENCE_ORDER)}

# Maximum claim allowed by source type.  A vendor data sheet, design derivation or
# bounded assumption can support an engineering-estimate profile, but not a ground
# calibration claim.  Synthetic/demo data cannot support engineering claims.
SOURCE_CONFIDENCE_CEILING: dict[str, str] = {
    "demo_default": "demo",
    "synthetic_only": "demo",
    "placeholder": "demo",
    "unknown": "demo",
    "bounded_assumption": "engineering_estimate",
    "engineering_assumption": "engineering_estimate",
    "public_literature": "engineering_estimate",
    "vendor_datasheet": "engineering_estimate",
    "design_derived": "engineering_estimate",
    "low_cost_bench": "ground_calibrated",
    "ground_test": "ground_calibrated",
    "formal_ground_test": "ground_calibrated",
    "flight_telemetry": "flight_correlated",
}

GROUND_OR_FLIGHT_SOURCE_TYPES = {
    "low_cost_bench",
    "ground_test",
    "formal_ground_test",
    "flight_telemetry",
}

ENGINEERING_SURROGATE_SOURCE_TYPES = {
    "bounded_assumption",
    "engineering_assumption",
    "public_literature",
    "vendor_datasheet",
    "design_derived",
}

UNCERTAINTY_DISTRIBUTIONS = {
    "not_established",
    "fixed_by_design",
    "categorical_fixed",
    "bounded",
    "bounded_vector",
    "uniform",
    "triangular",
    "normal",
    "log_uniform",
    "integer_range",
}

QUANTITATIVE_DISTRIBUTIONS = {
    "bounded",
    "bounded_vector",
    "uniform",
    "triangular",
    "normal",
    "log_uniform",
    "integer_range",
}

NON_QUANTITATIVE_DISTRIBUTIONS = {
    "fixed_by_design",
    "categorical_fixed",
}


@dataclass(frozen=True)
class EvidenceCompatibility:
    source_type: str
    confidence: str
    source_known: bool
    source_ceiling: str
    source_allows_confidence: bool
    requires_dataset: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "confidence": self.confidence,
            "source_known": self.source_known,
            "source_ceiling": self.source_ceiling,
            "source_allows_confidence": self.source_allows_confidence,
            "requires_dataset": self.requires_dataset,
        }


def confidence_rank(level: str) -> int:
    return CONFIDENCE_RANK[level]


def source_ceiling(source_type: str) -> str:
    return SOURCE_CONFIDENCE_CEILING.get(source_type, "demo")


def evidence_compatibility(source_type: str, confidence: str) -> EvidenceCompatibility:
    known = source_type in SOURCE_CONFIDENCE_CEILING
    ceiling = source_ceiling(source_type)
    allows = known and confidence in CONFIDENCE_RANK and confidence_rank(confidence) <= confidence_rank(ceiling)
    return EvidenceCompatibility(
        source_type=source_type,
        confidence=confidence,
        source_known=known,
        source_ceiling=ceiling,
        source_allows_confidence=allows,
        requires_dataset=source_type in GROUND_OR_FLIGHT_SOURCE_TYPES,
    )


def is_quantitative_value(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, (list, tuple)):
        return bool(value) and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
    return False


def uncertainty_distribution(record_uncertainty: dict[str, Any] | None) -> str:
    if not record_uncertainty:
        return "not_established"
    return str(record_uncertainty.get("distribution", "not_established"))


def uncertainty_is_engineering_ready(value: Any, uncertainty: dict[str, Any] | None) -> bool:
    dist = uncertainty_distribution(uncertainty)
    if dist not in UNCERTAINTY_DISTRIBUTIONS:
        return False
    if is_quantitative_value(value):
        if dist not in QUANTITATIVE_DISTRIBUTIONS:
            return False
        if dist in {"bounded", "uniform", "log_uniform", "integer_range"}:
            return "lower" in (uncertainty or {}) and "upper" in (uncertainty or {})
        if dist == "triangular":
            return all(k in (uncertainty or {}) for k in ("lower", "mode", "upper"))
        if dist == "normal":
            return "sigma" in (uncertainty or {}) or "std" in (uncertainty or {})
        if dist == "bounded_vector":
            return "lower" in (uncertainty or {}) and "upper" in (uncertainty or {})
        return True
    return dist in NON_QUANTITATIVE_DISTRIBUTIONS


__all__ = [
    "CONFIDENCE_ORDER",
    "CONFIDENCE_RANK",
    "SOURCE_CONFIDENCE_CEILING",
    "ENGINEERING_SURROGATE_SOURCE_TYPES",
    "GROUND_OR_FLIGHT_SOURCE_TYPES",
    "UNCERTAINTY_DISTRIBUTIONS",
    "QUANTITATIVE_DISTRIBUTIONS",
    "EvidenceCompatibility",
    "confidence_rank",
    "source_ceiling",
    "evidence_compatibility",
    "is_quantitative_value",
    "uncertainty_distribution",
    "uncertainty_is_engineering_ready",
]
