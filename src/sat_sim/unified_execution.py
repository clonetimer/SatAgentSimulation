"""A2R composition root for all model execution.

Product-facing callers use :func:`execute_compiled_task`.  The function derives
an internal logical adapter key, creates an engine-independent
``ExecutionRequest`` and invokes ``ModelExecutionPort``.  Arbitrary Python class
paths from TaskSpec, Agent or API input are never used as registry keys.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
from threading import RLock
from collections.abc import Mapping, Sequence
from typing import Any, Callable

from sat_sim_execution import (
    ExecutionAdapterRegistry,
    ExecutionRequest,
    ModelExecutionPort,
    RegistryExecutionPort,
)

from .dataset_writer import DatasetWriteResult
from .task_compiler import CompiledTask
from .task_runner import TaskRunResult
from .task_spec import TaskSpecError
from .legacy_execution import (
    LEGACY_CAPABILITY_ADAPTER_KEY,
    LEGACY_MODULE_ADAPTER_KEY,
    LEGACY_ORBIT_ENVIRONMENT_ADAPTER_KEY,
    LEGACY_WHOLE_SPACECRAFT_ADAPTER_KEY,
    LegacyCapabilityAdapter,
    LegacyModuleAdapter,
    LegacyOrbitEnvironmentAdapter,
    LegacyWholeSpacecraftAdapter,
)
from .registered_capability_execution import (
    REGISTERED_CAPABILITY_ADAPTER_KEY,
    RegisteredCapabilityExecutionAdapter,
)

UNIFIED_EXECUTION_VERSION = "sat-sim.unified-execution.a2r.v1"
_REGISTRY: ExecutionAdapterRegistry | None = None
_RESOLVER_REGISTRY: ModelAssetResolverRegistry | None = None
_PORT: ModelExecutionPort | None = None
_LOCK = RLock()


ModelAssetResolver = Callable[..., Any]


@dataclass(frozen=True)
class ModelAssetResolverEntry:
    adapter_key: str
    resolver: ModelAssetResolver
    diagnostics_key: str


class ModelAssetResolverRegistry:
    """Registry for adapter-key-specific model-asset binding resolvers."""

    def __init__(self) -> None:
        self._entries: dict[str, ModelAssetResolverEntry] = {}

    def register(self, adapter_key: str, resolver: ModelAssetResolver, *, diagnostics_key: str) -> None:
        from sat_sim_execution import validate_adapter_key

        key = validate_adapter_key(adapter_key)
        if key in self._entries:
            raise TaskSpecError(f"duplicate model-asset resolver for adapter_key={key!r}")
        if not callable(resolver):
            raise TypeError("resolver must be callable")
        if not isinstance(diagnostics_key, str) or not diagnostics_key.strip():
            raise ValueError("diagnostics_key must be a non-empty string")
        self._entries[key] = ModelAssetResolverEntry(key, resolver, diagnostics_key.strip())

    def resolve(
        self,
        adapter_key: str,
        task_spec: Mapping[str, Any],
        *,
        requested_outputs: Sequence[str],
    ) -> tuple[Any, str]:
        from sat_sim_execution import validate_adapter_key

        key = validate_adapter_key(adapter_key)
        entry = self._entries.get(key)
        if entry is None:
            raise TaskSpecError(f"no model-asset resolver registered for adapter_key={key!r}")
        return entry.resolver(task_spec, requested_outputs=requested_outputs), entry.diagnostics_key

    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._entries))


def create_default_model_asset_resolver_registry() -> ModelAssetResolverRegistry:
    registry = ModelAssetResolverRegistry()
    from .a3r.resolution import resolve_attitude_control_execution
    from .a4r.resolution import resolve_composite_digital_twin_execution
    from .a5r.resolution import make_subsystem_resolver
    from sat_sim_model_assets.subsystem_verticals import SUBSYSTEM_BUNDLES

    registry.register(
        "basilisk.attitude_control_graph",
        resolve_attitude_control_execution,
        diagnostics_key="a3r_diagnostics",
    )
    registry.register(
        "basilisk.composite_digital_twin_graph",
        resolve_composite_digital_twin_execution,
        diagnostics_key="a4r_diagnostics",
    )
    for bundle in SUBSYSTEM_BUNDLES:
        registry.register(
            bundle.adapter_key,
            make_subsystem_resolver(bundle.adapter_key),
            diagnostics_key=bundle.diagnostics_key,
        )
    return registry


def get_model_asset_resolver_registry() -> ModelAssetResolverRegistry:
    global _RESOLVER_REGISTRY
    with _LOCK:
        if _RESOLVER_REGISTRY is None:
            _RESOLVER_REGISTRY = create_default_model_asset_resolver_registry()
        return _RESOLVER_REGISTRY


def create_default_execution_registry() -> ExecutionAdapterRegistry:
    """Create the logical adapter registry without eagerly importing engines.

    Factories import their concrete adapter only when the corresponding logical
    route is resolved.  A Python-only capability therefore does not require the
    optional Basilisk runtime merely to construct the composition root.
    """

    registry = ExecutionAdapterRegistry()

    def attitude_factory():  # noqa: ANN202
        from .a3r.adapter import AttitudeControlGraphExecutionAdapter
        return AttitudeControlGraphExecutionAdapter()

    def composite_factory():  # noqa: ANN202
        from .a4r.adapter import CompositeDigitalTwinGraphExecutionAdapter
        return CompositeDigitalTwinGraphExecutionAdapter()

    registry.register("basilisk.attitude_control_graph", attitude_factory)
    registry.register("basilisk.composite_digital_twin_graph", composite_factory)

    from sat_sim_model_assets.subsystem_verticals import SUBSYSTEM_BUNDLES

    for bundle in SUBSYSTEM_BUNDLES:
        def subsystem_factory(bundle=bundle):  # noqa: ANN202
            from .a5r.adapter import SubsystemGraphExecutionAdapter
            return SubsystemGraphExecutionAdapter(bundle)

        registry.register(bundle.adapter_key, subsystem_factory)

    registry.register_instance(RegisteredCapabilityExecutionAdapter())
    registry.register_instance(LegacyCapabilityAdapter())
    registry.register_instance(LegacyModuleAdapter())
    registry.register_instance(LegacyWholeSpacecraftAdapter())
    registry.register_instance(LegacyOrbitEnvironmentAdapter())
    return registry


def get_execution_registry() -> ExecutionAdapterRegistry:
    global _REGISTRY
    with _LOCK:
        if _REGISTRY is None:
            _REGISTRY = create_default_execution_registry()
        return _REGISTRY


def get_execution_port() -> ModelExecutionPort:
    global _PORT
    with _LOCK:
        if _PORT is None:
            _PORT = RegistryExecutionPort(get_execution_registry())
        return _PORT


def reset_execution_composition_root_for_tests() -> None:
    """Clear cached composition objects; intended only for isolated tests."""

    global _REGISTRY, _RESOLVER_REGISTRY, _PORT
    with _LOCK:
        _PORT = None
        _REGISTRY = None
        _RESOLVER_REGISTRY = None


def adapter_key_for_compiled(compiled: CompiledTask) -> str:
    metadata = compiled.metadata if isinstance(compiled.metadata, Mapping) else {}
    execution = metadata.get("model_asset_execution") if isinstance(metadata.get("model_asset_execution"), Mapping) else {}
    explicit_key = execution.get("adapter_key")
    if isinstance(explicit_key, str) and explicit_key.strip():
        from sat_sim_execution import validate_adapter_key

        return validate_adapter_key(explicit_key.strip())
    if metadata.get("capability_id"):
        return REGISTERED_CAPABILITY_ADAPTER_KEY
    if compiled.task_type in {"component", "subsystem"}:
        return LEGACY_MODULE_ADAPTER_KEY
    if compiled.task_type == "whole_spacecraft":
        return LEGACY_WHOLE_SPACECRAFT_ADAPTER_KEY
    if compiled.task_type == "orbit_environment":
        return LEGACY_ORBIT_ENVIRONMENT_ADAPTER_KEY
    if compiled.task_type == "campaign":
        raise TaskSpecError("campaign is an application orchestrator, not a model execution adapter")
    raise TaskSpecError(f"no unified execution route for task_type={compiled.task_type!r}")


def _stable_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _plan_hash(compiled: CompiledTask, task_spec: Mapping[str, Any], explicit: str | None) -> str:
    if isinstance(explicit, str) and len(explicit) == 64:
        return explicit.lower()
    planning = compiled.metadata.get("v24_planning") if isinstance(compiled.metadata, Mapping) else None
    if isinstance(planning, Mapping):
        value = planning.get("execution_plan_sha256")
        if isinstance(value, str) and len(value) == 64:
            return value.lower()
    return _stable_hash({
        "schema_version": "sat-sim.synthetic-execution-plan.v1",
        "compiled": compiled.to_dict(),
        "task_spec": dict(task_spec),
    })


def _effects(task_spec: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    out: list[dict[str, Any]] = []
    for kind in ("faults", "constraints"):
        value = task_spec.get(kind)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for item in value:
                if isinstance(item, Mapping):
                    out.append({"kind": kind[:-1], **dict(item)})
    degradations = task_spec.get("degradations")
    if isinstance(degradations, Mapping) and degradations:
        out.append({"kind": "degradation", "value": dict(degradations)})
    modifiers = task_spec.get("modifiers")
    if isinstance(modifiers, Mapping):
        for kind, value in modifiers.items():
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                for item in value:
                    if isinstance(item, Mapping):
                        out.append({"kind": str(kind), **dict(item)})
    return tuple(out)


def _requested_outputs(task_spec: Mapping[str, Any]) -> tuple[str, ...]:
    outputs = task_spec.get("outputs")
    if not isinstance(outputs, Mapping):
        return ()
    values: list[str] = []
    for key in ("fields", "telemetry_fields", "qoi", "metrics"):
        item = outputs.get(key)
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            values.extend(str(value) for value in item if isinstance(value, str) and value.strip())
    streams = outputs.get("telemetry_streams")
    if isinstance(streams, Sequence) and not isinstance(streams, (str, bytes, bytearray)):
        for stream in streams:
            if not isinstance(stream, Mapping):
                continue
            fields = stream.get("fields")
            if isinstance(fields, Sequence) and not isinstance(fields, (str, bytes, bytearray)):
                values.extend(str(value) for value in fields if isinstance(value, str) and value.strip())
    return tuple(dict.fromkeys(values))


def _resolve_model_asset_execution(
    adapter_key: str,
    task_spec: Mapping[str, Any],
    requested_outputs: Sequence[str],
) -> tuple[Any, str]:
    return get_model_asset_resolver_registry().resolve(
        adapter_key,
        task_spec,
        requested_outputs=requested_outputs,
    )


def _raise_for_model_asset_diagnostics(diagnostics: Sequence[Mapping[str, Any]]) -> None:
    errors = [
        item for item in diagnostics
        if str(item.get("severity") or "").lower() == "error"
    ]
    if not errors:
        return
    details = "; ".join(
        f"{item.get('code', 'MODEL_ASSET_DIAGNOSTIC')}: {item.get('kind', 'effect')}={item.get('effect_id', '<unknown>')}"
        for item in errors[:8]
    )
    raise TaskSpecError(f"model-asset execution binding failed: {details}")


def build_execution_request(
    compiled: CompiledTask,
    task_spec: Mapping[str, Any],
    *,
    execution_plan_sha256: str | None = None,
    request_id: str | None = None,
    output_root: str | Path | None = None,
    write_dataset: bool = False,
    bundle_root: str | Path | None = None,
    timeout_s: float = 300.0,
    resource_locks: Sequence[str] = (),
) -> ExecutionRequest:
    if not isinstance(task_spec, Mapping):
        raise TypeError("task_spec must be a mapping")
    adapter_key = adapter_key_for_compiled(compiled)
    plan_hash = _plan_hash(compiled, task_spec, execution_plan_sha256)
    capability_id = None
    if isinstance(compiled.metadata, Mapping):
        value = compiled.metadata.get("capability_id")
        capability_id = str(value) if isinstance(value, str) and value else None
    resolved_output = output_root if output_root is not None else compiled.outputs.get("output_root")
    rid = request_id or f"exec_{plan_hash[:16]}"

    execution_contract = compiled.metadata.get("model_asset_execution") if isinstance(compiled.metadata, Mapping) and isinstance(compiled.metadata.get("model_asset_execution"), Mapping) else {}
    if execution_contract and adapter_key == execution_contract.get("adapter_key"):
        from sat_sim_kernel import ImplementationId, ModelRef

        resolution, diagnostics_key = _resolve_model_asset_execution(
            adapter_key,
            task_spec,
            _requested_outputs(task_spec),
        )
        _raise_for_model_asset_diagnostics(resolution.diagnostics)
        bound_payload = resolution.bound_graph.to_dict()
        bound_payload["content_sha256"] = resolution.bound_graph.content_sha256
        return ExecutionRequest(
            request_id=rid,
            task_id=compiled.task_id,
            execution_plan_sha256=plan_hash,
            adapter_key=adapter_key,
            model_ref=ModelRef.parse(str(execution_contract.get("model_ref"))),
            implementation_id=ImplementationId.parse(str(execution_contract.get("implementation_id"))),
            capability_id=capability_id,
            parameters=dict(resolution.bound_graph.resolved_parameters),
            effects=_effects(resolution.runtime_task_spec),
            requested_outputs=resolution.bound_graph.requested_outputs,
            resource_locks=tuple(resource_locks),
            timeout_s=timeout_s,
            bundle_root=str(bundle_root) if bundle_root is not None else None,
            output_root=str(resolved_output) if resolved_output is not None else None,
            write_dataset=write_dataset,
            metadata={
                "unified_execution_version": UNIFIED_EXECUTION_VERSION,
                "legacy_route": False,
                "compiled_task_type": compiled.task_type,
                "compiled_backend": compiled.backend,
                "compiled_task": compiled.to_dict(),
                "canonical_task_spec": dict(task_spec),
                "resolved_runtime_task_spec": resolution.runtime_task_spec,
                "bound_model_graph": bound_payload,
                "model_projection": dict(compiled.metadata.get("model_projection") or {}),
                diagnostics_key: list(resolution.diagnostics),
                "model_asset_diagnostics": list(resolution.diagnostics),
            },
            legacy_payload=None,
        )

    parameters = task_spec.get("parameters") if isinstance(task_spec.get("parameters"), Mapping) else {}
    registered_capability_route = adapter_key == REGISTERED_CAPABILITY_ADAPTER_KEY
    metadata_payload = {
        "unified_execution_version": UNIFIED_EXECUTION_VERSION,
        "legacy_route": not registered_capability_route,
        "compiled_task_type": compiled.task_type,
        "compiled_backend": compiled.backend,
        "historical_runner": compiled.runner,
    }
    legacy_payload = None
    if registered_capability_route:
        metadata_payload.update({
            "compiled_task": compiled.to_dict(),
            "canonical_task_spec": dict(task_spec),
            "capability_adapter_route": True,
        })
    else:
        legacy_payload = {
            "compiled": compiled,
            "task_spec": dict(task_spec),
            "output_root": str(resolved_output) if resolved_output is not None else None,
            "write_dataset": write_dataset,
        }
    return ExecutionRequest(
        request_id=rid,
        task_id=compiled.task_id,
        execution_plan_sha256=plan_hash,
        adapter_key=adapter_key,
        capability_id=capability_id,
        parameters=dict(parameters),
        effects=_effects(task_spec),
        requested_outputs=_requested_outputs(task_spec),
        resource_locks=tuple(resource_locks),
        timeout_s=timeout_s,
        bundle_root=str(bundle_root) if bundle_root is not None else None,
        output_root=str(resolved_output) if resolved_output is not None else None,
        write_dataset=write_dataset,
        metadata=metadata_payload,
        legacy_payload=legacy_payload,
    )


def _execute_campaign(
    compiled: CompiledTask,
    task_spec: Mapping[str, Any],
    *,
    output_root: str | Path | None,
    write_dataset: bool,
) -> TaskRunResult:
    from .campaign import run_campaign_spec

    campaign_result = run_campaign_spec(
        task_spec,
        output_root=output_root or compiled.outputs.get("output_root"),
        continue_on_error=True,
        dry_run=False,
    )
    dataset = None
    if write_dataset:
        dataset = DatasetWriteResult(
            output_root=campaign_result.output_root,
            files=dict(campaign_result.files),
            manifest=dict(campaign_result.manifest),
        )
    return TaskRunResult(
        compiled=compiled,
        summary=dict(campaign_result.summary),
        trace_rows=tuple(dict(row) for row in campaign_result.case_rows),
        dataset=dataset,
        runtime_metadata={
            "unified_execution": {
                "schema_version": "sat-sim.execution-evidence.v1",
                "adapter_key": "application.campaign",
                "legacy_mode": False,
                "orchestrator": True,
                "child_execution_port": True,
            }
        },
    )


def execute_compiled_task(
    compiled: CompiledTask,
    *,
    task_spec: Mapping[str, Any],
    output_root: str | Path | None = None,
    write_dataset: bool = True,
    execution_plan_sha256: str | None = None,
    request_id: str | None = None,
    bundle_root: str | Path | None = None,
    timeout_s: float = 300.0,
    resource_locks: Sequence[str] = (),
    port: ModelExecutionPort | None = None,
) -> TaskRunResult:
    """Execute a compiled task through the A2R execution boundary."""

    if compiled.task_type == "campaign":
        return _execute_campaign(
            compiled,
            task_spec,
            output_root=output_root,
            write_dataset=write_dataset,
        )
    request = build_execution_request(
        compiled,
        task_spec,
        execution_plan_sha256=execution_plan_sha256,
        request_id=request_id,
        output_root=output_root,
        write_dataset=write_dataset,
        bundle_root=bundle_root,
        timeout_s=timeout_s,
        resource_locks=resource_locks,
    )
    result = (port or get_execution_port()).execute(request)
    native = result.native_result
    if isinstance(native, TaskRunResult):
        return native

    dataset = None
    dataset_payload = result.artifacts.get("dataset") if isinstance(result.artifacts, Mapping) else None
    if isinstance(dataset_payload, Mapping) and dataset_payload.get("output_root"):
        dataset = DatasetWriteResult(
            output_root=Path(str(dataset_payload["output_root"])),
            files=dict(dataset_payload.get("files") or {}),
            manifest=dict(dataset_payload.get("manifest") or {}),
        )
    return TaskRunResult(
        compiled=compiled,
        summary=dict(result.summary),
        trace_rows=tuple(dict(row) for row in result.trace_rows),
        dataset=dataset,
        runtime_metadata=dict(result.runtime_metadata),
    )


__all__ = [
    "ModelAssetResolverEntry",
    "ModelAssetResolverRegistry",
    "UNIFIED_EXECUTION_VERSION",
    "adapter_key_for_compiled",
    "build_execution_request",
    "create_default_execution_registry",
    "create_default_model_asset_resolver_registry",
    "execute_compiled_task",
    "get_execution_port",
    "get_execution_registry",
    "get_model_asset_resolver_registry",
    "reset_execution_composition_root_for_tests",
]
