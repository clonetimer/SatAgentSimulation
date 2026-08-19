"""Source-native subsystem adapters for coverage-completion C4/C5.

These adapters wrap pure-Python subsystem builder/model APIs from ``src/subsystems``.
They do not call demo ``runner.py`` modules and do not add physics beyond the
existing source subsystem models.  ADCS/whole-spacecraft Basilisk-only readiness
is documented separately rather than forced into a brittle wrapper.
"""
from __future__ import annotations

import math
from dataclasses import replace
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


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", "disabled"}
    return bool(value)


def _profile(params: Mapping[str, Any], key: str, scalar_key: str, default: Any, n_steps: int) -> list[Any]:
    raw = params.get(key)
    if isinstance(raw, list) and raw:
        values = list(raw)
    else:
        values = [params.get(scalar_key, default)]
    if len(values) < n_steps:
        values += [values[-1]] * (n_steps - len(values))
    return values[:n_steps]


def _numeric_profile(params: Mapping[str, Any], key: str, scalar_key: str, default: float, n_steps: int) -> list[float]:
    return [_float(v, default) for v in _profile(params, key, scalar_key, default, n_steps)]


def _bool_profile(params: Mapping[str, Any], key: str, scalar_key: str, default: bool, n_steps: int) -> list[bool]:
    return [_bool(v, default) for v in _profile(params, key, scalar_key, default, n_steps)]


def _str_profile(params: Mapping[str, Any], key: str, scalar_key: str, default: str, n_steps: int) -> list[str]:
    return [str(v) for v in _profile(params, key, scalar_key, default, n_steps)]


def _time_grid(spec: Mapping[str, Any]) -> tuple[float, float, int]:
    sim = _mapping(spec.get("simulation"))
    duration_s = _float(sim.get("duration_s"), 300.0)
    sample_s = _float(sim.get("sample_s"), 10.0)
    n_steps = max(1, int(math.ceil(duration_s / sample_s)))
    return duration_s, sample_s, n_steps


def _event_rows(spec: Mapping[str, Any], kind: str) -> tuple[dict[str, Any], ...]:
    plural = f"{kind}s"
    rows: list[dict[str, Any]] = []
    for container_key in ("events", "modifiers"):
        container = spec.get(container_key)
        if isinstance(container, Mapping):
            rows.extend(dict(item) for item in (container.get(plural) or ()) if isinstance(item, Mapping))
    if isinstance(spec.get(plural), list):
        rows.extend(dict(item) for item in spec[plural] if isinstance(item, Mapping))
    return tuple(rows)


def _effect_id(event: Mapping[str, Any], kind: str) -> str:
    return str(event.get(f"{kind}_type") or event.get("modifier_type") or event.get("effect") or event.get("type") or "")


def _event_active(event: Mapping[str, Any], time_s: float) -> bool:
    start = _float(event.get("start_s", event.get("onset_time_s")), 0.0)
    end = event.get("end_s")
    if end is None and event.get("duration_s") is not None and _float(event.get("duration_s"), -1.0) >= 0.0:
        end = start + _float(event.get("duration_s"), 0.0)
    return time_s >= start and (end is None or time_s <= _float(end, time_s))


def _degradation_fraction(event: Mapping[str, Any], time_s: float) -> float:
    if not _event_active(event, time_s):
        return 0.0
    params = _mapping(event.get("parameters"))
    start = _float(event.get("start_s", event.get("onset_time_s")), 0.0)
    rate = max(0.0, _float(params.get("rate_per_s"), 0.05))
    maximum = min(1.0, max(0.0, _float(params.get("max_fraction", event.get("severity")), 0.8)))
    return min(maximum, rate * max(0.0, time_s - start))


def _row_base(spec: Mapping[str, Any], target_name: str, idx: int, time_s: float, mode: str) -> dict[str, Any]:
    metadata = _mapping(spec.get("metadata"))
    return {
        "task_id": str(spec.get("task_id", f"subsystem_{target_name}_task")),
        "case_id": str(metadata.get("case_id", "case_000")),
        "time_s": float(time_s),
        "sample_index": int(idx),
        "target_level": "subsystem",
        "target_name": target_name,
        "mode": str(mode),
    }


