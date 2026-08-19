"""Resolve an O-side attitude-control call into an auditable bound model graph."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from sat_sim_kernel import LocalId, ModelRef
from sat_sim_model_assets import BoundModelGraph
from sat_sim_model_assets.attitude_control import (
    A3_ADAPTER_KEY,
    ASSET_REGISTRY,
    ATTITUDE_CONTROL_BINDINGS,
    ATTITUDE_CONTROL_PROJECTION,
    BASILISK_SPACECRAFT_IMPLEMENTATION,
    BASILISK_SPACECRAFT_PROFILE,
    PARAMETER_SET,
    SPACECRAFT_GRAPH,
    SPACECRAFT_REF,
)

from ..task_models import to_runtime_task_spec


@dataclass(frozen=True)
class A3Resolution:
    bound_graph: BoundModelGraph
    runtime_task_spec: dict[str, Any]
    diagnostics: tuple[dict[str, Any], ...] = ()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _object_calls(runtime_spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    metadata = _mapping(runtime_spec.get("metadata"))
    raw = metadata.get("object_calls") or metadata.get("operations_object_calls") or []
    return [dict(item) for item in _sequence(raw) if isinstance(item, Mapping)]


def _call_ref(call: Mapping[str, Any]) -> str:
    return str(call.get("ref") or call.get("action") or call.get("effect") or "").strip()


def _time_value(parameters: Mapping[str, Any], name: str, default: float | None) -> float | None:
    value = parameters.get(name, default)
    if value is None:
        return None
    return float(value)


def _append_object_call_events(runtime: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    modifiers = runtime.setdefault("modifiers", {})
    if not isinstance(modifiers, dict):
        modifiers = {}
        runtime["modifiers"] = modifiers
    faults = modifiers.setdefault("faults", [])
    constraints = modifiers.setdefault("constraints", [])
    if not isinstance(faults, list):
        faults = []
        modifiers["faults"] = faults
    if not isinstance(constraints, list):
        constraints = []
        modifiers["constraints"] = constraints

    actions: list[dict[str, Any]] = []
    effects: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    duration_s = float(_mapping(runtime.get("simulation")).get("duration_s") or 120.0)

    for index, call in enumerate(_object_calls(runtime)):
        ref = _call_ref(call)
        parameters = dict(_mapping(call.get("parameters")))
        start_s = max(0.0, _time_value(parameters, "start_s", 0.0) or 0.0)
        end_s = _time_value(parameters, "end_s", duration_s)
        if end_s is not None:
            end_s = min(duration_s, max(start_s, end_s))
        call_id = str(call.get("id") or f"object_call_{index + 1}")

        if ref == "spacecraft.attitude_control.enter_safe_mode":
            payload = {
                "constraint_id": call_id,
                "target": "whole_spacecraft",
                "constraint_type": "power_safe_mode_threshold",
                "onset_time_s": start_s,
                "duration_s": -1.0 if end_s is None else max(0.0, end_s - start_s),
                "parameters": {"soc_threshold": 1.0},
                "label": "A3R object action: enter safe mode",
            }
            constraints.append(payload)
            actions.append({
                "call_id": call_id,
                "object_action": ref,
                "binding_id": "enter-safe-mode",
                "target_input": "power_manager.safe_mode_request",
                "resolved_value": True,
                "runtime_event": payload,
            })
        elif ref == "spacecraft.attitude_control.inject_rw_jamming":
            wheel_index = int(parameters.get("wheel_index", 0))
            payload = {
                "modifier_id": call_id,
                "target": f"adcs.reaction_wheels[{wheel_index}]",
                "fault_type": "adcs_rw_jamming",
                "onset_time_s": start_s,
                "duration_s": -1.0 if end_s is None else max(0.0, end_s - start_s),
                "severity": 1.0,
                "parameters": {"wheel_index": wheel_index},
            }
            faults.append(payload)
            effects.append({
                "call_id": call_id,
                "object_action": ref,
                "binding_id": "inject-rw-jamming",
                "effect_id": "rw_jamming",
                "runtime_effect": "adcs_rw_jamming",
                "target_node": "reaction_wheels",
                "parameters": {"wheel_index": wheel_index},
                "runtime_event": payload,
            })
        elif ref:
            diagnostics.append({
                "severity": "warning",
                "code": "A3R_OBJECT_CALL_NOT_BOUND",
                "call_id": call_id,
                "ref": ref,
            })
    return actions, effects, diagnostics


def resolve_attitude_control_execution(task_spec: Mapping[str, Any], *, requested_outputs: Sequence[str] = ()) -> A3Resolution:
    """Resolve parameter, action, effect and evidence bindings without importing Basilisk."""

    runtime = to_runtime_task_spec(task_spec)
    runtime = deepcopy(runtime)
    runtime_parameters = runtime.get("parameters")
    if not isinstance(runtime_parameters, Mapping):
        runtime_parameters = {}
    merged_parameters = dict(PARAMETER_SET.values)
    merged_parameters.update(dict(runtime_parameters))
    runtime["parameters"] = merged_parameters
    actions, effects, diagnostics = _append_object_call_events(runtime)

    outputs = list(dict.fromkeys([
        *requested_outputs,
        "adcs.pointing_error_deg",
        "adcs.rw.speed_rad_s_0",
        "adcs.rw.speed_rad_s_1",
        "adcs.rw.speed_rad_s_2",
        "eps.battery_soc",
        "payload.active",
        "label.power_safe_mode_engaged",
    ]))

    bound = BoundModelGraph(
        bound_graph_id=LocalId("attitude-control-run"),
        graph_ref=SPACECRAFT_GRAPH.graph_ref,
        graph_sha256=SPACECRAFT_GRAPH.content_sha256,
        root_model_ref=SPACECRAFT_REF,
        implementation_id=BASILISK_SPACECRAFT_IMPLEMENTATION,
        implementation_profile_sha256=BASILISK_SPACECRAFT_PROFILE.content_sha256,
        parameter_set_id=PARAMETER_SET.parameter_set_id,
        parameter_set_sha256=PARAMETER_SET.content_sha256,
        binding_set_id=ATTITUDE_CONTROL_BINDINGS.binding_set_id,
        binding_set_sha256=ATTITUDE_CONTROL_BINDINGS.content_sha256,
        capability_projection_id=ATTITUDE_CONTROL_PROJECTION.projection_id,
        capability_projection_sha256=ATTITUDE_CONTROL_PROJECTION.content_sha256,
        resolved_parameters=merged_parameters,
        resolved_actions=tuple(actions),
        resolved_effects=tuple(effects),
        requested_outputs=tuple(outputs),
    )
    return A3Resolution(bound, runtime, tuple(diagnostics))


def is_a3_capability_metadata(metadata: Mapping[str, Any]) -> bool:
    execution = _mapping(metadata.get("model_asset_execution"))
    return execution.get("adapter_key") == A3_ADAPTER_KEY


__all__ = ["A3Resolution", "is_a3_capability_metadata", "resolve_attitude_control_execution"]
