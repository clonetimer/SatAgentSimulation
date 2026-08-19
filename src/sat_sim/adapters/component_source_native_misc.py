"""Source-native adapters for C2/C3 component coverage expansion.

These adapters expose lightweight pure-Python component builder/model APIs from
``src/components``.  They intentionally avoid demo ``runner.py`` files and do
not increase simulation fidelity beyond the existing source functions.
"""
from __future__ import annotations

import math
from dataclasses import asdict, is_dataclass, replace
from typing import Any, Callable, Mapping, Sequence

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


def _vec3(value: Any, default: tuple[float, float, float]) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return default
    return tuple(_float(item, default[index]) for index, item in enumerate(value))


def _list_profile(params: Mapping[str, Any], profile_key: str, scalar_key: str, default: Any, n_steps: int) -> list[Any]:
    raw = params.get(profile_key)
    if isinstance(raw, list) and raw:
        values = list(raw)
    else:
        values = [params.get(scalar_key, default)]
    if len(values) < n_steps:
        values += [values[-1]] * (n_steps - len(values))
    return values[:n_steps]


def _numeric_profile(params: Mapping[str, Any], profile_key: str, scalar_key: str, default: float, n_steps: int) -> list[float]:
    return [_float(v, default) for v in _list_profile(params, profile_key, scalar_key, default, n_steps)]


def _bool_profile(params: Mapping[str, Any], profile_key: str, scalar_key: str, default: bool, n_steps: int) -> list[bool]:
    return [_bool(v, default) for v in _list_profile(params, profile_key, scalar_key, default, n_steps)]


def _time_grid(spec: Mapping[str, Any]) -> tuple[float, float, int, list[float]]:
    sim = _mapping(spec.get("simulation"))
    duration_s = _float(sim.get("duration_s"), 300.0)
    sample_s = _float(sim.get("sample_s"), 10.0)
    n_steps = max(1, int(math.ceil(duration_s / sample_s)))
    return duration_s, sample_s, n_steps, [round(i * sample_s, 12) for i in range(n_steps + 1)]


def _row_base(spec: Mapping[str, Any], target_name: str, idx: int, time_s: float, mode: str) -> dict[str, Any]:
    metadata = _mapping(spec.get("metadata"))
    return {
        "task_id": str(spec.get("task_id", f"component_{target_name}_task")),
        "case_id": str(metadata.get("case_id", "case_000")),
        "time_s": time_s,
        "sample_index": idx,
        "target_level": "component",
        "target_name": target_name,
        "mode": mode,
    }