class _BaseSubsystemAdapter:
    capability_id: str = ""
    target_name: str = ""
    supported_mode: str = "nominal"

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if spec.get("capability_id") != self.capability_id:
            issues.append(ValidationIssue("error", "$.capability_id", f"must be {self.capability_id!r}", "capability"))
        if spec.get("task_type") != "subsystem":
            issues.append(ValidationIssue("error", "$.task_type", "source-native subsystem capability requires task_type='subsystem'", "capability"))
        target = _mapping(spec.get("target"))
        if target.get("level") != "subsystem" or target.get("name") != self.target_name:
            issues.append(ValidationIssue("error", "$.target", f"requires target.level='subsystem' and target.name='{self.target_name}'", "capability"))
        sim = _mapping(spec.get("simulation"))
        for key in ("duration_s", "sample_s"):
            if key in sim and (isinstance(sim[key], bool) or not isinstance(sim[key], (int, float)) or float(sim[key]) <= 0):
                issues.append(ValidationIssue("error", f"$.simulation.{key}", "must be a positive number", "range"))
        return tuple(issues)

    def _result(self, spec: Mapping[str, Any], rows: list[dict[str, Any]], qoi: dict[str, Any], events: dict[str, Any] | None = None) -> SimulationResult:
        duration_s, sample_s, _ = _time_grid(spec)
        mode = str(_mapping(spec.get("target")).get("mode") or self.supported_mode)
        summary = {
            "task_id": str(spec.get("task_id", f"subsystem_{self.target_name}_task")),
            "case_id": str(_mapping(spec.get("metadata")).get("case_id", "case_000")),
            "status": "complete",
            "duration_s": duration_s,
            "sample_s": sample_s,
            "target_level": "subsystem",
            "target_name": self.target_name,
            "capability_id": self.capability_id,
            "mode": mode,
            "qoi": qoi,
            "events": events or {},
            "trace_rows": len(rows),
        }
        labels = {"run_labels": [{"task_id": summary["task_id"], "mode": mode, "capability_id": self.capability_id}]}
        return SimulationResult(summary=summary, trace_rows=tuple(rows), labels=labels, metadata={"capability_id": self.capability_id})

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        raise NotImplementedError("S6 central script exporter should be used for capability-python generation")

    def output_schema(self, capability: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return {"capability_id": self.capability_id, "target": self.target_name}


class EpsSourceNativeAdapter(_BaseSubsystemAdapter):
    capability_id = "subsystem.eps.source_native.v1"
    target_name = "eps"

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from subsystems.eps.builder import build_nominal_eps_config
        from subsystems.eps.model import initialize_eps_state, simulate_eps_profile
        from subsystems.eps.schemas import EpsStepInput

        params = _mapping(spec.get("parameters"))
        _, sample_s, n_steps = _time_grid(spec)
        cfg = build_nominal_eps_config()
        cfg = replace(
            cfg,
            battery=replace(cfg.battery, capacity_wh=_float(params.get("battery_capacity_wh"), cfg.battery.capacity_wh), initial_soc=_float(params.get("initial_soc"), cfg.battery.initial_soc)),
            solar_panel=replace(cfg.solar_panel, max_power_w=_float(params.get("solar_power_w"), cfg.solar_panel.max_power_w), efficiency=_float(params.get("solar_efficiency"), cfg.solar_panel.efficiency)),
            pdu=replace(cfg.pdu, bus_max_w=_float(params.get("pdu_bus_max_w"), cfg.pdu.bus_max_w)),
        )
        base_loads = {"obc": _float(params.get("bus_power_w"), 10.0), "thermal": _float(params.get("thermal_power_w"), 5.0), "adcs": _float(params.get("adcs_power_w"), 10.0), "comm": _float(params.get("comm_power_w"), 5.0), "payload": _float(params.get("payload_power_w"), 30.0), "thruster": _float(params.get("thruster_power_w"), 0.0)}
        shadow = _numeric_profile(params, "shadow_profile", "shadow_factor", 1.0, n_steps)
        modes = _str_profile(params, "mode_profile", "mode", "payload", n_steps)
        steps = [EpsStepInput(dt_s=sample_s, mode=modes[i], shadow_factor=shadow[i], requested_loads_w=base_loads) for i in range(n_steps)]
        _, _, results = simulate_eps_profile(initialize_eps_state(cfg), cfg, steps)
        rows: list[dict[str, Any]] = []
        for idx, r in enumerate(results):
            row = _row_base(spec, self.target_name, idx, r.time_s, r.mode)
            row.update({
                "eps.source_native.solar_power_w": r.solar_power_w,
                "eps.source_native.load_requested_w": r.load_requested_w,
                "eps.source_native.load_served_w": r.load_served_w,
                "eps.source_native.net_power_w": r.net_power_w,
                "eps.source_native.battery_soc": r.battery_soc,
                "eps.source_native.battery_storage_wh": r.battery_storage_wh,
                "eps.source_native.shed_loads": ",".join(r.shed_loads),
                "label.load_shed_active": bool(r.shed_loads or r.low_soc_shed_loads),
                "label.health_state": "degraded" if (r.shed_loads or r.overload_remaining) else "nominal",
            })
            rows.append(row)
        qoi = {"eps.final_soc": rows[-1]["eps.source_native.battery_soc"], "eps.min_soc": min(r["eps.source_native.battery_soc"] for r in rows), "eps.max_load_requested_w": max(r["eps.source_native.load_requested_w"] for r in rows)}
        return self._result(spec, rows, qoi)


class CommDataSourceNativeAdapter(_BaseSubsystemAdapter):
    capability_id = "subsystem.comm_data.source_native.v1"
    target_name = "comm_data"

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from dataclasses import replace as dc_replace
        from subsystems.comm_data.builder import build_nominal_comm_data_config
        from subsystems.comm_data.model import initialize_comm_data_state, simulate_comm_data_profile
        from subsystems.comm_data.schemas import CommDataStepInput

        params = _mapping(spec.get("parameters"))
        _, sample_s, n_steps = _time_grid(spec)
        cfg = build_nominal_comm_data_config()
        cfg = dc_replace(
            cfg,
            queue=dc_replace(cfg.queue, capacity_bits=_float(params.get("queue_capacity_bits"), cfg.queue.capacity_bits)),
            default_generated_bps=_float(params.get("generated_bps"), cfg.default_generated_bps),
            max_downlink_bps=_float(params.get("max_downlink_bps"), cfg.max_downlink_bps or cfg.link.raw_rate_bps),
            require_access_for_downlink=_bool(params.get("require_access_for_downlink"), True),
            require_eps_permission=_bool(params.get("require_eps_permission"), True),
        )
        modes = _str_profile(params, "mode_profile", "mode", "payload", n_steps)
        generated = _numeric_profile(params, "generated_bps_profile", "generated_bps", cfg.default_generated_bps, n_steps)
        access = _bool_profile(params, "access_profile", "access_override", False, n_steps)
        eps_ok = _bool_profile(params, "eps_allows_downlink_profile", "eps_allows_downlink", True, n_steps)
        steps = [CommDataStepInput(dt_s=sample_s, mode=modes[i], generated_bps=generated[i], access_override=access[i], eps_allows_downlink=eps_ok[i]) for i in range(n_steps)]
        _, _, results = simulate_comm_data_profile(dc_replace(initialize_comm_data_state(cfg), queue=dc_replace(initialize_comm_data_state(cfg).queue, queue_bits=_float(params.get("initial_queue_bits"), 0.0))), cfg, steps)
        rows: list[dict[str, Any]] = []
        for idx, r in enumerate(results):
            row = _row_base(spec, self.target_name, idx, r.time_s, r.mode)
            row.update({
                "comm_data.source_native.queue_bits": r.queue_bits,
                "comm_data.source_native.generated_bps": r.generated_bps,
                "comm_data.source_native.downlink_rate_bps": r.downlink_rate_bps,
                "comm_data.source_native.has_access": r.has_access,
                "comm_data.source_native.eps_allows_downlink": r.eps_allows_downlink,
                "comm_data.source_native.cumulative_downlinked_bits": r.cumulative_downlinked_bits,
                "comm_data.source_native.cumulative_dropped_bits": r.cumulative_dropped_bits,
                "label.comm_state": "downlink" if r.downlink_rate_bps > 0 else "store",
                "label.health_state": "degraded" if r.dropped_bits_step > 0 else "nominal",
            })
            rows.append(row)
        qoi = {"comm_data.final_queue_bits": rows[-1]["comm_data.source_native.queue_bits"], "comm_data.total_downlinked_bits": rows[-1]["comm_data.source_native.cumulative_downlinked_bits"], "comm_data.total_dropped_bits": rows[-1]["comm_data.source_native.cumulative_dropped_bits"]}
        return self._result(spec, rows, qoi)


class ThermalSourceNativeAdapter(_BaseSubsystemAdapter):
    capability_id = "subsystem.thermal.source_native.v1"
    target_name = "thermal"

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from dataclasses import replace as dc_replace
        from subsystems.thermal.builder import build_nominal_thermal_config
        from subsystems.thermal.model import initialize_thermal_state, simulate_thermal_profile
        from subsystems.thermal.schemas import ThermalStepInput

        params = _mapping(spec.get("parameters"))
        _, sample_s, n_steps = _time_grid(spec)
        cfg = build_nominal_thermal_config()
        if "initial_battery_temp_k" in params:
            temps = dict(cfg.initial_temp_k_by_node)
            temps["battery"] = _float(params.get("initial_battery_temp_k"), temps.get("battery", 290.0))
            cfg = dc_replace(cfg, initial_temp_k_by_node=temps)
        modes = _str_profile(params, "mode_profile", "mode", "payload", n_steps)
        shadow = _numeric_profile(params, "shadow_profile", "shadow_factor", 1.0, n_steps)
        payload_power = _numeric_profile(params, "payload_power_profile_w", "payload_power_w", 20.0, n_steps)
        eps_power = _numeric_profile(params, "eps_power_profile_w", "eps_power_w", 5.0, n_steps)
        heater_enabled = _bool_profile(params, "heater_enabled_profile", "heater_enabled", True, n_steps)
        steps = [ThermalStepInput(dt_s=sample_s, mode=modes[i], shadow_factor=shadow[i], component_power_w={"payload": payload_power[i], "eps": eps_power[i]}, heater_enabled=heater_enabled[i]) for i in range(n_steps)]
        _, _, results = simulate_thermal_profile(initialize_thermal_state(cfg), cfg, steps)
        rows: list[dict[str, Any]] = []
        for idx, r in enumerate(results):
            row = _row_base(spec, self.target_name, idx, r.time_s, r.mode)
            batt = float(r.node_temperature_k.get("battery", 0.0))
            electronics = float(r.node_temperature_k.get("electronics", 0.0))
            row.update({
                "thermal.source_native.battery_temp_k": batt,
                "thermal.source_native.electronics_temp_k": electronics,
                "thermal.source_native.payload_temp_k": float(r.node_temperature_k.get("payload", 0.0)),
                "thermal.source_native.heater_power_w": sum(float(v) for v in r.heater_power_w.values()),
                "thermal.source_native.thermal_safe_request": bool(r.thermal_safe_request),
                "label.thermal_state": "safe_request" if r.thermal_safe_request else "nominal",
                "label.health_state": "degraded" if r.thermal_safe_request else "nominal",
            })
            rows.append(row)
        qoi = {"thermal.max_battery_temp_k": max(r["thermal.source_native.battery_temp_k"] for r in rows), "thermal.max_electronics_temp_k": max(r["thermal.source_native.electronics_temp_k"] for r in rows), "thermal.safe_request_count": sum(1 for r in rows if r["thermal.source_native.thermal_safe_request"])}
        return self._result(spec, rows, qoi)


class PayloadSourceNativeAdapter(_BaseSubsystemAdapter):
    capability_id = "subsystem.payload.source_native.v1"
    target_name = "payload"
    fault_effects = ("instrument_failure",)
    degradation_effects = ("quality_or_rate_degradation",)

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues = list(super().validate(spec, capability))
        mode = str(_mapping(spec.get("target")).get("mode") or "nominal")
        if mode not in {"nominal", "fault", "degradation"}:
            issues.append(ValidationIssue("error", "$.target.mode", "payload capability supports nominal/fault/degradation", "capability"))
        for index, event in enumerate(_event_rows(spec, "fault")):
            if _effect_id(event, "fault") not in self.fault_effects:
                issues.append(ValidationIssue("error", f"$.modifiers.faults[{index}]", f"unsupported payload fault effect {_effect_id(event, 'fault')!r}", "capability_fault"))
        for index, event in enumerate(_event_rows(spec, "degradation")):
            if _effect_id(event, "degradation") not in self.degradation_effects:
                issues.append(ValidationIssue("error", f"$.modifiers.degradations[{index}]", f"unsupported payload degradation effect {_effect_id(event, 'degradation')!r}", "capability_degradation"))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from dataclasses import replace as dc_replace
        from subsystems.payload.builder import build_nominal_payload_subsystem_config
        from subsystems.payload.model import initialize_payload_state, step_payload_subsystem
        from subsystems.payload.schemas import PayloadSubsystemStepInput

        params = _mapping(spec.get("parameters"))
        _, sample_s, n_steps = _time_grid(spec)
        cfg = build_nominal_payload_subsystem_config()
        cfg = dc_replace(cfg, instrument=dc_replace(cfg.instrument, observation_power_w=_float(params.get("observation_power_w"), cfg.instrument.observation_power_w), standby_power_w=_float(params.get("standby_power_w"), cfg.instrument.standby_power_w), data_rate_bps=_float(params.get("data_rate_bps"), cfg.instrument.data_rate_bps), max_pointing_error_deg=_float(params.get("max_pointing_error_deg"), cfg.instrument.max_pointing_error_deg)))
        modes = _str_profile(params, "mode_profile", "mode", "observation", n_steps)
        pointing = _numeric_profile(params, "pointing_error_profile_deg", "pointing_error_deg", 0.05, n_steps)
        requested = _bool_profile(params, "payload_requested_profile", "payload_requested", True, n_steps)
        eps_ok = _bool_profile(params, "eps_allows_payload_profile", "eps_allows_payload", True, n_steps)
        th_ok = _bool_profile(params, "thermal_allows_payload_profile", "thermal_allows_payload", True, n_steps)
        faults = _event_rows(spec, "fault")
        degradations = _event_rows(spec, "degradation")
        mode = str(_mapping(spec.get("target")).get("mode") or "nominal")
        state = initialize_payload_state(cfg)
        rows: list[dict[str, Any]] = []
        for idx in range(n_steps):
            time_s = (idx + 1) * sample_s
            fault_active = any(_event_active(event, time_s) for event in faults)
            fraction = max((_degradation_fraction(event, time_s) for event in degradations), default=0.0)
            degradation_active = fraction > 0.0
            effective_cfg = dc_replace(
                cfg,
                instrument=dc_replace(cfg.instrument, data_rate_bps=cfg.instrument.data_rate_bps * max(0.0, 1.0 - fraction)),
            )
            step = PayloadSubsystemStepInput(
                dt_s=sample_s,
                mode=modes[idx],
                payload_requested=requested[idx] and not fault_active,
                pointing_error_deg=pointing[idx] + cfg.instrument.max_pointing_error_deg * 0.5 * fraction,
                eps_allows_payload=eps_ok[idx],
                thermal_allows_payload=th_ok[idx],
            )
            state, r = step_payload_subsystem(state, effective_cfg, step)
            row = _row_base(spec, self.target_name, idx, r.time_s, mode)
            row.update({
                "payload.source_native.enabled": bool(r.payload_enabled),
                "payload.source_native.power_w": r.payload_power_w,
                "payload.source_native.heat_w": r.payload_heat_w,
                "payload.source_native.generated_bps": r.generated_bps,
                "payload.source_native.cumulative_data_bits": r.cumulative_data_bits,
                "payload.source_native.block_reasons": ",".join(r.block_reasons),
                "label.payload_state": "enabled" if r.payload_enabled else "blocked",
                "label.fault_active": fault_active,
                "label.degradation_active": degradation_active,
                "label.health_state": "fault" if fault_active else ("degraded" if degradation_active or not r.payload_enabled else "nominal"),
            })
            rows.append(row)
        qoi = {"payload.enabled_count": sum(1 for r in rows if r["payload.source_native.enabled"]), "payload.total_data_bits": rows[-1]["payload.source_native.cumulative_data_bits"], "payload.max_power_w": max(r["payload.source_native.power_w"] for r in rows)}
        return self._result(spec, rows, qoi)


class PropulsionSourceNativeAdapter(_BaseSubsystemAdapter):
    capability_id = "subsystem.propulsion.source_native.v1"
    target_name = "propulsion"

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from dataclasses import replace as dc_replace
        from subsystems.propulsion.builder import build_nominal_propulsion_config
        from subsystems.propulsion.model import initialize_propulsion_state, simulate_propulsion_profile
        from subsystems.propulsion.schemas import PropulsionStepInput

        params = _mapping(spec.get("parameters"))
        _, sample_s, n_steps = _time_grid(spec)
        cfg = build_nominal_propulsion_config()
        cfg = dc_replace(cfg, fuel_tank=dc_replace(cfg.fuel_tank, capacity_kg=_float(params.get("fuel_capacity_kg"), cfg.fuel_tank.capacity_kg), initial_mass_kg=_float(params.get("initial_fuel_kg"), cfg.fuel_tank.initial_mass_kg)), min_soc_for_burn=_float(params.get("min_soc_for_burn"), cfg.min_soc_for_burn), require_eps_permission=_bool(params.get("require_eps_permission"), cfg.require_eps_permission))
        burn = _bool_profile(params, "burn_requested_profile", "burn_requested", True, n_steps)
        soc = _numeric_profile(params, "battery_soc_profile", "battery_soc", 1.0, n_steps)
        eps_ok = _bool_profile(params, "eps_allows_burn_profile", "eps_allows_burn", True, n_steps)
        modes = _str_profile(params, "mode_profile", "mode", "reboost", n_steps)
        steps = [PropulsionStepInput(dt_s=sample_s, burn_requested=burn[i], battery_soc=soc[i], eps_allows_burn=eps_ok[i], mode=modes[i]) for i in range(n_steps)]
        _, _, results = simulate_propulsion_profile(initialize_propulsion_state(cfg), cfg, steps)
        rows: list[dict[str, Any]] = []
        cumulative = 0.0
        for idx, r in enumerate(results):
            cumulative += r.total_impulse_ns
            row = _row_base(spec, self.target_name, idx, r.time_s, r.mode)
            row.update({
                "propulsion.source_native.burn_requested": bool(r.burn_requested),
                "propulsion.source_native.burn_allowed": bool(r.burn_allowed),
                "propulsion.source_native.inhibition_reason": r.inhibition_reason or "",
                "propulsion.source_native.total_impulse_ns": r.total_impulse_ns,
                "propulsion.source_native.cumulative_impulse_ns": cumulative,
                "propulsion.source_native.propellant_used_kg": r.propellant_used_kg,
                "propulsion.source_native.propellant_remaining_kg": r.propellant_remaining_kg,
                "propulsion.source_native.tank_pressure_pa": r.tank_pressure_pa,
                "label.propulsion_state": "burn" if r.burn_allowed else "inhibited",
                "label.health_state": "nominal" if r.burn_allowed or not r.burn_requested else "degraded",
            })
            rows.append(row)
        qoi = {"propulsion.total_impulse_ns": rows[-1]["propulsion.source_native.cumulative_impulse_ns"], "propulsion.final_propellant_kg": rows[-1]["propulsion.source_native.propellant_remaining_kg"], "propulsion.burn_allowed_count": sum(1 for r in rows if r["propulsion.source_native.burn_allowed"])}
        return self._result(spec, rows, qoi)
