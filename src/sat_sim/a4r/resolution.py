"""Resolve the composite digital-twin capability into an auditable model graph."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from sat_sim_kernel import LocalId
from sat_sim_model_assets import BoundModelGraph
from sat_sim_model_assets.composite_digital_twin import (
    COMPOSITE_ADAPTER_KEY,
    COMPOSITE_BINDINGS,
    COMPOSITE_GRAPH,
    COMPOSITE_IMPLEMENTATION,
    COMPOSITE_IMPLEMENTATION_PROFILE,
    COMPOSITE_PARAMETER_SET,
    COMPOSITE_PROJECTION,
    COMPOSITE_SPACECRAFT_REF,
    DEGRADATION_EFFECT_IDS,
    EFFECT_IDS,
    FAULT_EFFECT_IDS,
)

from ..task_models import to_runtime_task_spec


COMPOSITE_REQUIRED_OUTPUTS = (
    "payload_generated_bps",
    "payload.payload_generated_bps",
    "data_storage_bits",
    "storage.data_storage_bits",
    "downlink_delivered_bps",
    "comm.downlink_delivered_bps",
    "comm.removed_bps",
    "comm.legacy_transmitter_removed_bps",
    "downlink_requested_rate_bps",
    "cumulative_delivered_bits",
    "eps.battery_soc",
    "battery_soc",
    "eps.net_power_w",
    "eps.pdu_payload_enabled",
    "eps.pdu_comm_enabled",
    "eps.pdu_heater_enabled",
    "adcs.attitude_error_deg",
    "attitude_error_deg",
    "adcs.pointing_gate",
    "adcs.adcs_control_power_w",
    "thermal.thermal_temp_c",
    "thermal_temp_c",
    "thermal.thermal_adcs_temp_c",
    "thermal.thermal_structure_temp_c",
    "thermal.thermal_solar_panel_temp_c",
    "thermal.heater_active_count",
    "thermal.heater_eps_load_w",
    "orbit_environment.solar_power_available_w",
    "orbit_environment.eclipse_shadow_factor",
    "orbit_environment.ground_slant_range_m",
    "orbit_environment.gravity_accel_m_s2",
    "comm.rf_link_distance_m",
    "propulsion.propulsion_power_w",
    "propulsion.propulsion_heat_w",
    "propulsion.propellant_used_kg",
    "propulsion.propellant_remaining_kg",
    "propulsion.orbit_perturbation_m",
    "spacecraft_dynamics.orbit_radius_m",
    "spacecraft_dynamics.spacecraft_position_x_m",
)


@dataclass(frozen=True)
class A4Resolution:
    bound_graph: BoundModelGraph
    runtime_task_spec: dict[str, Any]
    diagnostics: tuple[dict[str, Any], ...] = ()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _effect_candidate(item: Mapping[str, Any], *, fallback_key: str) -> str:
    parameters = _mapping(item.get("parameters"))
    candidate = str(
        parameters.get("scenario")
        or item.get(fallback_key)
        or item.get("effect")
        or item.get("modifier_type")
        or item.get("type")
        or ""
    ).strip()
    if candidate == "capacity_fade" and float(parameters.get("capacity_loss_pct") or 0.0) == 30.0:
        return "battery_capacity_loss_30pct"
    return candidate


def _modifier_id(item: Mapping[str, Any], *, prefix: str, index: int) -> str:
    return str(
        item.get("modifier_id")
        or item.get(f"{prefix}_id")
        or item.get("id")
        or f"{prefix}_{index:03d}"
    )


def _resolved_effect_payload(item: Mapping[str, Any], *, effect_id: str, kind: str, index: int) -> dict[str, Any]:
    source = dict(item)
    return {
        "modifier_id": _modifier_id(item, prefix=kind, index=index),
        "effect_id": effect_id,
        "kind": kind,
        "target": str(item.get("target") or "whole_spacecraft"),
        "onset_time_s": float(item.get("onset_time_s", item.get("start_time_s", 0.0)) or 0.0),
        "duration_s": float(item.get("duration_s", -1.0) or -1.0),
        "severity": float(item.get("severity", item.get("magnitude", 1.0)) or 1.0),
        "parameters": dict(_mapping(item.get("parameters"))),
        "source_modifier": source,
    }


def _resolve_effects(runtime: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    modifiers = _mapping(runtime.get("modifiers"))
    effects: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []

    for index, item in enumerate(_sequence(modifiers.get("faults"))):
        if not isinstance(item, Mapping):
            continue
        effect_id = _effect_candidate(item, fallback_key="fault_type")
        if effect_id in FAULT_EFFECT_IDS:
            effects.append(_resolved_effect_payload(item, effect_id=effect_id, kind="fault", index=index))
        elif effect_id:
            diagnostics.append({
                "severity": "error",
                "code": "A4R_EFFECT_NOT_BOUND",
                "kind": "fault",
                "effect_id": effect_id,
                "modifier_id": _modifier_id(item, prefix="fault", index=index),
            })

    for index, item in enumerate(_sequence(modifiers.get("degradations"))):
        if not isinstance(item, Mapping):
            continue
        effect_id = _effect_candidate(item, fallback_key="degradation_type")
        if effect_id in DEGRADATION_EFFECT_IDS:
            effects.append(_resolved_effect_payload(item, effect_id=effect_id, kind="degradation", index=index))
        elif effect_id:
            diagnostics.append({
                "severity": "error",
                "code": "A4R_EFFECT_NOT_BOUND",
                "kind": "degradation",
                "effect_id": effect_id,
                "modifier_id": _modifier_id(item, prefix="degradation", index=index),
            })

    if not effects and not diagnostics:
        effects.append({
            "modifier_id": "nominal",
            "effect_id": "nominal",
            "kind": "mode",
            "target": "whole_spacecraft",
            "parameters": {},
        })
    return effects, diagnostics


def resolve_composite_digital_twin_execution(
    task_spec: Mapping[str, Any],
    *,
    requested_outputs: Sequence[str] = (),
) -> A4Resolution:
    """Resolve A4R parameter, effect and evidence bindings without importing Basilisk."""

    runtime = deepcopy(to_runtime_task_spec(task_spec))
    runtime_parameters = runtime.get("parameters")
    if not isinstance(runtime_parameters, Mapping):
        runtime_parameters = {}
    merged_parameters = dict(COMPOSITE_PARAMETER_SET.values)
    merged_parameters.update(dict(runtime_parameters))
    runtime["parameters"] = merged_parameters

    effects, diagnostics = _resolve_effects(runtime)
    outputs = tuple(dict.fromkeys([*requested_outputs, *COMPOSITE_REQUIRED_OUTPUTS]))

    bound = BoundModelGraph(
        bound_graph_id=LocalId("composite-digital-twin-run"),
        graph_ref=COMPOSITE_GRAPH.graph_ref,
        graph_sha256=COMPOSITE_GRAPH.content_sha256,
        root_model_ref=COMPOSITE_SPACECRAFT_REF,
        implementation_id=COMPOSITE_IMPLEMENTATION,
        implementation_profile_sha256=COMPOSITE_IMPLEMENTATION_PROFILE.content_sha256,
        parameter_set_id=COMPOSITE_PARAMETER_SET.parameter_set_id,
        parameter_set_sha256=COMPOSITE_PARAMETER_SET.content_sha256,
        binding_set_id=COMPOSITE_BINDINGS.binding_set_id,
        binding_set_sha256=COMPOSITE_BINDINGS.content_sha256,
        capability_projection_id=COMPOSITE_PROJECTION.projection_id,
        capability_projection_sha256=COMPOSITE_PROJECTION.content_sha256,
        resolved_parameters=merged_parameters,
        resolved_effects=tuple(effects),
        requested_outputs=outputs,
    )
    return A4Resolution(bound, runtime, tuple(diagnostics))


def is_a4_capability_metadata(metadata: Mapping[str, Any]) -> bool:
    execution = _mapping(metadata.get("model_asset_execution"))
    return execution.get("adapter_key") == COMPOSITE_ADAPTER_KEY


__all__ = [
    "A4Resolution",
    "COMPOSITE_REQUIRED_OUTPUTS",
    "is_a4_capability_metadata",
    "resolve_composite_digital_twin_execution",
]
