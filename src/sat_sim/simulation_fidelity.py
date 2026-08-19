"""Fail-closed simulation-fidelity classification for formal datasets.

Basilisk-native execution is the default and only path eligible for formal
AstroGraph training candidates.  Pure-Python/proxy execution remains available
for unit tests and interface development, but it is never promoted implicitly.
"""
from __future__ import annotations

import importlib.metadata
import importlib.util
from dataclasses import asdict, dataclass
from typing import Any, Mapping

ACCEPTED_BSK_VERSIONS = frozenset({"2.11.0", "2.11.0+satfix1"})
FORMAL_BASILISK_CAPABILITIES = frozenset({
    "subsystem.adcs_unified_native.v1",
    "whole_spacecraft.unified_native.v1",
    "whole_spacecraft.basilisk_6dof.v1",
})


@dataclass(frozen=True)
class BasiliskRuntimeStatus:
    installed: bool
    importable: bool
    version: str | None
    accepted_version: bool
    ready: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def inspect_basilisk_runtime() -> BasiliskRuntimeStatus:
    spec = importlib.util.find_spec("Basilisk")
    try:
        version = importlib.metadata.version("bsk")
        installed = True
    except importlib.metadata.PackageNotFoundError:
        version = None
        installed = False
    importable = spec is not None
    accepted = version in ACCEPTED_BSK_VERSIONS
    if not installed:
        reason = "bsk_distribution_not_installed"
    elif not importable:
        reason = "basilisk_import_root_unavailable"
    elif not accepted:
        reason = f"unsupported_bsk_version:{version}"
    else:
        reason = "ready"
    return BasiliskRuntimeStatus(installed, importable, version, accepted, installed and importable and accepted, reason)


def require_basilisk_runtime() -> BasiliskRuntimeStatus:
    status = inspect_basilisk_runtime()
    if not status.ready:
        raise RuntimeError(
            "formal Basilisk dataset execution requires an importable bsk "
            f"runtime in {sorted(ACCEPTED_BSK_VERSIONS)}; status={status.reason}"
        )
    return status


def classify_simulation_fidelity(
    *,
    capability_id: str | None,
    backend: str | None,
    bsk_version: str | None,
    runtime_summary: Mapping[str, Any] | None = None,
    runtime_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a conservative, machine-readable fidelity decision."""

    summary = dict(runtime_summary or {})
    evidence = dict(runtime_evidence or {})
    runtime_truth = str(summary.get("runtime_truth_status") or "")
    manifest = evidence.get("runtime_manifest") if isinstance(evidence.get("runtime_manifest"), Mapping) else {}
    instantiated = manifest.get("instantiated_modules") if isinstance(manifest, Mapping) else []
    instantiated_count = len(instantiated) if isinstance(instantiated, list) else 0
    formal_capability = capability_id in FORMAL_BASILISK_CAPABILITIES
    version_ok = bsk_version in ACCEPTED_BSK_VERSIONS
    backend_ok = str(backend or "").lower() == "basilisk"
    runtime_ok = runtime_truth.startswith("instantiated_connected_recorded") and instantiated_count > 0
    formal = formal_capability and version_ok and backend_ok and runtime_ok

    if formal:
        level = "A_ENGINEERING_BASILISK_NATIVE"
        purpose = ["model_training_candidate", "benchmark_candidate", "expert_review_candidate"]
        reason = "basilisk_native_runtime_evidence_complete"
    elif formal_capability or backend_ok:
        level = "B_BASILISK_UNVERIFIED_OR_INCOMPLETE"
        purpose = ["engineering_debug", "runtime_repair"]
        reason = "basilisk_requested_but_runtime_evidence_incomplete"
    else:
        level = "C_TEST_PROXY_ONLY"
        purpose = ["unit_test", "interface_test", "agent_workflow_test"]
        reason = "non_basilisk_execution"

    return {
        "simulation_engine": "Basilisk" if backend_ok or formal_capability else "non_basilisk_test_runtime",
        "fidelity_level": level,
        "formal_training_eligible": formal,
        "reason": reason,
        "capability_id": capability_id,
        "backend": backend,
        "bsk_version": bsk_version,
        "bsk_version_accepted": version_ok,
        "runtime_truth_status": runtime_truth or None,
        "instantiated_module_count": instantiated_count,
        "allowed_purposes": purpose,
        "flight_validated": False,
    }


__all__ = [
    "ACCEPTED_BSK_VERSIONS",
    "FORMAL_BASILISK_CAPABILITIES",
    "BasiliskRuntimeStatus",
    "classify_simulation_fidelity",
    "inspect_basilisk_runtime",
    "require_basilisk_runtime",
]
