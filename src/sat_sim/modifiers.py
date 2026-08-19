"""Scenario modifier support for fault, degradation and constraint events.

Faults describe abnormal failures, degradations describe progressive performance
loss, and constraints describe normal operational limits or state transitions.
C7 deliberately treats these entries as audited scenario modifiers.
They decorate an already selected capability and are audited in trace rows,
summary, labels, and dataset manifests.  The implementation is conservative:
it does not invent new physics, but it records timing/targets and applies a few
safe, observable scalar transforms for common Agent-facing fields.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from .adapter_base import SimulationResult

MODIFIER_SCHEMA_VERSION = "c7.scenario_modifiers.v1"

SUPPORTED_TARGETS = {
    "eps.battery",
    "battery",
    "eps.solar_panel",
    "solar_panel",
    "reaction_wheel",
    "adcs.reaction_wheel",
    "comm.transmitter",
    "transmitter",
    "thermal.heater",
    "heater",
    "thermal.radiator",
    "radiator",
    "propulsion.thruster",
    "thruster",
    "propulsion.fuel_tank",
    "fuel_tank",
    "adcs.sensor",
    "payload.instrument",
    "payload.storage",
    "comm.storage",
}

FAULT_ALIASES = {
    "capacity_drop": "capacity_drop",
    "capacity_loss": "capacity_drop",
    "sudden_capacity_loss": "capacity_drop",
    "solar_panel_failure": "efficiency_drop",
    "efficiency_drop": "efficiency_drop",
    "transmitter_outage": "transmitter_outage",
    "link_loss": "transmitter_outage",
    "rw_torque_limit": "torque_limit_drop",
    "heater_stuck": "heater_stuck",
    "heater_failure": "heater_stuck",
}

DEGRADATION_ALIASES = {
    "efficiency_scale": "efficiency_scale",
    "efficiency_loss": "efficiency_scale",
    "capacity_fade": "capacity_fade",
    "capacity_scale": "capacity_fade",
    "torque_limit_scale": "torque_limit_scale",
    "downlink_scale": "downlink_scale",
    "heater_efficiency_scale": "heater_efficiency_scale",
}
CONSTRAINT_ALIASES = {
    "rw_speed_saturation": "reaction_wheel_speed_limit",
    "adcs_rw_speed_saturation": "adcs_reaction_wheel_speed_limit",
    "adcs_reaction_wheel_speed_limit": "adcs_reaction_wheel_speed_limit",
    "reaction_wheel_saturation": "reaction_wheel_speed_limit",
    "reaction_wheel_speed_limit": "reaction_wheel_speed_limit",
    "control_torque_saturation": "control_torque_limit",
    "momentum_unloading": "momentum_unloading",
}



@dataclass(frozen=True)
class ScenarioModifier:
    modifier_id: str
    kind: str
    target: str
    modifier_type: str
    onset_time_s: float = 0.0
    duration_s: float = -1.0
    severity: float | None = None
    scale: float | None = None
    parameters: dict[str, Any] | None = None

    @property
    def end_time_s(self) -> float | None:
        if self.duration_s == -1:
            return None
        return self.onset_time_s + self.duration_s

    def active_at(self, time_s: float) -> bool:
        if time_s < self.onset_time_s:
            return False
        end = self.end_time_s
        if end is None:
            return True
        return time_s <= end + 1e-12

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = MODIFIER_SCHEMA_VERSION
        payload["end_time_s"] = self.end_time_s
        return payload


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _float(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except Exception:
        return default


def _normalize_target(value: Any) -> str:
    text = str(value or "").strip().lower().replace("/", ".")
    aliases = {
        "eps.battery": "eps.battery",
        "battery": "eps.battery",
        "eps.solar": "eps.solar_panel",
        "solar_panel": "eps.solar_panel",
        "solar panel": "eps.solar_panel",
        "reaction_wheel": "adcs.reaction_wheel",
        "rw": "adcs.reaction_wheel",
        "adcs.reaction_wheel": "adcs.reaction_wheel",
        "comm.transmitter": "comm.transmitter",
        "transmitter": "comm.transmitter",
        "tx": "comm.transmitter",
        "thermal.heater": "thermal.heater",
        "heater": "thermal.heater",
        "thermal.radiator": "thermal.radiator",
        "radiator": "thermal.radiator",
        "propulsion.thruster": "propulsion.thruster",
        "thruster": "propulsion.thruster",
        "propulsion.fuel_tank": "propulsion.fuel_tank",
        "fuel_tank": "propulsion.fuel_tank",
        "fuel tank": "propulsion.fuel_tank",
        "adcs.sensor": "adcs.sensor",
        "sensor": "adcs.sensor",
        "payload.instrument": "payload.instrument",
        "payload": "payload.instrument",
        "payload.storage": "payload.storage",
        "comm.storage": "comm.storage",
        "comm_data.storage": "comm.storage",
    }
    return aliases.get(text, text)


def _modifier_type(kind: str, item: Mapping[str, Any]) -> str:
    if kind == "fault":
        raw = item.get("fault_type") or item.get("effect") or item.get("modifier_type") or item.get("type")
        return FAULT_ALIASES.get(str(raw or "").strip(), str(raw or "unknown"))
    if kind == "constraint":
        raw = item.get("constraint_type") or item.get("effect") or item.get("modifier_type") or item.get("type")
        return CONSTRAINT_ALIASES.get(str(raw or "").strip(), str(raw or "unknown"))
    raw = item.get("degradation_type") or item.get("effect") or item.get("modifier_type") or item.get("type")
    return DEGRADATION_ALIASES.get(str(raw or "").strip(), str(raw or "unknown"))


def _modifier_id(kind: str, idx: int, item: Mapping[str, Any]) -> str:
    return str(item.get("modifier_id") or item.get("fault_id") or item.get("degradation_id") or item.get("constraint_id") or item.get("event_id") or item.get("id") or f"{kind}_{idx}")


def _from_items(items: Any, *, kind: str) -> list[ScenarioModifier]:
    if not isinstance(items, list):
        return []
    out: list[ScenarioModifier] = []
    for idx, raw in enumerate(items):
        if not isinstance(raw, Mapping):
            continue
        target = _normalize_target(raw.get("target") or raw.get("target_path") or raw.get("target_id") or raw.get("target_type"))
        mtype = _modifier_type(kind, raw)
        onset = _float(raw.get("onset_time_s", raw.get("start_time_s", raw.get("start_s", 0.0))), 0.0)
        if raw.get("duration_s") is not None:
            duration = _float(raw.get("duration_s"), -1.0)
        elif raw.get("end_s") is not None:
            duration = max(0.0, _float(raw.get("end_s"), onset) - onset)
        else:
            duration = -1.0
        severity = raw.get("severity", raw.get("magnitude"))
        scale = raw.get("scale", raw.get("value_scale"))
        sev = None if severity is None else max(0.0, min(1.0, _float(severity, 0.0)))
        sca = None if scale is None else max(0.0, _float(scale, 1.0))
        out.append(ScenarioModifier(
            modifier_id=_modifier_id(kind, idx, raw),
            kind=kind,
            target=target,
            modifier_type=mtype,
            onset_time_s=max(0.0, onset),
            duration_s=duration if duration == -1 else max(0.0, duration),
            severity=sev,
            scale=sca,
            parameters=dict(_mapping(raw.get("parameters"))),
        ))
    return out


def normalize_modifiers(task_spec: Mapping[str, Any]) -> tuple[ScenarioModifier, ...]:
    """Return normalized scenario modifiers from modern and legacy fields."""

    modern = _mapping(task_spec.get("modifiers"))
    faults: list[ScenarioModifier] = []
    degradations: list[ScenarioModifier] = []
    constraints: list[ScenarioModifier] = []
    faults.extend(_from_items(modern.get("faults"), kind="fault"))
    degradations.extend(_from_items(modern.get("degradations"), kind="degradation"))
    constraints.extend(_from_items(modern.get("constraints"), kind="constraint"))
    # Canonical v0.5 TaskSpec stores event declarations under ``events``.
    # Treat those declarations as scenario modifiers for common registration,
    # audit and runtime-evidence handling.  Explicit ``modifiers`` entries take
    # precedence to preserve compatibility with older integrations.
    canonical_events = _mapping(task_spec.get("events"))
    if not faults:
        faults.extend(_from_items(canonical_events.get("faults"), kind="fault"))
    if not degradations:
        degradations.extend(_from_items(canonical_events.get("degradations"), kind="degradation"))
    if not constraints:
        constraints.extend(_from_items(canonical_events.get("constraints"), kind="constraint"))
    # Legacy TaskSpec fields remain accepted and are mirrored for audit.
    if not faults:
        faults.extend(_from_items(task_spec.get("faults"), kind="fault"))
    if not degradations:
        legacy_deg = task_spec.get("degradations")
        if isinstance(legacy_deg, list):
            degradations.extend(_from_items(legacy_deg, kind="degradation"))
        elif isinstance(legacy_deg, Mapping):
            # Convert nested legacy degradation maps into target-based modifiers.
            converted: list[dict[str, Any]] = []
            eps = legacy_deg.get("eps") if isinstance(legacy_deg.get("eps"), Mapping) else None
            if eps:
                if isinstance(eps.get("battery"), Mapping):
                    converted.append({"target": "eps.battery", "degradation_type": "capacity_fade", "parameters": dict(eps["battery"])})
                if isinstance(eps.get("solar_panel"), Mapping):
                    converted.append({"target": "eps.solar_panel", "degradation_type": "efficiency_scale", "parameters": dict(eps["solar_panel"])})
            adcs = legacy_deg.get("adcs") if isinstance(legacy_deg.get("adcs"), Mapping) else None
            if adcs and isinstance(adcs.get("reaction_wheel"), Mapping):
                converted.append({"target": "adcs.reaction_wheel", "degradation_type": "torque_limit_scale", "parameters": dict(adcs["reaction_wheel"])})
            degradations.extend(_from_items(converted, kind="degradation"))
    return tuple(faults + degradations + constraints)


def modifiers_task_spec_payload(task_spec: Mapping[str, Any]) -> dict[str, Any]:
    modifiers = [m.to_dict() for m in normalize_modifiers(task_spec)]
    return {
        "schema_version": MODIFIER_SCHEMA_VERSION,
        "count": len(modifiers),
        "fault_count": sum(1 for m in modifiers if m["kind"] == "fault"),
        "degradation_count": sum(1 for m in modifiers if m["kind"] == "degradation"),
        "constraint_count": sum(1 for m in modifiers if m["kind"] == "constraint"),
        "modifiers": modifiers,
        "runnable_capability": False,
    }


def _effect_scale(mod: ScenarioModifier) -> float:
    if mod.scale is not None:
        return max(0.0, float(mod.scale))
    if mod.severity is not None:
        return max(0.0, 1.0 - float(mod.severity))
    return 1.0


def _set_if_present(row: dict[str, Any], key: str, value: Any) -> None:
    if key in row:
        row[key] = value


def _apply_row_effect(row: dict[str, Any], mod: ScenarioModifier) -> None:
    scale = _effect_scale(mod)
    target = mod.target
    if target == "eps.battery":
        row["modifier.eps.battery.capacity_scale"] = scale
        for key in ("eps.battery.effective_capacity_wh", "eps.battery.capacity_wh", "battery.effective_capacity_wh", "battery.capacity_wh"):
            if key in row and isinstance(row[key], (int, float)):
                row[key] = float(row[key]) * scale
    elif target == "eps.solar_panel":
        row["modifier.eps.solar_panel.efficiency_scale"] = scale
        for key in ("eps.solar.array_power_w", "solar_panel.power_w", "power_w"):
            if key in row and isinstance(row[key], (int, float)):
                row[key] = float(row[key]) * scale
    elif target == "adcs.reaction_wheel":
        row["modifier.adcs.reaction_wheel.torque_scale"] = scale
        for key in ("adcs.reaction_wheel.effective_max_torque_nm_0", "reaction_wheel.effective_max_torque_nm"):
            if key in row and isinstance(row[key], (int, float)):
                row[key] = float(row[key]) * scale
    elif target == "comm.transmitter":
        row["modifier.comm.transmitter.available"] = False if mod.kind == "fault" else (scale > 0.0)
        for key in ("comm.tx.downlink_active", "comm.transmitter.enabled", "transmitter.enabled"):
            _set_if_present(row, key, False if mod.kind == "fault" and mod.modifier_type == "transmitter_outage" else row.get(key))
        for key in ("comm.link.data_rate_bps", "comm.link.effective_rate_bps", "comm.transmitter.effective_rate_bps"):
            if key in row and isinstance(row[key], (int, float)):
                row[key] = 0.0 if mod.kind == "fault" and mod.modifier_type == "transmitter_outage" else float(row[key]) * scale
    elif target == "thermal.heater":
        row["modifier.thermal.heater.scale"] = scale
        for key in ("thermal.heater.power_w", "heater.power_w"):
            if key in row and isinstance(row[key], (int, float)):
                row[key] = float(row[key]) * scale
    elif target == "thermal.radiator":
        row["modifier.thermal.radiator.scale"] = scale
        for key in ("thermal.radiator.heat_reject_w", "radiator.heat_rejection_w"):
            if key in row and isinstance(row[key], (int, float)):
                row[key] = float(row[key]) * scale


def apply_modifiers_to_result(
    result: SimulationResult,
    task_spec: Mapping[str, Any],
    *,
    application_mode: str = "audit_only",
) -> SimulationResult:
    """Attach modifier scheduling evidence without fabricating physics.

    ``audit_only`` is the default and only production-safe generic mode.  It
    adds event-window labels and audit metadata but never mutates physical
    telemetry.  A capability that implements runtime effects must set
    ``metadata.modifiers_applied_by_adapter=true`` and emit its own state/effect
    evidence.  ``legacy_trace_transform`` is retained solely for explicit
    compatibility runs and is classified as post-processing proxy evidence.
    """

    modifiers = normalize_modifiers(task_spec)
    if not modifiers:
        return result
    mode = str(application_mode or "audit_only")
    if mode not in {"audit_only", "legacy_trace_transform"}:
        raise ValueError(f"unsupported modifier application mode: {mode}")

    rows: list[dict[str, Any]] = []
    active_samples = 0
    for raw in result.trace_rows:
        row = dict(raw)
        time_s = _float(row.get("time_s"), 0.0)
        active = [m for m in modifiers if m.active_at(time_s)]
        if active:
            active_samples += 1
            row["label.modifier_active"] = True
            row["modifier.active_count"] = len(active)
            row["modifier.active_ids"] = ",".join(m.modifier_id for m in active)
            row["label.fault_active"] = bool(row.get("label.fault_active")) or any(m.kind == "fault" for m in active)
            row["label.degradation_active"] = bool(row.get("label.degradation_active")) or any(m.kind == "degradation" for m in active)
            row["label.constraint_active"] = bool(row.get("label.constraint_active")) or any(m.kind == "constraint" for m in active)
            if (row["label.fault_active"] or row["label.degradation_active"]) and row.get("label.health_state") in (None, "nominal"):
                row["label.health_state"] = "degraded_unverified"
            if mode == "legacy_trace_transform":
                for mod in active:
                    _apply_row_effect(row, mod)
        else:
            row.setdefault("label.modifier_active", False)
        rows.append(row)

    payload = modifiers_task_spec_payload(task_spec)
    payload.update({
        "active_sample_count": active_samples,
        "application_mode": mode,
        "physical_effect_verified": False,
        "evidence_classification": (
            "postprocess_proxy_unverified" if mode == "legacy_trace_transform" else "schedule_annotation_only"
        ),
        "boundary": (
            "Generic modifier handling does not update model state equations. "
            "Only adapter-owned runtime injection may be used as physical-effect evidence."
        ),
    })
    summary = dict(result.summary)
    summary.setdefault("events", {})
    if isinstance(summary["events"], dict):
        summary["events"]["applied_modifiers"] = payload
    summary["modifier_count"] = payload["count"]
    summary["fault_modifier_count"] = payload["fault_count"]
    summary["degradation_modifier_count"] = payload["degradation_count"]
    summary["constraint_modifier_count"] = payload["constraint_count"]
    summary["generic_modifier_physics_verified"] = False
    labels = dict(result.labels)
    labels["applied_modifiers"] = payload
    metadata = dict(result.metadata)
    metadata["applied_modifiers"] = payload
    metadata["modifier_evidence_classification"] = payload["evidence_classification"]
    return SimulationResult(summary=summary, trace_rows=tuple(rows), labels=labels, metadata=metadata)


def modifier_repair_hints(task_spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    for idx, mod in enumerate(normalize_modifiers(task_spec)):
        if mod.target not in {_normalize_target(x) for x in SUPPORTED_TARGETS}:
            hints.append({"path": f"modifiers[{idx}].target", "message": f"unsupported modifier target {mod.target!r}"})
        if mod.modifier_type == "unknown":
            hints.append({"path": f"modifiers[{idx}].type", "message": "modifier type is unknown; use a known fault_type/degradation_type/constraint_type"})
    return hints


__all__ = [
    "MODIFIER_SCHEMA_VERSION",
    "ScenarioModifier",
    "normalize_modifiers",
    "modifiers_task_spec_payload",
    "apply_modifiers_to_result",
    "modifier_repair_hints",
]
