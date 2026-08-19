"""High-fidelity readiness foundation package."""

from .foundations import (
    DEFAULT_FOUNDATION_STATUS,
    HF1_HF2_FOUNDATION_SCHEMA_VERSION,
    FoundationStatus,
    build_hf1_hf2_foundation_payload,
    build_hf1_hf2_readiness_matrix,
)
from .contracts import (
    DEFAULT_VALIDATION_GATES,
    HF0_SCHEMA_VERSION,
    FidelityReadinessReport,
    FrameContract,
    SolverConfig,
    TimeContract,
    UnitContract,
    ValidationGate,
    build_hf0_readiness_matrix,
    build_hf0_readiness_report,
)

__all__ = [
    "DEFAULT_VALIDATION_GATES",
    "HF0_SCHEMA_VERSION",
    "FidelityReadinessReport",
    "FrameContract",
    "SolverConfig",
    "TimeContract",
    "UnitContract",
    "ValidationGate",
    "build_hf0_readiness_matrix",
    "build_hf0_readiness_report",
    "DEFAULT_FOUNDATION_STATUS",
    "HF1_HF2_FOUNDATION_SCHEMA_VERSION",
    "FoundationStatus",
    "build_hf1_hf2_foundation_payload",
    "build_hf1_hf2_readiness_matrix",
]

from .package_policy import (
    ROUTE_B_CAPABILITY_PACKAGE_SCHEMA_VERSION,
    build_route_b_package_policy_report,
    evaluate_route_b_capability_package,
)

__all__ = [name for name in globals() if not name.startswith("_")]
