"""Route-B capability package policy.

Route-B model-library capabilities should not grow as arbitrary file piles.  This
module defines a small auditable package checklist used by HF-7/HF-8 tests and
release reports.
"""
from __future__ import annotations

from typing import Any, Mapping

ROUTE_B_CAPABILITY_PACKAGE_SCHEMA_VERSION = "route_b.capability_package.v1"

REQUIRED_ROUTE_B_SECTIONS: tuple[str, ...] = (
    "capability_id",
    "adapter",
    "implementation",
    "parameters",
    "consumes",
    "produces",
    "outputs",
    "source_binding",
    "fidelity_level",
    "known_physics_limits",
    "allowed_task_spec_sections",
)

REQUIRED_SOURCE_BINDING_FIELDS: tuple[str, ...] = (
    "mode",
    "primary_module",
    "source_modules",
    "public_api",
    "uses_legacy_runner",
)


def evaluate_route_b_capability_package(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate whether a capability follows the Route-B package convention."""

    cid = str(contract.get("capability_id") or "")
    source_binding = contract.get("source_binding") if isinstance(contract.get("source_binding"), Mapping) else {}
    implementation = contract.get("implementation") if isinstance(contract.get("implementation"), Mapping) else {}
    adapter = contract.get("adapter") if isinstance(contract.get("adapter"), Mapping) else {}
    missing_sections = [key for key in REQUIRED_ROUTE_B_SECTIONS if key not in contract]
    missing_source_binding = [key for key in REQUIRED_SOURCE_BINDING_FIELDS if key not in source_binding]
    model_modules = implementation.get("model_modules") if isinstance(implementation.get("model_modules"), list) else []
    public_api = source_binding.get("public_api") if isinstance(source_binding.get("public_api"), list) else []
    allowed = contract.get("allowed_task_spec_sections") if isinstance(contract.get("allowed_task_spec_sections"), list) else []
    checks = {
        "has_adapter_runner": bool(adapter.get("class_path") and adapter.get("runtime_run") and adapter.get("script_export")),
        "has_model_modules": bool(model_modules),
        "has_builder_public_api": any("build_" in str(item) or "payload" in str(item) for item in public_api) or any("build_" in str(item) for item in model_modules),
        "has_schema_contract": bool(contract.get("schema_version") and contract.get("outputs")),
        "has_fault_or_degradation_policy": "fault" in (contract.get("modes") or {}) or "degradation" in (contract.get("modes") or {}) or bool(contract.get("modifier_policy")),
        "has_known_limits": bool(contract.get("known_physics_limits")),
        "has_allowed_sections": bool(allowed),
        "uses_route_b_source_binding": source_binding.get("mode") == "route_b_model_library",
        "does_not_use_legacy_runner": source_binding.get("uses_legacy_runner") is False and implementation.get("uses_legacy_runner") is False,
    }
    ok = not missing_sections and not missing_source_binding and all(checks.values())
    return {
        "schema_version": ROUTE_B_CAPABILITY_PACKAGE_SCHEMA_VERSION,
        "capability_id": cid,
        "ok": ok,
        "status": "pass" if ok else "fail",
        "missing_sections": missing_sections,
        "missing_source_binding_fields": missing_source_binding,
        "checks": checks,
        "canonical_package_shape": {
            "model_core": "src/sat_sim/<domain>/<capability_model>.py",
            "adapter_runner": "src/sat_sim/adapters/<capability_adapter>.py",
            "contract_schema": "src/sat_sim/capabilities/<capability_id>.yaml plus task/dataset schema entries when needed",
            "builder_payload": "build_hf*_..._payload() used by compiler and manifest",
            "fault_degradation_policy": "modes plus TaskSpec modifiers; dedicated faults/degradations modules only when executable behavior exists",
            "validation": "HF-8 physical validation gates in summaries/manifests for Route-B coupled models",
            "examples_tests_docs": "examples/, tests/, docs/, reports/ for every new capability",
        },
    }


def build_route_b_package_policy_report(contracts: list[Mapping[str, Any]]) -> dict[str, Any]:
    evaluations = [evaluate_route_b_capability_package(contract) for contract in contracts]
    return {
        "schema_version": ROUTE_B_CAPABILITY_PACKAGE_SCHEMA_VERSION,
        "route_b_version": "B-5/HF-7+HF-8",
        "policy": "Route-B capabilities must use model core + adapter/runner + contract schema + builder payload + modifier policy + validation + examples/tests/docs/reports.",
        "evaluated_count": len(evaluations),
        "pass_count": sum(1 for item in evaluations if item["ok"]),
        "fail_count": sum(1 for item in evaluations if not item["ok"]),
        "evaluations": evaluations,
    }


__all__ = [
    "ROUTE_B_CAPABILITY_PACKAGE_SCHEMA_VERSION",
    "REQUIRED_ROUTE_B_SECTIONS",
    "REQUIRED_SOURCE_BINDING_FIELDS",
    "evaluate_route_b_capability_package",
    "build_route_b_package_policy_report",
]