def _dataclass_dict(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    return {name: getattr(value, name) for name in dir(value) if not name.startswith("_") and not callable(getattr(value, name))}


def _normalized_events(spec: Mapping[str, Any], key: str, type_key: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    top = spec.get(key)
    if isinstance(top, list):
        out.extend(dict(item) for item in top if isinstance(item, Mapping))
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
    start_s = _float(event.get("start_s", event.get("onset_time_s", 0.0)), 0.0)
    end_raw = event.get("end_s")
    if end_raw is None and event.get("duration_s") is not None:
        duration = _float(event.get("duration_s"), -1.0)
        end_raw = None if duration < 0 else start_s + duration
    return time_s >= start_s and (end_raw is None or time_s <= _float(end_raw, time_s))


def _event_params(event: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(event.get("parameters"))


def _event_name(event: Mapping[str, Any], kind: str) -> str:
    return str(event.get(f"{kind}_type") or event.get("effect") or event.get("modifier_type") or event.get("type") or "")


def _event_severity(event: Mapping[str, Any], default: float = 0.5) -> float:
    params = _event_params(event)
    return min(1.0, max(0.0, _float(params.get("severity", event.get("severity", event.get("magnitude"))), default)))


def _degradation_fraction(event: Mapping[str, Any], time_s: float) -> float:
    if not _event_active(event, time_s):
        return 0.0
    params = _event_params(event)
    start_s = _float(event.get("start_s", event.get("onset_time_s", 0.0)), 0.0)
    explicit = params.get("loss_fraction", params.get("degradation_fraction"))
    if explicit is not None:
        return min(1.0, max(0.0, _float(explicit, 0.0)))
    rate = max(0.0, _float(params.get("rate_per_s"), 0.01))
    maximum = min(1.0, max(0.0, _float(params.get("max_fraction"), _event_severity(event, 0.5))))
    return min(maximum, rate * max(0.0, time_s - start_s))


class _BaseComponentAdapter:
    capability_id: str = ""
    target_name: str = ""
    supported_mode: str = "nominal"
    supported_modes: tuple[str, ...] = ("nominal",)
    fault_effects: tuple[str, ...] = ()
    degradation_effects: tuple[str, ...] = ()

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if spec.get("capability_id") != self.capability_id:
            issues.append(ValidationIssue("error", "$.capability_id", f"must be {self.capability_id!r}", "capability"))
        if spec.get("task_type") != "component":
            issues.append(ValidationIssue("error", "$.task_type", "component capability requires task_type='component'", "capability"))
        target = _mapping(spec.get("target"))
        if target.get("level") != "component" or target.get("name") != self.target_name:
            issues.append(ValidationIssue("error", "$.target", f"requires target.level='component' and target.name='{self.target_name}'", "capability"))
        mode = str(target.get("mode") or self.supported_mode)
        if mode not in self.supported_modes:
            issues.append(ValidationIssue("error", "$.target.mode", f"{self.capability_id} supports {self.supported_modes!r}", "capability"))
        for index, event in enumerate(_normalized_events(spec, "faults", "fault_type")):
            effect = _event_name(event, "fault")
            if effect not in self.fault_effects:
                issues.append(ValidationIssue("error", f"$.faults[{index}]", f"unsupported fault effect {effect!r}", "capability_fault"))
        for index, event in enumerate(_normalized_events(spec, "degradations", "degradation_type")):
            effect = _event_name(event, "degradation")
            if effect not in self.degradation_effects:
                issues.append(ValidationIssue("error", f"$.degradations[{index}]", f"unsupported degradation effect {effect!r}", "capability_degradation"))
        sim = _mapping(spec.get("simulation"))
        for key in ("duration_s", "sample_s"):
            if key in sim and (isinstance(sim[key], bool) or not isinstance(sim[key], (int, float)) or float(sim[key]) <= 0):
                issues.append(ValidationIssue("error", f"$.simulation.{key}", "must be a positive number", "range"))
        for issue in self._validate_parameters(_mapping(spec.get("parameters"))):
            issues.append(issue)
        return tuple(issues)

    def _validate_parameters(self, params: Mapping[str, Any]) -> Sequence[ValidationIssue]:
        return ()

    def _summary(self, spec: Mapping[str, Any], rows: list[dict[str, Any]], qoi: dict[str, Any], events: dict[str, Any] | None = None) -> dict[str, Any]:
        duration_s, sample_s, _, _ = _time_grid(spec)
        return {
            "task_id": str(spec.get("task_id", f"component_{self.target_name}_task")),
            "case_id": str(_mapping(spec.get("metadata")).get("case_id", "case_000")),
            "status": "complete",
            "duration_s": duration_s,
            "sample_s": sample_s,
            "target_level": "component",
            "target_name": self.target_name,
            "capability_id": self.capability_id,
            "mode": str(_mapping(spec.get("target")).get("mode") or "nominal"),
            "qoi": qoi,
            "events": events or {},
            "trace_rows": len(rows),
        }

    def _result(self, spec: Mapping[str, Any], rows: list[dict[str, Any]], qoi: dict[str, Any], events: dict[str, Any] | None = None) -> SimulationResult:
        summary = self._summary(spec, rows, qoi, events)
        labels = {"run_labels": [{"task_id": summary["task_id"], "mode": summary["mode"], "capability_id": self.capability_id}]}
        return SimulationResult(summary=summary, trace_rows=tuple(rows), labels=labels, metadata={"capability_id": self.capability_id})

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        raise NotImplementedError("S6 central script exporter should be used for capability-python generation")

    def output_schema(self, capability: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return {"capability_id": self.capability_id, "target": self.target_name}


class GroundStationAdapter(_BaseComponentAdapter):
    capability_id = "component.ground_station.v1"
    target_name = "ground_station"
    supported_modes = ("nominal", "fault", "degradation")
    fault_effects = ("tracking_loss", "weather_fade", "receiver_outage")
    degradation_effects = ("gover_tdrift", "pointing_accuracy_drift")

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from components.ground_station.builder import build_nominal_ground_station_config, compute_ground_access

        params = _mapping(spec.get("parameters"))
        duration_s, sample_s, n_steps, times = _time_grid(spec)
        cfg = build_nominal_ground_station_config(
            min_elevation_deg=_float(params.get("min_elevation_deg"), 5.0),
            max_range_m=_float(params.get("max_range_m"), 2_000_000.0),
        )
        lat = math.radians(_float(params.get("ground_lat_deg"), 0.0))
        lon = math.radians(_float(params.get("ground_lon_deg"), 0.0))
        radius = _float(params.get("planet_radius_m"), 6_371_000.0) + _float(params.get("ground_alt_m"), 0.0)
        station = (radius * math.cos(lat) * math.cos(lon), radius * math.cos(lat) * math.sin(lon), radius * math.sin(lat))
        sc_radius = _float(params.get("spacecraft_radius_m"), _float(params.get("orbit_radius_m"), 6_871_000.0))
        phase0 = math.radians(_float(params.get("phase0_deg"), 0.0))
        omega = _float(params.get("angular_rate_rad_s"), math.sqrt(3.986004418e14 / max(sc_radius, 1.0) ** 3))
        faults = _normalized_events(spec, "faults", "fault_type")
        degradations = _normalized_events(spec, "degradations", "degradation_type")
        mode = str(_mapping(spec.get("target")).get("mode") or "nominal")
        rows = []
        for i, t in enumerate(times):
            theta = phase0 + omega * t
            sc = (sc_radius * math.cos(theta), sc_radius * math.sin(theta), 0.0)
            effective_min_elevation = cfg.min_elevation_deg
            effective_max_range = cfg.max_range_m
            forced_outage = False
            fault_active = False
            degradation_active = False
            for event in faults:
                if not _event_active(event, t):
                    continue
                fault_active = True
                name = _event_name(event, "fault")
                severity = _event_severity(event)
                if name in {"tracking_loss", "receiver_outage"}:
                    forced_outage = True
                elif name == "weather_fade":
                    effective_max_range *= max(0.05, 1.0 - 0.9 * severity)
            for event in degradations:
                fraction = _degradation_fraction(event, t)
                if fraction <= 0.0:
                    continue
                degradation_active = True
                if _event_name(event, "degradation") == "gover_tdrift":
                    effective_max_range *= max(0.1, 1.0 - 0.8 * fraction)
                else:
                    effective_min_elevation += 30.0 * fraction
            effective_cfg = replace(cfg, min_elevation_deg=effective_min_elevation, max_range_m=effective_max_range)
            result = compute_ground_access(station, sc, effective_cfg)
            has_access = bool(result.has_access and not forced_outage)
            row = _row_base(spec, self.target_name, i, t, mode)
            row.update({
                "ground_station.has_access": has_access,
                "ground_station.elevation_deg": float(result.elevation_deg),
                "ground_station.slant_range_m": float(result.slant_range_m),
                "ground_station.min_elevation_deg": float(effective_cfg.min_elevation_deg),
                "ground_station.max_range_m": float(effective_cfg.max_range_m),
                "label.access_state": "in_view" if has_access else "out_of_view",
                "label.fault_active": fault_active,
                "label.degradation_active": degradation_active,
                "label.health_state": "fault" if fault_active else ("degraded" if degradation_active else "nominal"),
            })
            rows.append(row)
        qoi = {
            "ground_station.access_count": sum(1 for r in rows if r["ground_station.has_access"]),
            "ground_station.max_elevation_deg": max(float(r["ground_station.elevation_deg"]) for r in rows),
            "ground_station.min_slant_range_m": min(float(r["ground_station.slant_range_m"]) for r in rows),
        }
        return self._result(spec, rows, qoi, {"access_sample_count": qoi["ground_station.access_count"]})


class AntennaAdapter(_BaseComponentAdapter):
    capability_id = "component.antenna.v1"
    target_name = "antenna"
    supported_modes = ("nominal", "fault", "degradation")
    fault_effects = ("pointing_loss", "feed_open", "gain_drop")
    degradation_effects = ("surface_contamination", "pointing_bias_drift")

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from components.antenna.builder import build_nominal_antenna_config, compute_antenna_gain

        params = _mapping(spec.get("parameters"))
        _, _, n_steps, times = _time_grid(spec)
        cfg = build_nominal_antenna_config(
            peak_gain_dbi=_float(params.get("peak_gain_dbi"), 8.0),
            half_power_beamwidth_deg=_float(params.get("half_power_beamwidth_deg"), 30.0),
            max_pointing_loss_db=_float(params.get("max_pointing_loss_db"), 18.0),
        )
        off = _numeric_profile(params, "off_boresight_profile_deg", "off_boresight_deg", 0.0, n_steps)
        faults = _normalized_events(spec, "faults", "fault_type")
        degradations = _normalized_events(spec, "degradations", "degradation_type")
        mode = str(_mapping(spec.get("target")).get("mode") or "nominal")
        rows = []
        for i, t in enumerate(times):
            idx = min(max(i - 1, 0), n_steps - 1)
            effective_off = off[idx]
            peak_gain = cfg.peak_gain_dbi
            fault_active = False
            degradation_active = False
            for event in faults:
                if not _event_active(event, t):
                    continue
                fault_active = True
                name = _event_name(event, "fault")
                severity = _event_severity(event)
                if name == "pointing_loss":
                    effective_off += cfg.half_power_beamwidth_deg * (1.0 + severity)
                elif name == "feed_open":
                    peak_gain -= 80.0
                else:
                    peak_gain -= 20.0 * severity
            for event in degradations:
                fraction = _degradation_fraction(event, t)
                if fraction <= 0.0:
                    continue
                degradation_active = True
                if _event_name(event, "degradation") == "surface_contamination":
                    peak_gain -= 12.0 * fraction
                else:
                    effective_off += cfg.half_power_beamwidth_deg * fraction
            effective_cfg = replace(cfg, peak_gain_dbi=peak_gain)
            result = compute_antenna_gain(effective_off, effective_cfg)
            row = _row_base(spec, self.target_name, i, t, mode)
            row.update({
                "comm.antenna.off_boresight_deg": float(effective_off),
                "comm.antenna.effective_peak_gain_dbi": float(peak_gain),
                "comm.antenna.gain_dbi": float(result.antenna_gain_dbi),
                "comm.antenna.pointing_loss_db": float(result.pointing_loss_db),
                "comm.antenna.boresight_ok": bool(result.boresight_ok),
                "label.antenna_state": "boresight_ok" if result.boresight_ok else "pointing_loss_high",
                "label.fault_active": fault_active,
                "label.degradation_active": degradation_active,
                "label.health_state": "fault" if fault_active else ("degraded" if degradation_active or result.limit_violation else "nominal"),
            })
            rows.append(row)
        qoi = {"comm.antenna.min_gain_dbi": min(float(r["comm.antenna.gain_dbi"]) for r in rows), "comm.antenna.max_pointing_loss_db": max(float(r["comm.antenna.pointing_loss_db"]) for r in rows)}
        return self._result(spec, rows, qoi)


class TransmitterAdapter(_BaseComponentAdapter):
    capability_id = "component.transmitter.v1"
    target_name = "transmitter"
    supported_modes = ("nominal", "fault", "degradation")
    fault_effects = ("power_amplifier_fault", "frequency_drift_fault", "signal_loss")
    degradation_effects = ("output_power_decay", "frequency_stability_drift")

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from components.transmitter.builder import TransmitterConfig, compute_transmitter

        params = _mapping(spec.get("parameters"))
        _, _, n_steps, times = _time_grid(spec)
        cfg = TransmitterConfig(
            max_tx_power_w=_float(params.get("max_tx_power_w"), 12.0),
            standby_power_w=_float(params.get("standby_power_w"), 1.5),
            efficiency=_float(params.get("efficiency"), 0.55),
            max_rate_bps=_float(params.get("max_rate_bps"), 1_000_000.0),
            amp_type=str(params.get("amp_type", "sspa")),
            gain_dB=_float(params.get("gain_db", params.get("gain_dB", 30.0)), 30.0),
            input_power_w=_float(params.get("input_power_w"), 0.001),
            min_input_power_w=_float(params.get("min_input_power_w"), 1e-6),
            max_input_power_w=_float(params.get("max_input_power_w"), 0.1),
        )
        commanded = _bool_profile(params, "commanded_on_profile", "commanded_on", True, n_steps)
        requested = _numeric_profile(params, "requested_rate_profile_bps", "requested_rate_bps", 500_000.0, n_steps)
        available = _numeric_profile(params, "available_power_profile_w", "available_power_w", 20.0, n_steps)
        faults = _normalized_events(spec, "faults", "fault_type")
        degradations = _normalized_events(spec, "degradations", "degradation_type")
        mode = str(_mapping(spec.get("target")).get("mode") or "nominal")
        rows = []
        for i, t in enumerate(times):
            idx = min(max(i - 1, 0), n_steps - 1)
            effective_command = commanded[idx]
            power_factor = 1.0
            rate_factor = 1.0
            fault_active = False
            degradation_active = False
            for event in faults:
                if not _event_active(event, t):
                    continue
                fault_active = True
                name = _event_name(event, "fault")
                severity = _event_severity(event)
                if name == "signal_loss":
                    effective_command = False
                elif name == "power_amplifier_fault":
                    power_factor *= max(0.0, 1.0 - severity)
                else:
                    rate_factor *= max(0.0, 1.0 - 0.9 * severity)
            for event in degradations:
                fraction = _degradation_fraction(event, t)
                if fraction <= 0.0:
                    continue
                degradation_active = True
                if _event_name(event, "degradation") == "output_power_decay":
                    power_factor *= max(0.05, 1.0 - fraction)
                else:
                    rate_factor *= max(0.05, 1.0 - fraction)
            effective_cfg = replace(
                cfg,
                max_tx_power_w=cfg.max_tx_power_w * power_factor,
                max_rate_bps=cfg.max_rate_bps * rate_factor,
                input_power_w=cfg.input_power_w * power_factor,
            )
            result = compute_transmitter(effective_command, requested[idx], available[idx], effective_cfg)
            row = _row_base(spec, self.target_name, i, t, mode)
            row.update({
                "comm.transmitter.commanded_on": effective_command,
                "comm.transmitter.enabled": bool(result.transmitter_enabled),
                "comm.transmitter.tx_power_w": float(result.tx_power_w),
                "comm.transmitter.power_draw_w": float(result.power_draw_w),
                "comm.transmitter.effective_rate_bps": float(result.effective_rate_bps),
                "comm.transmitter.requested_rate_bps": float(requested[idx]),
                "comm.transmitter.available_power_w": float(available[idx]),
                "comm.transmitter.effective_max_tx_power_w": float(effective_cfg.max_tx_power_w),
                "comm.transmitter.effective_max_rate_bps": float(effective_cfg.max_rate_bps),
                "comm.transmitter.efficiency": float(result.efficiency),
                "label.transmitter_state": "enabled" if result.transmitter_enabled else "off_or_power_limited",
                "label.fault_active": fault_active,
                "label.degradation_active": degradation_active,
                "label.health_state": "fault" if fault_active else ("degraded" if degradation_active or result.limit_violation else "nominal"),
            })
            rows.append(row)
        qoi = {"comm.transmitter.max_effective_rate_bps": max(float(r["comm.transmitter.effective_rate_bps"]) for r in rows), "comm.transmitter.max_power_draw_w": max(float(r["comm.transmitter.power_draw_w"]) for r in rows)}
        return self._result(spec, rows, qoi)


class LinkBudgetAdapter(_BaseComponentAdapter):
    capability_id = "component.link_budget.v1"
    target_name = "link_budget"
    supported_modes = ("nominal", "fault", "degradation")
    fault_effects = ("jamming", "fade", "antenna_mispointing")
    degradation_effects = ("margin_erosion", "noise_figure_growth")

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from components.link_budget.builder import build_nominal_link_budget_config, compute_link_budget

        params = _mapping(spec.get("parameters"))
        _, _, n_steps, times = _time_grid(spec)
        base_cfg = build_nominal_link_budget_config(
            raw_rate_bps=_float(params.get("raw_rate_bps"), 1000.0),
            tx_power_w=_float(params.get("tx_power_w"), 1.0),
            tx_gain_dbi=_float(params.get("tx_gain_dbi"), 0.0),
            rx_gain_dbi=_float(params.get("rx_gain_dbi"), 0.0),
            misc_loss_db=_float(params.get("misc_loss_db"), 0.0),
            freq_hz=_float(params.get("freq_hz"), 2.2e9),
            noise_temp_k=_float(params.get("noise_temp_k"), 500.0),
            downlink_eff=_float(params.get("downlink_eff"), 1.0),
        )
        ranges = _numeric_profile(params, "slant_range_profile_m", "slant_range_m", 1_000_000.0, n_steps)
        faults = _normalized_events(spec, "faults", "fault_type")
        degradations = _normalized_events(spec, "degradations", "degradation_type")
        rows = []
        for i, t in enumerate(times):
            idx = min(max(i - 1, 0), n_steps - 1)
            loss_db = float(base_cfg.misc_loss_db)
            noise_temp_k = float(base_cfg.noise_temp_k)
            fault_active = False
            degradation_active = False
            for event in faults:
                if not _event_active(event, t):
                    continue
                fault_active = True
                severity = _event_severity(event)
                effect = _event_name(event, "fault")
                loss_db += {"jamming": 30.0, "fade": 18.0, "antenna_mispointing": 12.0}[effect] * severity
            for event in degradations:
                fraction = _degradation_fraction(event, t)
                if fraction <= 0.0:
                    continue
                degradation_active = True
                effect = _event_name(event, "degradation")
                if effect == "margin_erosion":
                    loss_db += 20.0 * fraction
                else:
                    noise_temp_k *= 1.0 + 3.0 * fraction
            cfg = replace(base_cfg, misc_loss_db=loss_db, noise_temp_k=noise_temp_k)
            result = compute_link_budget(cfg, ranges[idx])
            mode = str(_mapping(spec.get("target")).get("mode") or "nominal")
            row = _row_base(spec, self.target_name, i, t, mode)
            row.update({
                "comm.link_budget.slant_range_m": float(ranges[idx]),
                "comm.link_budget.effective_rate_bps": float(result.effective_rate_bps),
                "comm.link_budget.ebn0_db": float(result.ebn0_db),
                "comm.link_budget.fspl_db": float(result.fspl_db),
                "comm.link_budget.ber": float(result.ber),
                "comm.link_budget.effective_misc_loss_db": loss_db,
                "comm.link_budget.effective_noise_temp_k": noise_temp_k,
                "label.link_state": "usable" if result.effective_rate_bps > 0 else "closed",
                "label.fault_active": fault_active,
                "label.degradation_active": degradation_active,
                "label.health_state": "fault" if fault_active else ("degraded" if degradation_active or result.ber > 0.1 else "nominal"),
            })
            rows.append(row)
        qoi = {
            "comm.link_budget.min_effective_rate_bps": min(float(r["comm.link_budget.effective_rate_bps"]) for r in rows),
            "comm.link_budget.max_ebn0_db": max(float(r["comm.link_budget.ebn0_db"]) for r in rows),
            "comm.link_budget.max_effective_misc_loss_db": max(float(r["comm.link_budget.effective_misc_loss_db"]) for r in rows),
        }
        return self._result(spec, rows, qoi)


class PowerSinkAdapter(_BaseComponentAdapter):
    capability_id = "component.power_sink.v1"
    target_name = "power_sink"
    supported_modes = ("nominal", "fault", "degradation")
    fault_effects = ("overload", "intermittent_load", "open_load")
    degradation_effects = ("load_drift", "efficiency_decay")

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from components.power_sink.builder import PowerSinkState, build_nominal_power_sink_config, demand_w

        params = _mapping(spec.get("parameters"))
        _, _, n_steps, times = _time_grid(spec)
        cfg = build_nominal_power_sink_config(name=str(params.get("name", "load")), base_w=_float(params.get("base_w", params.get("nominal_power_w", 10.0)), 10.0))
        enabled = _bool_profile(params, "enabled_profile", "enabled", True, n_steps)
        modes = _list_profile(params, "mode_profile", "mode", "", n_steps)
        faults = _normalized_events(spec, "faults", "fault_type")
        degradations = _normalized_events(spec, "degradations", "degradation_type")
        rows = []
        for i, t in enumerate(times):
            idx = min(max(i - 1, 0), n_steps - 1)
            state = PowerSinkState(power_w=float(cfg.base_w), mode=str(modes[idx]))
            power = float(demand_w(cfg, mode=state.mode, enabled=enabled[idx]))
            fault_active = False
            degradation_active = False
            for event in faults:
                if not _event_active(event, t):
                    continue
                fault_active = True
                effect = _event_name(event, "fault")
                severity = _event_severity(event)
                if effect == "overload":
                    power *= 1.0 + 2.0 * severity
                elif effect == "open_load":
                    power = 0.0
                elif effect == "intermittent_load" and i % 2:
                    power = 0.0
            for event in degradations:
                fraction = _degradation_fraction(event, t)
                if fraction <= 0.0:
                    continue
                degradation_active = True
                power *= 1.0 + (1.0 if _event_name(event, "degradation") == "load_drift" else 0.6) * fraction
            mode = str(_mapping(spec.get("target")).get("mode") or "nominal")
            row = _row_base(spec, self.target_name, i, t, mode)
            row.update({
                "eps.power_sink.enabled": enabled[idx],
                "eps.power_sink.nominal_demand_w": float(demand_w(cfg, mode=state.mode, enabled=enabled[idx])),
                "eps.power_sink.demand_w": power,
                "label.power_sink_state": "enabled" if enabled[idx] else "disabled",
                "label.fault_active": fault_active,
                "label.degradation_active": degradation_active,
                "label.health_state": "fault" if fault_active else ("degraded" if degradation_active else "nominal"),
            })
            rows.append(row)
        qoi = {"eps.power_sink.max_demand_w": max(float(r["eps.power_sink.demand_w"]) for r in rows), "eps.power_sink.mean_demand_w": sum(float(r["eps.power_sink.demand_w"]) for r in rows) / len(rows)}
        return self._result(spec, rows, qoi)


class HeaterAdapter(_BaseComponentAdapter):
    capability_id = "component.heater.v1"
    target_name = "heater"
    supported_modes = ("nominal", "fault", "degradation")
    fault_effects = ("stuck_off", "stuck_on", "relay_chatter")
    degradation_effects = ("heating_efficiency_decay", "resistance_drift")

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from components.heater.builder import build_nominal_heater_config, step_heater

        params = _mapping(spec.get("parameters"))
        _, _, n_steps, times = _time_grid(spec)
        base_cfg = build_nominal_heater_config(max_power_w=_float(params.get("max_power_w"), 25.0), setpoint_k=_float(params.get("setpoint_k"), 285.0), hysteresis_k=_float(params.get("hysteresis_k"), 2.0))
        temps = _numeric_profile(params, "node_temp_profile_k", "node_temp_k", 280.0, n_steps)
        faults = _normalized_events(spec, "faults", "fault_type")
        degradations = _normalized_events(spec, "degradations", "degradation_type")
        rows = []
        for i, t in enumerate(times):
            idx = min(max(i - 1, 0), n_steps - 1)
            effective_power = float(base_cfg.max_power_w)
            forced_state: bool | None = None
            fault_active = False
            degradation_active = False
            for event in faults:
                if not _event_active(event, t):
                    continue
                fault_active = True
                effect = _event_name(event, "fault")
                if effect == "stuck_off":
                    forced_state = False
                    effective_power = 0.0
                elif effect == "stuck_on":
                    forced_state = True
                else:
                    effective_power *= 0.55
            for event in degradations:
                fraction = _degradation_fraction(event, t)
                if fraction <= 0.0:
                    continue
                degradation_active = True
                effective_power *= 1.0 - (0.7 if _event_name(event, "degradation") == "heating_efficiency_decay" else 0.4) * fraction
            cfg = replace(base_cfg, max_power_w=max(0.0, effective_power))
            result = step_heater(temps[idx], cfg)
            heater_on = bool(result.heater_on if forced_state is None else forced_state)
            power_w = cfg.max_power_w if heater_on else 0.0
            mode = str(_mapping(spec.get("target")).get("mode") or "nominal")
            row = _row_base(spec, self.target_name, i, t, mode)
            row.update({
                "thermal.heater.node_temp_k": temps[idx],
                "thermal.heater.heater_on": heater_on,
                "thermal.heater.power_w": float(power_w),
                "thermal.heater.effective_max_power_w": float(cfg.max_power_w),
                "thermal.heater.temperature_error_k": float(result.temperature_error_k),
                "label.heater_state": "on" if heater_on else "off",
                "label.fault_active": fault_active,
                "label.degradation_active": degradation_active,
                "label.health_state": "fault" if fault_active else ("degraded" if degradation_active else "nominal"),
            })
            rows.append(row)
        qoi = {"thermal.heater.on_sample_count": sum(1 for r in rows if r["thermal.heater.heater_on"]), "thermal.heater.max_power_w": max(float(r["thermal.heater.power_w"]) for r in rows)}
        return self._result(spec, rows, qoi)


class RadiatorAdapter(_BaseComponentAdapter):
    capability_id = "component.radiator.v1"
    target_name = "radiator"
    supported_modes = ("nominal", "fault", "degradation")
    fault_effects = ("radiator_rejection_loss", "radiator_surface_contamination")
    degradation_effects = ("radiator_emissivity_decay", "radiator_area_degradation")

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues = list(super().validate({**dict(spec), "target": {**dict(_mapping(spec.get("target"))), "mode": "nominal"}}, capability))
        mode = str(_mapping(spec.get("target")).get("mode") or "nominal")
        if mode not in {"nominal", "fault", "degradation"}:
            issues.append(ValidationIssue("error", "$.target.mode", "radiator supports nominal, fault or degradation mode", "capability"))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from components.radiator.builder import build_nominal_radiator_config, compute_radiator_rejection

        params = _mapping(spec.get("parameters"))
        duration_s, sample_s, n_steps, times = _time_grid(spec)
        base_cfg = build_nominal_radiator_config(
            area_m2=_float(params.get("area_m2"), 0.35),
            emissivity=_float(params.get("emissivity"), 0.82),
            effective_sink_temp_k=_float(params.get("effective_sink_temp_k"), 250.0),
            max_rejection_w=_float(params.get("max_rejection_w"), 180.0),
        )
        temps = _numeric_profile(params, "node_temp_profile_k", "node_temp_k", 300.0, n_steps)
        heat = _numeric_profile(params, "heat_load_profile_w", "heat_load_w", 25.0, n_steps)
        faults = _normalized_events(spec, "faults", "fault_type")
        degradations = _normalized_events(spec, "degradations", "degradation_type")
        rows: list[dict[str, Any]] = []
        any_fault = False
        any_degradation = False
        for i, t in enumerate(times):
            idx = min(max(i - 1, 0), n_steps - 1)
            area = float(base_cfg.area_m2)
            emissivity = float(base_cfg.emissivity)
            max_rejection = float(base_cfg.max_rejection_w)
            fault_active = False
            degradation_active = False
            for event in faults:
                if not _event_active(event, t):
                    continue
                fault_active = True
                any_fault = True
                effect = str(event.get("fault_type") or event.get("effect") or "")
                ep = _event_params(event)
                if effect == "radiator_rejection_loss":
                    max_rejection *= min(max(_float(ep.get("remaining_capacity_ratio"), 0.5), 0.0), 1.0)
                elif effect == "radiator_surface_contamination":
                    emissivity *= 1.0 - min(max(_float(ep.get("emissivity_loss_ratio"), 0.2), 0.0), 1.0)
            for event in degradations:
                if not _event_active(event, t):
                    continue
                degradation_active = True
                any_degradation = True
                effect = str(event.get("degradation_type") or event.get("effect") or "")
                ep = _event_params(event)
                if effect == "radiator_emissivity_decay":
                    emissivity *= 1.0 - min(max(_float(ep.get("emissivity_loss_ratio"), 0.1), 0.0), 1.0)
                elif effect == "radiator_area_degradation":
                    area *= 1.0 - min(max(_float(ep.get("area_loss_ratio"), 0.2), 0.0), 1.0)
            cfg = replace(base_cfg, area_m2=max(area, 0.0), emissivity=max(emissivity, 0.0), max_rejection_w=max(max_rejection, 0.0))
            result = compute_radiator_rejection(temps[idx], heat[idx], cfg)
            row = _row_base(spec, self.target_name, i, t, str(_mapping(spec.get("target")).get("mode") or "nominal"))
            row.update({
                "thermal.radiator.node_temp_k": temps[idx],
                "thermal.radiator.heat_load_w": heat[idx],
                "thermal.radiator.rejected_heat_w": float(result.rejected_heat_w),
                "thermal.radiator.margin_w": float(result.radiator_margin_w),
                "thermal.radiator.effective_emissivity": float(cfg.emissivity),
                "thermal.radiator.effective_area_m2": float(cfg.area_m2),
                "thermal.radiator.effective_max_rejection_w": float(cfg.max_rejection_w),
                "label.radiator_state": "under_capacity" if result.limit_violation else "nominal",
                "label.fault_active": fault_active,
                "label.degradation_active": degradation_active,
                "label.health_state": "fault" if fault_active else ("degraded" if degradation_active or result.limit_violation else "nominal"),
            })
            rows.append(row)
        qoi = {
            "thermal.radiator.max_rejected_heat_w": max(float(r["thermal.radiator.rejected_heat_w"]) for r in rows),
            "thermal.radiator.min_margin_w": min(float(r["thermal.radiator.margin_w"]) for r in rows),
            "thermal.radiator.min_effective_emissivity": min(float(r["thermal.radiator.effective_emissivity"]) for r in rows),
            "thermal.radiator.min_effective_area_m2": min(float(r["thermal.radiator.effective_area_m2"]) for r in rows),
        }
        mode = str(_mapping(spec.get("target")).get("mode") or "nominal")
        summary = {
            "task_id": str(spec.get("task_id", "component_radiator_task")),
            "case_id": str(_mapping(spec.get("metadata")).get("case_id", "case_000")),
            "status": "complete", "duration_s": duration_s, "sample_s": sample_s,
            "target_level": "component", "target_name": "radiator", "capability_id": self.capability_id, "mode": mode,
            "qoi": qoi, "trace_rows": len(rows),
            "events": {
                "fault_registered": bool(faults), "degradation_registered": bool(degradations),
                "fault_effect_observed": any_fault, "degradation_effect_observed": any_degradation,
            },
        }
        labels = {"run_labels": [{"task_id": summary["task_id"], "mode": mode, "capability_id": self.capability_id}]}
        return SimulationResult(summary=summary, trace_rows=tuple(rows), labels=labels, metadata={"capability_id": self.capability_id})


class ThermalNodeAdapter(_BaseComponentAdapter):
    capability_id = "component.thermal_node.v1"
    target_name = "thermal_node"
    supported_modes = ("nominal", "fault", "degradation")
    fault_effects = ("thermal_runaway", "sensor_bias", "blocked_heat_path")
    degradation_effects = ("thermal_resistance_growth", "heat_capacity_drift")

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from components.thermal_node.builder import ThermalNodeState, build_nominal_thermal_node_config, step_thermal_node

        params = _mapping(spec.get("parameters"))
        _, sample_s, n_steps, times = _time_grid(spec)
        base_cfg = build_nominal_thermal_node_config(ambient_k=_float(params.get("ambient_k"), 300.0), tau_s=_float(params.get("tau_s"), 10.0), heat_gain_k_per_w=_float(params.get("heat_gain_k_per_w"), 1.0), min_temp_k=_float(params.get("min_temp_k"), 0.0), max_temp_k=_float(params.get("max_temp_k"), 1000.0))
        state = ThermalNodeState(temp_k=_float(params.get("initial_temp_k"), base_cfg.ambient_k))
        power = _numeric_profile(params, "power_profile_w", "power_w", 10.0, n_steps)
        faults = _normalized_events(spec, "faults", "fault_type")
        degradations = _normalized_events(spec, "degradations", "degradation_type")
        rows = []
        for i, t in enumerate(times):
            idx = min(max(i - 1, 0), n_steps - 1)
            effective_power = power[idx]
            tau_s = float(base_cfg.tau_s)
            heat_gain = float(base_cfg.heat_gain_k_per_w)
            sensor_bias_k = 0.0
            fault_active = False
            degradation_active = False
            for event in faults:
                if not _event_active(event, t):
                    continue
                fault_active = True
                effect = _event_name(event, "fault")
                severity = _event_severity(event)
                if effect == "thermal_runaway":
                    effective_power *= 1.0 + 4.0 * severity
                elif effect == "sensor_bias":
                    sensor_bias_k += 20.0 * severity
                else:
                    tau_s *= 1.0 + 8.0 * severity
                    heat_gain *= 1.0 + 2.0 * severity
            for event in degradations:
                fraction = _degradation_fraction(event, t)
                if fraction <= 0.0:
                    continue
                degradation_active = True
                if _event_name(event, "degradation") == "thermal_resistance_growth":
                    heat_gain *= 1.0 + 2.0 * fraction
                    tau_s *= 1.0 + fraction
                else:
                    tau_s *= 1.0 + 3.0 * fraction
            cfg = replace(base_cfg, tau_s=tau_s, heat_gain_k_per_w=heat_gain)
            if i:
                state = step_thermal_node(state, cfg, effective_power, sample_s)
            measured_temp = state.temp_k + sensor_bias_k
            mode = str(_mapping(spec.get("target")).get("mode") or "nominal")
            row = _row_base(spec, self.target_name, i, float(t), mode)
            row.update({
                "thermal.node.power_w": effective_power,
                "thermal.node.temp_k": float(state.temp_k),
                "thermal.node.measured_temp_k": float(measured_temp),
                "thermal.node.temp_c": float(state.temp_k) - 273.15,
                "thermal.node.effective_tau_s": tau_s,
                "thermal.node.effective_heat_gain_k_per_w": heat_gain,
                "label.thermal_node_state": "hot" if state.temp_k >= cfg.max_temp_k else "nominal",
                "label.fault_active": fault_active,
                "label.degradation_active": degradation_active,
                "label.health_state": "fault" if fault_active else ("degraded" if degradation_active or state.temp_k >= cfg.max_temp_k else "nominal"),
            })
            rows.append(row)
        qoi = {"thermal.node.initial_temp_k": float(rows[0]["thermal.node.temp_k"]), "thermal.node.final_temp_k": float(rows[-1]["thermal.node.temp_k"]), "thermal.node.max_temp_k": max(float(row["thermal.node.temp_k"]) for row in rows)}
        return self._result(spec, rows, qoi)


class PayloadAdapter(_BaseComponentAdapter):
    capability_id = "component.payload.v1"
    target_name = "payload"
    supported_modes = ("nominal", "fault", "degradation")
    fault_effects = ("instrument_off", "saturation", "pointing_limit")
    degradation_effects = ("sensitivity_decay", "dark_current_growth")

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from components.payload.builder import PayloadInstrumentState, PayloadInstrumentStepInput, build_nominal_payload_config, step_payload_instrument

        params = _mapping(spec.get("parameters"))
        _, sample_s, n_steps, times = _time_grid(spec)
        cfg = build_nominal_payload_config(name=str(params.get("name", "nadir_imager")), observation_power_w=_float(params.get("observation_power_w"), 18.0), standby_power_w=_float(params.get("standby_power_w"), 3.0), data_rate_bps=_float(params.get("data_rate_bps"), 250_000.0), heat_fraction=_float(params.get("heat_fraction"), 0.85), max_pointing_error_deg=_float(params.get("max_pointing_error_deg"), 0.25))
        requested = _bool_profile(params, "requested_profile", "requested", True, n_steps)
        pointing = _numeric_profile(params, "pointing_error_profile_deg", "pointing_error_deg", 0.05, n_steps)
        mode_values = _list_profile(params, "mode_profile", "mode", "observation", n_steps)
        faults = _normalized_events(spec, "faults", "fault_type")
        degradations = _normalized_events(spec, "degradations", "degradation_type")
        state = PayloadInstrumentState()
        rows = []
        for i, t in enumerate(times):
            idx = min(max(i - 1, 0), n_steps - 1)
            effective_requested = requested[idx]
            effective_pointing = pointing[idx]
            rate_factor = 1.0
            fault_active = False
            degradation_active = False
            for event in faults:
                if not _event_active(event, t):
                    continue
                fault_active = True
                effect = _event_name(event, "fault")
                severity = _event_severity(event)
                if effect == "instrument_off":
                    effective_requested = False
                elif effect == "saturation":
                    rate_factor *= 1.0 - 0.9 * severity
                else:
                    effective_pointing += cfg.max_pointing_error_deg * (1.0 + severity)
            for event in degradations:
                fraction = _degradation_fraction(event, t)
                if fraction <= 0.0:
                    continue
                degradation_active = True
                rate_factor *= 1.0 - (0.8 if _event_name(event, "degradation") == "sensitivity_decay" else 0.5) * fraction
            if i:
                step = PayloadInstrumentStepInput(
                    dt_s=sample_s,
                    mode=str(mode_values[idx]),
                    requested=effective_requested,
                    pointing_error_deg=effective_pointing,
                    data_rate_bps_override=cfg.data_rate_bps * max(0.0, rate_factor),
                )
                state, result = step_payload_instrument(state, cfg, step)
                enabled = result.enabled
                power_w = result.power_w
                heat_w = result.heat_w
                generated_bps = result.generated_bps
                generated_bits = result.generated_bits
                block_reasons = result.block_reasons
            else:
                enabled = False
                power_w = cfg.standby_power_w
                heat_w = cfg.standby_power_w * cfg.heat_fraction
                generated_bps = 0.0
                generated_bits = 0.0
                block_reasons = ("initial_state",)
            mode = str(_mapping(spec.get("target")).get("mode") or "nominal")
            row = _row_base(spec, self.target_name, i, t, mode)
            row.update({
                "payload.instrument.enabled": bool(enabled),
                "payload.instrument.power_w": float(power_w),
                "payload.instrument.heat_w": float(heat_w),
                "payload.instrument.generated_bps": float(generated_bps),
                "payload.instrument.generated_bits": float(generated_bits),
                "payload.instrument.cumulative_data_bits": float(state.cumulative_data_bits),
                "payload.instrument.effective_rate_factor": rate_factor,
                "payload.instrument.block_reasons": ";".join(block_reasons),
                "label.payload_state": "enabled" if enabled else "blocked",
                "label.fault_active": fault_active,
                "label.degradation_active": degradation_active,
                "label.health_state": "fault" if fault_active else ("degraded" if degradation_active or not enabled else "nominal"),
            })
            rows.append(row)
        qoi = {"payload.instrument.final_cumulative_data_bits": float(rows[-1]["payload.instrument.cumulative_data_bits"]), "payload.instrument.max_power_w": max(float(r["payload.instrument.power_w"]) for r in rows)}
        return self._result(spec, rows, qoi)


class PayloadSensorAdapter(_BaseComponentAdapter):
    capability_id = "component.payload_sensor.v1"
    target_name = "payload_sensor"
    supported_modes = ("nominal", "fault", "degradation")
    fault_effects = ("pixel_dropout", "blur", "radiation_hit")
    degradation_effects = ("responsivity_decay", "noise_growth")

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from components.payload_sensor.builder import build_nominal_payload_sensor_config, compute_payload_sensor

        params = _mapping(spec.get("parameters"))
        _, sample_s, n_steps, times = _time_grid(spec)
        base_cfg = build_nominal_payload_sensor_config(nominal_data_rate_bps=_float(params.get("nominal_data_rate_bps"), 250_000.0), max_pointing_error_deg=_float(params.get("max_pointing_error_deg"), 0.25), min_quality_score=_float(params.get("min_quality_score"), 0.5))
        pointing = _numeric_profile(params, "pointing_error_profile_deg", "pointing_error_deg", 0.05, n_steps)
        cloud = _numeric_profile(params, "cloud_fraction_profile", "cloud_fraction", 0.0, n_steps)
        faults = _normalized_events(spec, "faults", "fault_type")
        degradations = _normalized_events(spec, "degradations", "degradation_type")
        rows = []
        cumulative = 0.0
        for i, t in enumerate(times):
            idx = min(max(i - 1, 0), n_steps - 1)
            effective_pointing = pointing[idx]
            effective_cloud = cloud[idx]
            rate_factor = 1.0
            fault_active = False
            degradation_active = False
            for event in faults:
                if not _event_active(event, t):
                    continue
                fault_active = True
                effect = _event_name(event, "fault")
                severity = _event_severity(event)
                if effect == "pixel_dropout":
                    rate_factor *= 1.0 - 0.9 * severity
                elif effect == "blur":
                    effective_pointing += base_cfg.max_pointing_error_deg * severity
                else:
                    effective_cloud = min(1.0, effective_cloud + severity)
            for event in degradations:
                fraction = _degradation_fraction(event, t)
                if fraction <= 0.0:
                    continue
                degradation_active = True
                if _event_name(event, "degradation") == "responsivity_decay":
                    rate_factor *= 1.0 - 0.8 * fraction
                else:
                    effective_pointing += base_cfg.max_pointing_error_deg * fraction
            cfg = replace(base_cfg, nominal_data_rate_bps=base_cfg.nominal_data_rate_bps * max(0.0, rate_factor))
            result = compute_payload_sensor(sample_s, effective_pointing, effective_cloud, cfg)
            if i > 0:
                cumulative += float(result.generated_bits)
            mode = str(_mapping(spec.get("target")).get("mode") or "nominal")
            row = _row_base(spec, self.target_name, i, t, mode)
            row.update({
                "payload.sensor.pointing_error_deg": effective_pointing,
                "payload.sensor.cloud_fraction": effective_cloud,
                "payload.sensor.valid_observation": bool(result.valid_observation),
                "payload.sensor.generated_bits": float(result.generated_bits),
                "payload.sensor.quality_score": float(result.quality_score),
                "payload.sensor.effective_data_rate_bps": float(cfg.nominal_data_rate_bps),
                "payload.sensor.cumulative_generated_bits": cumulative,
                "label.payload_sensor_state": "valid" if result.valid_observation else "invalid",
                "label.fault_active": fault_active,
                "label.degradation_active": degradation_active,
                "label.health_state": "fault" if fault_active else ("degraded" if degradation_active or not result.valid_observation else "nominal"),
            })
            rows.append(row)
        qoi = {"payload.sensor.final_cumulative_generated_bits": cumulative, "payload.sensor.min_quality_score": min(float(r["payload.sensor.quality_score"]) for r in rows)}
        return self._result(spec, rows, qoi)


class CmgAdapter(_BaseComponentAdapter):
    capability_id = "component.cmg.v1"
    target_name = "cmg"
    supported_modes = ("nominal", "fault", "degradation")
    fault_effects = ("gimbal_stuck", "wheel_drive_fault", "rate_limit_fault")
    degradation_effects = ("gimbal_friction_growth", "momentum_loss")

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from components.cmg.builder import SingleGimbalCmgState, propagate_single_cmg_state
        from components.cmg.schemas import SingleGimbalCmgConfig

        params = _mapping(spec.get("parameters"))
        _, sample_s, n_steps, times = _time_grid(spec)
        config = SingleGimbalCmgConfig(
            wheel_inertia_kg_m2=_float(params.get("wheel_inertia_kg_m2"), 1.0),
            wheel_speed_rad_s=_float(params.get("initial_wheel_speed_rad_s"), 10.0),
            gimbal_angle_rad=_float(params.get("initial_gimbal_angle_rad"), 0.0),
            gimbal_rate_limit_rad_s=_float(params.get("gimbal_rate_limit_rad_s"), 1.0),
            wheel_speed_limit_rad_s=_float(params.get("wheel_speed_limit_rad_s"), 100.0),
        )
        state = SingleGimbalCmgState(config.wheel_speed_rad_s, config.gimbal_angle_rad)
        rates = _numeric_profile(params, "gimbal_rate_profile_rad_s", "gimbal_rate_cmd_rad_s", 0.1, n_steps)
        torques = _numeric_profile(params, "wheel_torque_profile_nm", "wheel_torque_nm", 0.01, n_steps)
        faults = _normalized_events(spec, "faults", "fault_type")
        degradations = _normalized_events(spec, "degradations", "degradation_type")
        mode = str(_mapping(spec.get("target")).get("mode") or "nominal")
        rows: list[dict[str, Any]] = []
        for index, time_s in enumerate(times):
            profile_index = min(max(index - 1, 0), n_steps - 1)
            effective_rate = rates[profile_index]
            effective_torque = torques[profile_index]
            momentum_factor = 1.0
            fault_active = False
            degradation_active = False
            for event in faults:
                if not _event_active(event, time_s):
                    continue
                fault_active = True
                name = _event_name(event, "fault")
                severity = _event_severity(event)
                if name == "gimbal_stuck":
                    effective_rate = 0.0
                elif name == "wheel_drive_fault":
                    effective_torque = 0.0
                else:
                    effective_rate *= max(0.0, 1.0 - severity)
            for event in degradations:
                fraction = _degradation_fraction(event, time_s)
                if fraction <= 0.0:
                    continue
                degradation_active = True
                if _event_name(event, "degradation") == "gimbal_friction_growth":
                    effective_rate *= max(0.0, 1.0 - fraction)
                else:
                    effective_torque *= max(0.0, 1.0 - fraction)
                    momentum_factor *= max(0.0, 1.0 - fraction)
            if index:
                state = propagate_single_cmg_state(state, config, effective_rate, effective_torque, sample_s)
            momentum = config.wheel_inertia_kg_m2 * state.wheel_speed_rad_s * momentum_factor
            row = _row_base(spec, self.target_name, index, time_s, mode)
            row.update({
                "adcs.cmg.wheel_speed_rad_s": float(state.wheel_speed_rad_s),
                "adcs.cmg.gimbal_angle_rad": float(state.gimbal_angle_rad),
                "adcs.cmg.wheel_momentum_nms": float(momentum),
                "adcs.cmg.commanded_gimbal_rate_rad_s": effective_rate,
                "adcs.cmg.commanded_wheel_torque_nm": effective_torque,
                "label.fault_active": fault_active,
                "label.degradation_active": degradation_active,
                "label.health_state": "fault" if fault_active else ("degraded" if degradation_active else "nominal"),
            })
            rows.append(row)
        qoi = {
            "adcs.cmg.final_wheel_speed_rad_s": rows[-1]["adcs.cmg.wheel_speed_rad_s"],
            "adcs.cmg.final_gimbal_angle_rad": rows[-1]["adcs.cmg.gimbal_angle_rad"],
            "adcs.cmg.max_abs_momentum_nms": max(abs(float(row["adcs.cmg.wheel_momentum_nms"])) for row in rows),
        }
        return self._result(spec, rows, qoi)


class FuelTankAdapter(_BaseComponentAdapter):
    capability_id = "component.fuel_tank.v1"
    target_name = "fuel_tank"
    supported_modes = ("nominal", "fault", "degradation")
    fault_effects = ("leak", "outlet_blockage", "pressure_sensor_fault")
    degradation_effects = ("pressure_decay", "outlet_aging")

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from components.fuel_tank.model import initialize_fuel_tank, step_fuel_tank
        from components.fuel_tank.schemas import FuelTankConfig

        params = _mapping(spec.get("parameters"))
        _, sample_s, n_steps, times = _time_grid(spec)
        config = FuelTankConfig(
            capacity_kg=_float(params.get("capacity_kg"), 3.0),
            initial_mass_kg=_float(params.get("initial_mass_kg"), 2.0),
            full_pressure_pa=_float(params.get("full_pressure_pa"), 2.5e6),
            dry_pressure_pa=_float(params.get("dry_pressure_pa"), 1.5e5),
            leak_rate_kg_s=_float(params.get("leak_rate_kg_s"), 0.0),
        )
        flow = _numeric_profile(params, "mass_flow_profile_kg_s", "mass_flow_kg_s", 0.001, n_steps)
        faults = _normalized_events(spec, "faults", "fault_type")
        degradations = _normalized_events(spec, "degradations", "degradation_type")
        mode = str(_mapping(spec.get("target")).get("mode") or "nominal")
        state = initialize_fuel_tank(config)
        rows = []
        for index, time_s in enumerate(times):
            flow_index = min(max(index - 1, 0), n_steps - 1)
            effective_flow = flow[flow_index]
            pressure_factor = 1.0
            fault_active = False
            degradation_active = False
            for event in faults:
                if not _event_active(event, time_s):
                    continue
                fault_active = True
                name = _event_name(event, "fault")
                severity = _event_severity(event)
                if name == "leak":
                    effective_flow += max(0.001, flow[flow_index]) * (1.0 + 4.0 * severity)
                elif name == "outlet_blockage":
                    effective_flow *= max(0.0, 1.0 - severity)
                else:
                    pressure_factor *= max(0.0, 1.0 - severity)
            for event in degradations:
                fraction = _degradation_fraction(event, time_s)
                if fraction <= 0.0:
                    continue
                degradation_active = True
                if _event_name(event, "degradation") == "pressure_decay":
                    pressure_factor *= max(0.05, 1.0 - fraction)
                else:
                    effective_flow *= max(0.05, 1.0 - fraction)
            if index:
                state = step_fuel_tank(state, config, effective_flow, sample_s)
            measured_pressure = state.pressure_pa * pressure_factor
            row = _row_base(spec, self.target_name, index, float(time_s), mode)
            row.update({
                "propulsion.fuel_tank.propellant_mass_kg": float(state.propellant_mass_kg),
                "propulsion.fuel_tank.pressure_pa": float(measured_pressure),
                "propulsion.fuel_tank.commanded_mass_flow_kg_s": effective_flow,
                "label.fault_active": fault_active,
                "label.degradation_active": degradation_active,
                "label.health_state": "fault" if fault_active else ("degraded" if degradation_active else "nominal"),
            })
            rows.append(row)
        qoi = {
            "propulsion.fuel_tank.initial_mass_kg": rows[0]["propulsion.fuel_tank.propellant_mass_kg"],
            "propulsion.fuel_tank.final_mass_kg": rows[-1]["propulsion.fuel_tank.propellant_mass_kg"],
            "propulsion.fuel_tank.final_pressure_pa": rows[-1]["propulsion.fuel_tank.pressure_pa"],
            "propulsion.fuel_tank.consumed_mass_kg": rows[0]["propulsion.fuel_tank.propellant_mass_kg"] - rows[-1]["propulsion.fuel_tank.propellant_mass_kg"],
        }
        return self._result(spec, rows, qoi)


class ImuAdapter(_BaseComponentAdapter):
    capability_id = "component.imu.v1"
    target_name = "imu"
    supported_modes = ("nominal", "fault", "degradation")
    fault_effects = ("bias_step", "axis_dropout", "noise_burst")
    degradation_effects = ("bias_drift", "noise_density_growth")

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from components.imu.builder import ImuBiasState, measure_imu
        from components.imu.schemas import ImuConfig

        params = _mapping(spec.get("parameters"))
        _, _, _, times = _time_grid(spec)
        gyro = _vec3(params.get("true_gyro_rad_s"), (0.1, 0.2, 0.3))
        accel = _vec3(params.get("true_accel_m_s2"), (0.0, 0.0, 0.0))
        config = ImuConfig(
            gyro_scale=_vec3(params.get("gyro_scale"), (1.0, 1.0, 1.0)),
            accel_scale=_vec3(params.get("accel_scale"), (1.0, 1.0, 1.0)),
            gyro_bias_rad_s=_vec3(params.get("gyro_bias_rad_s"), (0.0, 0.0, 0.0)),
            accel_bias_m_s2=_vec3(params.get("accel_bias_m_s2"), (0.0, 0.0, 0.0)),
        )
        faults = _normalized_events(spec, "faults", "fault_type")
        degradations = _normalized_events(spec, "degradations", "degradation_type")
        mode = str(_mapping(spec.get("target")).get("mode") or "nominal")
        rows = []
        for index, time_s in enumerate(times):
            bias = list(config.gyro_bias_rad_s)
            scale = list(config.gyro_scale)
            fault_active = False
            degradation_active = False
            for event in faults:
                if not _event_active(event, time_s):
                    continue
                fault_active = True
                name = _event_name(event, "fault")
                severity = _event_severity(event)
                if name == "bias_step":
                    bias[0] += 0.2 * severity
                elif name == "axis_dropout":
                    scale[0] = 0.0
                    bias[0] = 0.0
                else:
                    bias[0] += 0.5 * severity
            for event in degradations:
                fraction = _degradation_fraction(event, time_s)
                if fraction <= 0.0:
                    continue
                degradation_active = True
                bias[0] += (0.1 if _event_name(event, "degradation") == "bias_drift" else 0.2) * fraction
            effective_config = replace(config, gyro_scale=tuple(scale), gyro_bias_rad_s=tuple(bias))
            measurement = measure_imu(gyro, accel, effective_config, ImuBiasState())
            row = _row_base(spec, self.target_name, index, time_s, mode)
            for axis, value in zip("xyz", measurement.gyro_rad_s):
                row[f"adcs.imu.gyro_{axis}_rad_s"] = float(value)
            for axis, value in zip("xyz", measurement.accel_m_s2):
                row[f"adcs.imu.accel_{axis}_m_s2"] = float(value)
            row["adcs.imu.effective_gyro_bias_x_rad_s"] = bias[0]
            row["label.fault_active"] = fault_active
            row["label.degradation_active"] = degradation_active
            row["label.health_state"] = "fault" if fault_active else ("degraded" if degradation_active else "nominal")
            rows.append(row)
        gyro_error = math.sqrt(sum((float(rows[-1][f"adcs.imu.gyro_{axis}_rad_s"]) - gyro[i]) ** 2 for i, axis in enumerate("xyz")))
        accel_error = math.sqrt(sum((float(rows[-1][f"adcs.imu.accel_{axis}_m_s2"]) - accel[i]) ** 2 for i, axis in enumerate("xyz")))
        return self._result(spec, rows, {"adcs.imu.gyro_error_norm_rad_s": gyro_error, "adcs.imu.accel_error_norm_m_s2": accel_error})


class MagnetometerAdapter(_BaseComponentAdapter):
    capability_id = "component.magnetometer.v1"
    target_name = "magnetometer"
    supported_modes = ("nominal", "fault", "degradation")
    fault_effects = ("axis_bias", "axis_dropout", "saturation")
    degradation_effects = ("bias_drift", "scale_factor_drift")

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from components.magnetometer.builder import measure_magnetic_field_sensor
        from components.magnetometer.schemas import MagnetometerConfig

        params = _mapping(spec.get("parameters"))
        _, _, _, times = _time_grid(spec)
        field = _vec3(params.get("magnetic_field_t"), (2.0e-5, 0.0, 4.0e-5))
        config = MagnetometerConfig(
            axis_scale=_vec3(params.get("axis_scale"), (1.0, 1.0, 1.0)),
            bias_t=_vec3(params.get("bias_t"), (0.0, 0.0, 0.0)),
        )
        faults = _normalized_events(spec, "faults", "fault_type")
        degradations = _normalized_events(spec, "degradations", "degradation_type")
        mode = str(_mapping(spec.get("target")).get("mode") or "nominal")
        rows = []
        for index, time_s in enumerate(times):
            bias = list(config.bias_t)
            scale = list(config.axis_scale)
            fault_active = False
            degradation_active = False
            saturation = False
            for event in faults:
                if not _event_active(event, time_s):
                    continue
                fault_active = True
                name = _event_name(event, "fault")
                severity = _event_severity(event)
                if name == "axis_bias":
                    bias[0] += 5.0e-5 * severity
                elif name == "axis_dropout":
                    scale[0] = 0.0
                    bias[0] = 0.0
                else:
                    saturation = True
            for event in degradations:
                fraction = _degradation_fraction(event, time_s)
                if fraction <= 0.0:
                    continue
                degradation_active = True
                if _event_name(event, "degradation") == "bias_drift":
                    bias[0] += 2.0e-5 * fraction
                else:
                    scale[0] *= max(0.0, 1.0 - fraction)
            effective_config = replace(config, axis_scale=tuple(scale), bias_t=tuple(bias))
            measured = list(measure_magnetic_field_sensor(field, effective_config))
            if saturation:
                measured = [max(-1.0e-6, min(1.0e-6, value)) for value in measured]
            row = _row_base(spec, self.target_name, index, time_s, mode)
            for axis, value in zip("xyz", measured):
                row[f"adcs.magnetometer.field_{axis}_t"] = float(value)
            row["adcs.magnetometer.field_norm_t"] = math.sqrt(sum(float(value) ** 2 for value in measured))
            row["adcs.magnetometer.effective_bias_x_t"] = bias[0]
            row["label.fault_active"] = fault_active
            row["label.degradation_active"] = degradation_active
            row["label.health_state"] = "fault" if fault_active else ("degraded" if degradation_active else "nominal")
            rows.append(row)
        error = math.sqrt(sum((float(rows[-1][f"adcs.magnetometer.field_{axis}_t"]) - field[i]) ** 2 for i, axis in enumerate("xyz")))
        return self._result(spec, rows, {"adcs.magnetometer.field_norm_t": rows[-1]["adcs.magnetometer.field_norm_t"], "adcs.magnetometer.error_norm_t": error})


class PduAdapter(_BaseComponentAdapter):
    capability_id = "component.pdu.v1"
    target_name = "pdu"
    supported_modes = ("nominal", "fault", "degradation")
    fault_effects = ("channel_trip", "over_current", "switch_stuck")
    degradation_effects = ("contact_resistance_growth", "voltage_regulation_drift")

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from components.pdu.builder import apply_load_shedding
        from components.pdu.schemas import PduConfig

        params = _mapping(spec.get("parameters"))
        _, _, _, times = _time_grid(spec)
        raw_loads = params.get("loads_w")
        loads = {str(key): _float(value, 0.0) for key, value in raw_loads.items()} if isinstance(raw_loads, Mapping) else {"adcs": 35.0, "payload": 55.0, "comm": 25.0}
        shed_order_raw = params.get("shed_order")
        shed_order = tuple(str(value) for value in shed_order_raw) if isinstance(shed_order_raw, list) else ("payload", "comm", "adcs")
        config = PduConfig(bus_max_w=_float(params.get("bus_max_w"), 100.0), shed_order=shed_order)
        faults = _normalized_events(spec, "faults", "fault_type")
        degradations = _normalized_events(spec, "degradations", "degradation_type")
        mode = str(_mapping(spec.get("target")).get("mode") or "nominal")
        rows = []
        for index, time_s in enumerate(times):
            effective_loads = dict(loads)
            bus_factor = 1.0
            delivery_factor = 1.0
            fault_active = False
            degradation_active = False
            for event in faults:
                if not _event_active(event, time_s):
                    continue
                fault_active = True
                name = _event_name(event, "fault")
                severity = _event_severity(event)
                if name == "channel_trip":
                    effective_loads.pop("payload", None)
                elif name == "over_current":
                    effective_loads["fault_over_current"] = config.bus_max_w * (0.5 + severity)
                else:
                    bus_factor *= max(0.1, 1.0 - 0.8 * severity)
            for event in degradations:
                fraction = _degradation_fraction(event, time_s)
                if fraction <= 0.0:
                    continue
                degradation_active = True
                if _event_name(event, "degradation") == "contact_resistance_growth":
                    delivery_factor *= max(0.1, 1.0 - 0.5 * fraction)
                else:
                    bus_factor *= max(0.1, 1.0 - 0.5 * fraction)
            effective_config = replace(config, bus_max_w=config.bus_max_w * bus_factor)
            result = apply_load_shedding(effective_loads, effective_config)
            delivered = float(result.demand_after_w) * delivery_factor
            row = _row_base(spec, self.target_name, index, time_s, mode)
            row.update({
                "eps.pdu.requested_power_w": sum(effective_loads.values()),
                "eps.pdu.delivered_power_w": delivered,
                "eps.pdu.effective_bus_max_w": float(effective_config.bus_max_w),
                "eps.pdu.shed_count": len(result.shed),
                "eps.pdu.overload_remaining": bool(result.overload_remaining),
                "label.fault_active": fault_active,
                "label.degradation_active": degradation_active,
                "label.health_state": "fault" if fault_active else ("degraded" if degradation_active else "nominal"),
            })
            rows.append(row)
        return self._result(spec, rows, {
            "eps.pdu.requested_power_w": rows[-1]["eps.pdu.requested_power_w"],
            "eps.pdu.delivered_power_w": rows[-1]["eps.pdu.delivered_power_w"],
            "eps.pdu.shed_count": rows[-1]["eps.pdu.shed_count"],
        })


class StarTrackerAdapter(_BaseComponentAdapter):
    capability_id = "component.star_tracker.v1"
    target_name = "star_tracker"
    supported_modes = ("nominal", "fault", "degradation")
    fault_effects = ("blinding", "dropout", "misalignment")
    degradation_effects = ("centroid_noise_growth", "boresight_drift")

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from components.star_tracker.builder import StarTrackerState, measure_star_tracker
        from components.star_tracker.schemas import StarTrackerConfig

        params = _mapping(spec.get("parameters"))
        _, sample_s, _, times = _time_grid(spec)
        sigma = _vec3(params.get("true_sigma_bn"), (0.1, 0.0, 0.0))
        config = StarTrackerConfig(
            drift_rate_mrp_s=_vec3(params.get("drift_rate_mrp_s"), (0.0, 0.0, 0.0)),
            max_drift_norm=_float(params.get("max_drift_norm"), 1.0),
        )
        faults = _normalized_events(spec, "faults", "fault_type")
        degradations = _normalized_events(spec, "degradations", "degradation_type")
        mode = str(_mapping(spec.get("target")).get("mode") or "nominal")
        state = StarTrackerState()
        rows = []
        for index, time_s in enumerate(times):
            effective_sigma = list(sigma)
            drift_rate = list(config.drift_rate_mrp_s)
            forced_invalid = False
            fault_active = False
            degradation_active = False
            for event in faults:
                if not _event_active(event, time_s):
                    continue
                fault_active = True
                name = _event_name(event, "fault")
                severity = _event_severity(event)
                if name in {"blinding", "dropout"}:
                    forced_invalid = True
                else:
                    effective_sigma[0] += 0.2 * severity
            for event in degradations:
                fraction = _degradation_fraction(event, time_s)
                if fraction <= 0.0:
                    continue
                degradation_active = True
                if _event_name(event, "degradation") == "centroid_noise_growth":
                    effective_sigma[0] += 0.1 * fraction
                else:
                    drift_rate[0] += 0.01 * fraction
            effective_config = replace(config, drift_rate_mrp_s=tuple(drift_rate))
            measurement = measure_star_tracker(tuple(effective_sigma), effective_config, sample_s if index else 0.0, state)
            state = StarTrackerState(measurement.drift_bias_mrp)
            valid = bool(measurement.valid and not forced_invalid)
            row = _row_base(spec, self.target_name, index, time_s, mode)
            row["adcs.star_tracker.valid"] = valid
            for axis, value in zip("xyz", measurement.sigma_bn):
                row[f"adcs.star_tracker.sigma_{axis}"] = float(value) if valid else 0.0
            row["adcs.star_tracker.drift_norm"] = math.sqrt(sum(float(value) ** 2 for value in measurement.drift_bias_mrp))
            row["label.fault_active"] = fault_active
            row["label.degradation_active"] = degradation_active
            row["label.health_state"] = "fault" if fault_active else ("degraded" if degradation_active else "nominal")
            rows.append(row)
        return self._result(spec, rows, {
            "adcs.star_tracker.valid_fraction": sum(bool(row["adcs.star_tracker.valid"]) for row in rows) / len(rows),
            "adcs.star_tracker.final_drift_norm": rows[-1]["adcs.star_tracker.drift_norm"],
        })


class SunSensorAdapter(_BaseComponentAdapter):
    capability_id = "component.sun_sensor.v1"
    target_name = "sun_sensor"
    supported_modes = ("nominal", "fault", "degradation")
    fault_effects = ("false_eclipse", "cell_failure", "contamination")
    degradation_effects = ("sensitivity_decay", "bias_drift")

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from components.sun_sensor.builder import measure_sun_sensor
        from components.sun_sensor.schemas import SunSensorConfig

        params = _mapping(spec.get("parameters"))
        _, _, _, times = _time_grid(spec)
        sun = _vec3(params.get("sun_direction_b"), (1.0, 0.0, 0.0))
        shadow = _float(params.get("shadow_factor"), 1.0)
        config = SunSensorConfig(
            min_intensity=_float(params.get("min_intensity"), 1e-6),
            accuracy=_float(params.get("accuracy"), 1.0),
            n_hat_b=_vec3(params.get("n_hat_b"), (1.0, 0.0, 0.0)),
            fov_rad=_float(params.get("fov_rad"), math.pi / 2.0),
        )
        faults = _normalized_events(spec, "faults", "fault_type")
        degradations = _normalized_events(spec, "degradations", "degradation_type")
        mode = str(_mapping(spec.get("target")).get("mode") or "nominal")
        rows = []
        for index, time_s in enumerate(times):
            effective_shadow = shadow
            effective_sun = list(sun)
            fault_active = False
            degradation_active = False
            for event in faults:
                if not _event_active(event, time_s):
                    continue
                fault_active = True
                name = _event_name(event, "fault")
                severity = _event_severity(event)
                if name in {"false_eclipse", "cell_failure"}:
                    effective_shadow *= max(0.0, 1.0 - severity)
                else:
                    effective_shadow *= max(0.0, 1.0 - 0.8 * severity)
            for event in degradations:
                fraction = _degradation_fraction(event, time_s)
                if fraction <= 0.0:
                    continue
                degradation_active = True
                if _event_name(event, "degradation") == "sensitivity_decay":
                    effective_shadow *= max(0.0, 1.0 - fraction)
                else:
                    effective_sun[1] += 0.5 * fraction
            measurement = measure_sun_sensor(tuple(effective_sun), effective_shadow, config)
            row = _row_base(spec, self.target_name, index, time_s, mode)
            row["adcs.sun_sensor.valid"] = bool(measurement.valid)
            row["adcs.sun_sensor.intensity"] = float(measurement.intensity)
            row["adcs.sun_sensor.effective_shadow_factor"] = float(effective_shadow)
            for axis, value in zip("xyz", measurement.sun_direction_b):
                row[f"adcs.sun_sensor.direction_{axis}"] = float(value)
            row["label.fault_active"] = fault_active
            row["label.degradation_active"] = degradation_active
            row["label.health_state"] = "fault" if fault_active else ("degraded" if degradation_active else "nominal")
            rows.append(row)
        return self._result(spec, rows, {
            "adcs.sun_sensor.valid_fraction": sum(bool(row["adcs.sun_sensor.valid"]) for row in rows) / len(rows),
            "adcs.sun_sensor.mean_intensity": sum(float(row["adcs.sun_sensor.intensity"]) for row in rows) / len(rows),
        })


class ThrusterAdapter(_BaseComponentAdapter):
    capability_id = "component.thruster.v1"
    target_name = "thruster"
    supported_modes = ("nominal", "fault", "degradation")
    fault_effects = ("valve_stuck_closed", "valve_stuck_open", "nozzle_blockage")
    degradation_effects = ("isp_decay", "thrust_coefficient_drift")

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from components.thruster.model import compute_thruster_pulse
        from components.thruster.schemas import ThrusterPhysicalConfig

        params = _mapping(spec.get("parameters"))
        _, _, n_steps, times = _time_grid(spec)
        thrust = _float(params.get("thrust_n"), 1.0)
        isp = _float(params.get("isp_s"), 280.0)
        config = ThrusterPhysicalConfig(thrust_n=(thrust,), isp_s=(isp,), directions_b=((1.0, 0.0, 0.0),), lever_arms_b_m=((0.0, 0.5, 0.0),))
        on_times = _numeric_profile(params, "on_time_profile_s", "on_time_s", 0.1, n_steps)
        faults = _normalized_events(spec, "faults", "fault_type")
        degradations = _normalized_events(spec, "degradations", "degradation_type")
        mode = str(_mapping(spec.get("target")).get("mode") or "nominal")
        cumulative_impulse = 0.0
        cumulative_propellant = 0.0
        rows = []
        for index, time_s in enumerate(times):
            profile_index = min(max(index - 1, 0), n_steps - 1)
            effective_on_time = on_times[profile_index] if index else 0.0
            effective_thrust = thrust
            effective_isp = isp
            fault_active = False
            degradation_active = False
            for event in faults:
                if not _event_active(event, time_s):
                    continue
                fault_active = True
                name = _event_name(event, "fault")
                severity = _event_severity(event)
                if name == "valve_stuck_closed":
                    effective_on_time = 0.0
                elif name == "valve_stuck_open":
                    effective_on_time = max(effective_on_time, _float(_event_params(event).get("open_time_s"), 1.0))
                else:
                    effective_thrust *= max(0.0, 1.0 - severity)
            for event in degradations:
                fraction = _degradation_fraction(event, time_s)
                if fraction <= 0.0:
                    continue
                degradation_active = True
                if _event_name(event, "degradation") == "isp_decay":
                    effective_isp *= max(0.05, 1.0 - fraction)
                else:
                    effective_thrust *= max(0.05, 1.0 - fraction)
            effective_config = replace(config, thrust_n=(effective_thrust,), isp_s=(effective_isp,))
            pulse = compute_thruster_pulse((effective_on_time,), effective_config)
            if index:
                cumulative_impulse += sum(float(value) for value in pulse.impulse_ns)
                cumulative_propellant += float(pulse.propellant_used_kg)
            row = _row_base(spec, self.target_name, index, time_s, mode)
            row.update({
                "propulsion.thruster.on_time_s": effective_on_time,
                "propulsion.thruster.effective_thrust_n": effective_thrust,
                "propulsion.thruster.effective_isp_s": effective_isp,
                "propulsion.thruster.cumulative_impulse_ns": cumulative_impulse,
                "propulsion.thruster.cumulative_propellant_kg": cumulative_propellant,
                "label.fault_active": fault_active,
                "label.degradation_active": degradation_active,
                "label.health_state": "fault" if fault_active else ("degraded" if degradation_active else "nominal"),
            })
            rows.append(row)
        return self._result(spec, rows, {
            "propulsion.thruster.total_impulse_ns": cumulative_impulse,
            "propulsion.thruster.propellant_used_kg": cumulative_propellant,
        })


__all__ = [
    "GroundStationAdapter",
    "AntennaAdapter",
    "TransmitterAdapter",
    "LinkBudgetAdapter",
    "PowerSinkAdapter",
    "HeaterAdapter",
    "RadiatorAdapter",
    "ThermalNodeAdapter",
    "PayloadAdapter",
    "PayloadSensorAdapter",
    "CmgAdapter",
    "FuelTankAdapter",
    "ImuAdapter",
    "MagnetometerAdapter",
    "PduAdapter",
    "StarTrackerAdapter",
    "SunSensorAdapter",
    "ThrusterAdapter",
]
