"""Capability-to-model-library integration contracts.

Each Capability YAML explicitly declares the implementation class and supported
physical causal edges.  Source modules remain authoritative under
``implementation.model_modules`` and are resolved through a reference to avoid
maintaining two divergent module lists.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

from .physical_couplings import KNOWN_PHYSICAL_COUPLING_IDS, normalize_coupling_ids

CAPABILITY_INTEGRATION_VERSION = "sat-sim.capability-integration.v1"
ImplementationClass = Literal[
    "official_basilisk_native",
    "mixed_native_project",
    "project_deterministic_model",
    "engineering_proxy",
    "orchestration_adapter",
]


class CapabilityIntegrationContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[CAPABILITY_INTEGRATION_VERSION] = CAPABILITY_INTEGRATION_VERSION
    capability_id: str = Field(min_length=1)
    source_modules_ref: Literal["implementation.model_modules"] = "implementation.model_modules"
    source_modules: list[str] = Field(default_factory=list)
    implementation_class: ImplementationClass
    supported_couplings: list[str] = Field(default_factory=list)
    unsupported_couplings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class CapabilityIntegrationIssue:
    severity: Literal["error", "warning"]
    capability_id: str
    path: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def normalize_capability_integration(data: Mapping[str, Any]) -> CapabilityIntegrationContract:
    capability_id = str(data.get("capability_id") or "").strip()
    raw = _mapping(data.get("integration_contract"))
    if not raw:
        raise ValueError(f"capability {capability_id!r} does not define integration_contract")
    implementation = _mapping(data.get("implementation"))
    source_modules = [str(item) for item in (implementation.get("model_modules") or []) if str(item).strip()]
    couplings = _mapping(raw.get("couplings"))
    supported = list(normalize_coupling_ids([str(item) for item in (couplings.get("supported") or [])]))
    unsupported_raw = [str(item) for item in (couplings.get("unsupported") or [])]
    if "*" in unsupported_raw:
        unsupported = sorted(KNOWN_PHYSICAL_COUPLING_IDS - set(supported))
    else:
        unsupported = list(normalize_coupling_ids(unsupported_raw))
    limitations = [str(item) for item in (raw.get("limitations") or []) if str(item).strip()]
    return CapabilityIntegrationContract.model_validate({
        "schema_version": raw.get("schema_version", CAPABILITY_INTEGRATION_VERSION),
        "capability_id": capability_id,
        "source_modules_ref": raw.get("source_modules_ref", "implementation.model_modules"),
        "source_modules": source_modules,
        "implementation_class": raw.get("implementation_class"),
        "supported_couplings": supported,
        "unsupported_couplings": unsupported,
        "limitations": limitations,
    })


def validate_capability_integration(data: Mapping[str, Any]) -> list[CapabilityIntegrationIssue]:
    cid = str(data.get("capability_id") or "")
    issues: list[CapabilityIntegrationIssue] = []
    try:
        contract = normalize_capability_integration(data)
    except Exception as exc:
        return [CapabilityIntegrationIssue("error", cid, "$.integration_contract", "CAPABILITY_INTEGRATION_INVALID", str(exc))]
    if not contract.source_modules:
        issues.append(CapabilityIntegrationIssue("error", cid, "$.implementation.model_modules", "CAPABILITY_SOURCE_MODULES_EMPTY", "Capability must map to at least one source model/adapter module."))
    supported = set(contract.supported_couplings)
    unsupported = set(contract.unsupported_couplings)
    unknown = sorted((supported | unsupported) - KNOWN_PHYSICAL_COUPLING_IDS)
    if unknown:
        issues.append(CapabilityIntegrationIssue("error", cid, "$.integration_contract.couplings", "CAPABILITY_COUPLING_UNKNOWN", f"Unknown coupling IDs: {unknown}"))
    overlap = sorted(supported & unsupported)
    if overlap:
        issues.append(CapabilityIntegrationIssue("error", cid, "$.integration_contract.couplings", "CAPABILITY_COUPLING_CONFLICT", f"Couplings declared supported and unsupported: {overlap}"))
    uncovered = sorted(KNOWN_PHYSICAL_COUPLING_IDS - supported - unsupported)
    if uncovered:
        issues.append(CapabilityIntegrationIssue("error", cid, "$.integration_contract.couplings", "CAPABILITY_COUPLING_COVERAGE_INCOMPLETE", f"Coupling declaration does not cover: {uncovered}"))
    return issues


__all__ = [
    "CAPABILITY_INTEGRATION_VERSION",
    "CapabilityIntegrationContract",
    "CapabilityIntegrationIssue",
    "normalize_capability_integration",
    "validate_capability_integration",
]
