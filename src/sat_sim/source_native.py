"""Source-native binding utilities for capability contracts.

S2 makes the relationship between Agent-facing capabilities and the original
``src`` simulation modules explicit.  A capability can be backed by existing
source modules, by a deterministic synthetic adapter, by a legacy runner wrapper,
or marked as demo-only.  The binding is metadata and validation support; it does
not change simulation fidelity or authorize the LLM to write arbitrary imports.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Mapping


SOURCE_BINDING_SCHEMA_VERSION = "s2.source_binding.v1"
VALID_SOURCE_BINDING_MODES = {"source_native", "synthetic", "legacy_runner", "demo_only", "route_b_model_library", "basilisk_native"}


@dataclass(frozen=True)
class SourceBinding:
    """Normalized source binding metadata for one capability."""

    mode: str
    status: str
    primary_module: str | None = None
    source_modules: tuple[str, ...] = ()
    public_api: tuple[str, ...] = ()
    legacy_demo_runner: str | None = None
    uses_legacy_runner: bool = False
    basilisk_required: bool = False
    rationale: str | None = None
    schema_version: str = SOURCE_BINDING_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["source_modules"] = list(self.source_modules)
        payload["public_api"] = list(self.public_api)
        return payload


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _str_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _bool(value: Any, default: bool = False) -> bool:
    return bool(default if value is None else value)


def infer_source_binding(contract_data: Mapping[str, Any]) -> SourceBinding:
    """Infer a conservative binding when a legacy contract lacks one.

    S2 writes explicit ``source_binding`` blocks for current contracts.  This
    fallback keeps older or third-party contracts readable without promoting
    them to source-native accidentally.
    """

    implementation = _mapping(contract_data.get("implementation"))
    adapter = _mapping(contract_data.get("adapter"))
    model_modules = _str_list(implementation.get("model_modules"))
    legacy_runner = implementation.get("legacy_demo_runner")
    uses_legacy = _bool(implementation.get("uses_legacy_runner"), False)
    basilisk_required = _bool(implementation.get("basilisk_required"), False)
    if model_modules and not uses_legacy:
        return SourceBinding(
            mode="source_native",
            status="inferred_from_implementation_model_modules",
            primary_module=model_modules[0],
            source_modules=model_modules,
            public_api=(),
            legacy_demo_runner=str(legacy_runner) if legacy_runner else None,
            uses_legacy_runner=False,
            basilisk_required=basilisk_required,
        )
    if uses_legacy or legacy_runner:
        return SourceBinding(
            mode="legacy_runner",
            status="inferred_from_legacy_runner",
            primary_module=str(legacy_runner) if legacy_runner else None,
            source_modules=(str(legacy_runner),) if legacy_runner else (),
            legacy_demo_runner=str(legacy_runner) if legacy_runner else None,
            uses_legacy_runner=True,
            basilisk_required=basilisk_required,
        )
    return SourceBinding(
        mode="synthetic",
        status="inferred_synthetic_adapter",
        primary_module=str(adapter.get("class_path")) if adapter.get("class_path") else None,
        source_modules=(),
        basilisk_required=basilisk_required,
        rationale="No source_binding or implementation.model_modules declared.",
    )


def normalize_source_binding(contract_data: Mapping[str, Any]) -> SourceBinding:
    """Normalize and validate a contract ``source_binding`` block."""

    raw = _mapping(contract_data.get("source_binding"))
    if not raw:
        return infer_source_binding(contract_data)
    mode = str(raw.get("mode") or "").strip()
    if mode not in VALID_SOURCE_BINDING_MODES:
        raise ValueError(f"invalid source_binding.mode {mode!r}; expected one of {sorted(VALID_SOURCE_BINDING_MODES)}")
    source_modules = _str_list(raw.get("source_modules"))
    public_api = _str_list(raw.get("public_api"))
    primary = raw.get("primary_module")
    primary_module = str(primary).strip() if primary is not None and str(primary).strip() else (source_modules[0] if source_modules else None)
    if mode in {"source_native", "legacy_runner", "route_b_model_library", "basilisk_native"} and not primary_module:
        raise ValueError(f"source_binding.mode={mode!r} requires primary_module or source_modules")
    legacy = raw.get("legacy_demo_runner")
    return SourceBinding(
        mode=mode,
        status=str(raw.get("status") or "declared").strip() or "declared",
        primary_module=primary_module,
        source_modules=source_modules,
        public_api=public_api,
        legacy_demo_runner=str(legacy).strip() if legacy is not None and str(legacy).strip() else None,
        uses_legacy_runner=_bool(raw.get("uses_legacy_runner"), mode == "legacy_runner"),
        basilisk_required=_bool(raw.get("basilisk_required"), False),
        rationale=str(raw.get("rationale")).strip() if raw.get("rationale") is not None else None,
    )


def source_binding_payload(contract_data: Mapping[str, Any]) -> dict[str, Any]:
    """Return serializable source-binding metadata for a capability contract.

    This is the internal/audit view.  It preserves declared legacy runner
    metadata even for source-native capabilities so historical migration context
    remains available in compiled manifests and source-coverage reports.
    """

    return normalize_source_binding(contract_data).to_dict()


def public_source_binding_payload(contract_data: Mapping[str, Any]) -> dict[str, Any]:
    """Return Agent/script-facing source-binding metadata.

    A-close keeps legacy runner names out of LLM prompts and generated scripts
    when a capability explicitly declares ``uses_legacy_runner: false``.  The
    public payload still exposes the auditable fields required for source
    grounding: mode, status, primary module, source modules, public API,
    ``uses_legacy_runner`` and Basilisk requirement.
    """

    payload = source_binding_payload(contract_data)
    if not bool(payload.get("uses_legacy_runner", False)):
        legacy_runner = payload.pop("legacy_demo_runner", None)
        rationale = payload.get("rationale")
        if legacy_runner and isinstance(rationale, str) and legacy_runner in rationale:
            payload["rationale"] = rationale.replace(legacy_runner, "legacy demo runner")
    return payload


__all__ = [
    "SOURCE_BINDING_SCHEMA_VERSION",
    "VALID_SOURCE_BINDING_MODES",
    "SourceBinding",
    "infer_source_binding",
    "normalize_source_binding",
    "source_binding_payload",
    "public_source_binding_payload",
]
