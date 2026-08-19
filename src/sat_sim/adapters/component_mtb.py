"""Source-native magnetic torque bar component adapter.

This adapter exposes the existing pure-Python MTB model as an independent
component capability.  It intentionally models magnetic-dipole command shaping,
first-order response and ``m × B`` torque only; complete spacecraft detumbling
remains an ADCS subsystem responsibility.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from sat_sim.adapter_base import SimulationResult
from sat_sim.task_validator import ValidationIssue


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _float(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except Exception:
        return default


def _vector3(value: Any, default: tuple[float, float, float]) -> tuple[float, float, float]:
    if isinstance(value, (list, tuple)) and value:
        vals = [_float(v, 0.0) for v in value[:3]]
        vals += [0.0] * (3 - len(vals))
        return tuple(vals)  # type: ignore[return-value]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (float(value), float(value), float(value))
    return default


def _time_grid(spec: Mapping[str, Any]) -> tuple[float, float, int, list[float]]:
    sim = _mapping(spec.get("simulation"))
    duration_s = max(_float(sim.get("duration_s"), 60.0), 1.0e-9)
    sample_s = max(_float(sim.get("sample_s"), 1.0), 1.0e-9)
    n_steps = max(1, int(math.ceil(duration_s / sample_s)))
    return duration_s, sample_s, n_steps, [round(i * sample_s, 12) for i in range(n_steps + 1)]


def _normalized_events(spec: Mapping[str, Any], key: str, type_key: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    raw_top = spec.get(key)
    if isinstance(raw_top, list):
        out.extend(dict(x) for x in raw_top if isinstance(x, Mapping))
    modifiers = _mapping(spec.get("modifiers"))
    for item in modifiers.get(key, []) or []:
        if not isinstance(item, Mapping):
            continue
        payload = dict(item)
        payload.setdefault(type_key, payload.get("modifier_type") or payload.get("type") or payload.get("effect"))
        payload.setdefault("start_s", payload.get("onset_time_s", 0.0))
        if "end_s" not in payload and payload.get("duration_s") is not None:
            duration = _float(payload.get("duration_s"), -1.0)
            if duration >= 0:
                payload["end_s"] = _float(payload.get("start_s"), 0.0) + duration
        out.append(payload)
    return out


def _event_active(event: Mapping[str, Any], time_s: float) -> bool:
    start = _float(event.get("start_s", event.get("onset_time_s", 0.0)), 0.0)
    end_raw = event.get("end_s")
    if end_raw is None and event.get("duration_s") is not None:
        duration = _float(event.get("duration_s"), -1.0)
        end_raw = None if duration < 0 else start + duration
    return time_s >= start and (end_raw is None or time_s <= _float(end_raw, time_s))


def _params(event: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(event.get("parameters"))


class MtbAdapter:
    capability_id = "component.mtb.v1"
    target_name = "mtb"

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if spec.get("capability_id") != self.capability_id:
            issues.append(ValidationIssue("error", "$.capability_id", f"must be {self.capability_id!r}", "capability"))
        if spec.get("task_type") != "component":
            issues.append(ValidationIssue("error", "$.task_type", "MTB capability requires task_type='component'", "capability"))
        target = _mapping(spec.get("target"))
        if target.get("level") != "component" or target.get("name") != self.target_name:
            issues.append(ValidationIssue("error", "$.target", "requires target.level='component' and target.name='mtb'", "capability"))
        params = _mapping(spec.get("parameters"))
        if _float(params.get("lag_tau_s"), 0.0) < 0:
            issues.append(ValidationIssue("error", "$.parameters.lag_tau_s", "must be non-negative", "range"))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from components.mtb.builder import MtbState, compute_mtb_torque_nm, map_torque_to_dipole, update_mtb_dipole
        from components.mtb.schemas import MtbConfig

        params = _mapping(spec.get("parameters"))
        duration_s, sample_s, n_steps, times = _time_grid(spec)
        base_limits = _vector3(params.get("dipole_limit_am2"), (0.2, 0.2, 0.2))
        base_lag = max(_float(params.get("lag_tau_s"), 0.2), 0.0)
        field_t = _vector3(params.get("magnetic_field_t"), (2.0e-5, -1.0e-5, 3.0e-5))
        direct_command = _vector3(params.get("command_dipole_am2"), (0.1, 0.0, 0.0))
        requested_torque = _vector3(params.get("requested_torque_nm"), (0.0, 3.0e-6, 0.0))
        command_mode = str(params.get("command_mode") or "direct_dipole")
        profile_raw = params.get("command_profile_am2")
        profile = list(profile_raw) if isinstance(profile_raw, list) and profile_raw else []
        faults = _normalized_events(spec, "faults", "fault_type")
        degradations = _normalized_events(spec, "degradations", "degradation_type")

        state = MtbState(_vector3(params.get("initial_dipole_am2"), (0.0, 0.0, 0.0)))
        rows: list[dict[str, Any]] = []
        previous_command = direct_command
        any_fault = False
        any_degradation = False
        for i, time_s in enumerate(times):
            idx = min(max(i - 1, 0), n_steps - 1)
            desired = _vector3(profile[idx], direct_command) if profile else direct_command
            limits = list(base_limits)
            lag_tau = base_lag
            fault_active = False
            degradation_active = False
            communication_loss = False
            for event in faults:
                if not _event_active(event, time_s):
                    continue
                fault_active = True
                any_fault = True
                effect = str(event.get("fault_type") or event.get("effect") or "")
                ep = _params(event)
                axis = max(0, min(2, int(_float(ep.get("axis_index"), 0))))
                if effect == "mtb_coil_open":
                    limits[axis] = 0.0
                elif effect == "mtb_communication_loss":
                    communication_loss = True
                elif effect == "mtb_coil_short":
                    limits[axis] *= min(max(_float(ep.get("remaining_dipole_ratio"), 0.3), 0.0), 1.0)
            for event in degradations:
                if not _event_active(event, time_s):
                    continue
                degradation_active = True
                any_degradation = True
                effect = str(event.get("degradation_type") or event.get("effect") or "")
                ep = _params(event)
                if effect == "mtb_dipole_capacity_loss":
                    ratio = min(max(_float(ep.get("remaining_capacity_ratio"), 0.7), 0.0), 1.0)
                    limits = [x * ratio for x in limits]
                elif effect == "mtb_response_lag_increase":
                    lag_tau *= max(_float(ep.get("lag_multiplier"), 2.0), 1.0)
            cfg = MtbConfig(num_axes=3, dipole_limit_am2=tuple(limits), lag_tau_s=lag_tau)
            if command_mode == "requested_torque":
                desired = tuple(map_torque_to_dipole(requested_torque, field_t, cfg).dipole_am2)
            if communication_loss:
                desired = previous_command
            if i > 0:
                state = update_mtb_dipole(desired, state, cfg, sample_s)
            previous_command = desired
            torque = compute_mtb_torque_nm(state.dipole_am2, field_t)
            saturated = any(abs(state.dipole_am2[j]) >= max(limits[j] - 1.0e-12, 0.0) and abs(desired[j]) > limits[j] for j in range(3))
            row = {
                "task_id": str(spec.get("task_id", "component_mtb_task")),
                "case_id": str(_mapping(spec.get("metadata")).get("case_id", "case_000")),
                "time_s": time_s, "sample_index": i, "target_level": "component", "target_name": "mtb",
                "mode": str(_mapping(spec.get("target")).get("mode") or "nominal"),
                "adcs.mtb.command_dipole_am2_0": desired[0], "adcs.mtb.command_dipole_am2_1": desired[1], "adcs.mtb.command_dipole_am2_2": desired[2],
                "adcs.mtb.actual_dipole_am2_0": state.dipole_am2[0], "adcs.mtb.actual_dipole_am2_1": state.dipole_am2[1], "adcs.mtb.actual_dipole_am2_2": state.dipole_am2[2],
                "adcs.mtb.torque_nm_0": torque[0], "adcs.mtb.torque_nm_1": torque[1], "adcs.mtb.torque_nm_2": torque[2],
                "adcs.mtb.torque_norm_nm": math.sqrt(sum(x*x for x in torque)),
                "adcs.mtb.magnetic_field_t_0": field_t[0], "adcs.mtb.magnetic_field_t_1": field_t[1], "adcs.mtb.magnetic_field_t_2": field_t[2],
                "adcs.mtb.effective_dipole_limit_am2_0": limits[0], "adcs.mtb.effective_dipole_limit_am2_1": limits[1], "adcs.mtb.effective_dipole_limit_am2_2": limits[2],
                "adcs.mtb.saturation_flag": saturated,
                "label.fault_active": fault_active, "label.degradation_active": degradation_active,
                "label.health_state": "fault" if fault_active else ("degraded" if degradation_active else "nominal"),
            }
            rows.append(row)
        qoi = {
            "adcs.mtb.max_torque_norm_nm": max(float(r["adcs.mtb.torque_norm_nm"]) for r in rows),
            "adcs.mtb.final_dipole_norm_am2": math.sqrt(sum(float(rows[-1][f"adcs.mtb.actual_dipole_am2_{i}"])**2 for i in range(3))),
            "adcs.mtb.saturation_count": sum(1 for r in rows if r["adcs.mtb.saturation_flag"]),
        }
        mode = str(_mapping(spec.get("target")).get("mode") or "nominal")
        summary = {
            "task_id": str(spec.get("task_id", "component_mtb_task")), "case_id": str(_mapping(spec.get("metadata")).get("case_id", "case_000")),
            "status": "complete", "duration_s": duration_s, "sample_s": sample_s, "target_level": "component", "target_name": "mtb",
            "capability_id": self.capability_id, "mode": mode, "qoi": qoi,
            "events": {"fault_registered": bool(faults), "degradation_registered": bool(degradations), "fault_effect_observed": any_fault, "degradation_effect_observed": any_degradation},
            "trace_rows": len(rows),
        }
        labels = {"run_labels": [{"task_id": summary["task_id"], "mode": mode, "capability_id": self.capability_id}]}
        return SimulationResult(summary=summary, trace_rows=tuple(rows), labels=labels, metadata={"capability_id": self.capability_id})

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        raise NotImplementedError("central script exporter should be used")

    def output_schema(self, capability: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return dict(capability.get("outputs") or {}) if isinstance(capability, Mapping) else {"capability_id": self.capability_id}
