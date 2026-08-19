"""HF-8 validation gate public façade."""
from __future__ import annotations

from .physical_checks import (
    HF8_PHYSICAL_VALIDATION_SCHEMA_VERSION,
    CHECK_FUNCTIONS,
    DEFAULT_CHECKS,
    PhysicalValidationIssue,
    build_hf8_physical_validation_payload,
    evaluate_physical_validation,
)


def build_hf8_readiness_matrix(*, capability_count: int | None = None, route_b_model_library_capability_count: int | None = None) -> dict[str, object]:
    out: dict[str, object] = {
        "schema_version": HF8_PHYSICAL_VALIDATION_SCHEMA_VERSION,
        "route_b_version": "B-5/HF-8",
        "physical_validation_gate_status": "implemented",
        "default_checks": list(DEFAULT_CHECKS),
        "high_fidelity_ready_count": 0,
        "can_claim_package_high_fidelity": False,
        "remaining_route_b_dependencies": ["HF-9 benchmark scenarios and tolerance envelopes"],
    }
    if capability_count is not None:
        out["capability_count"] = int(capability_count)
    if route_b_model_library_capability_count is not None:
        out["route_b_model_library_capability_count"] = int(route_b_model_library_capability_count)
    return out


__all__ = [
    "HF8_PHYSICAL_VALIDATION_SCHEMA_VERSION",
    "CHECK_FUNCTIONS",
    "DEFAULT_CHECKS",
    "PhysicalValidationIssue",
    "build_hf8_physical_validation_payload",
    "build_hf8_readiness_matrix",
    "evaluate_physical_validation",
]
