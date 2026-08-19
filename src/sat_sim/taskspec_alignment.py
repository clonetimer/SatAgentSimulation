"""TaskSpec-to-source alignment helpers for Agent-generated capability specs.

S5 keeps the core rule explicit: LLMs may draft TaskSpecs, but local code owns
alias repair, unit normalization, default filling, and the mapping from TaskSpec
fields to adapter/source-native public APIs.  This module is import-safe and does
not import Basilisk or demo runners.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class FieldAlignment:
    """One TaskSpec field mapping across user, adapter, and source surfaces."""

    canonical_path: str
    adapter_path: str
    source_parameter: str | None = None
    unit: str | None = None
    aliases: tuple[str, ...] = ()
    default: Any = None
    required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_path": self.canonical_path,
            "adapter_path": self.adapter_path,
            "source_parameter": self.source_parameter,
            "unit": self.unit,
            "aliases": list(self.aliases),
            "default": self.default,
            "required": self.required,
        }


@dataclass(frozen=True)
class AlignmentRepair:
    """A deterministic repair/normalization event."""

    action: str
    source_path: str | None
    target_path: str
    message: str
    value: Any = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "action": self.action,
            "source_path": self.source_path,
            "target_path": self.target_path,
            "message": self.message,
        }
        if self.value is not None:
            payload["value"] = self.value
        return payload


@dataclass(frozen=True)
class AlignmentResult:
    """Normalized TaskSpec plus audit metadata."""

    task_spec: dict[str, Any]
    repairs: tuple[AlignmentRepair, ...] = ()
    normalized_defaults: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_spec": self.task_spec,
            "repair_log": [item.to_dict() for item in self.repairs],
            "normalized_defaults": dict(self.normalized_defaults),
        }


# Common aliases that show up in LLM output or user-authored YAML.  Capability-
# specific aliases are added dynamically from capability contract parameters.
COMMON_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "simulation.duration_s": (
        "duration_s",
        "duration_sec",
        "duration_seconds",
        "simulation.duration",
        "simulation.duration_sec",
        "simulation.duration_seconds",
        "simulation.simulation_duration_s",
        "simulation.total_duration_s",
        "simulation.end_time_s",
        "simulation.runtime_s",
    ),
    "simulation.sample_s": (
        "sample_s",
        "sample_interval_s",
        "sampling_interval_s",
        "sample_period_s",
        "time_step_s",
        "dt_s",
        "simulation.sample_interval_s",
        "simulation.sampling_interval_s",
        "simulation.sample_period_s",
        "simulation.time_step_s",
        "simulation.step_s",
        "simulation.dt_s",
    ),
    "outputs.output_root": (
        "output_root",
        "output_dir",
        "dataset_dir",
        "output_path",
        "outputs.output_dir",
        "outputs.dataset_dir",
        "outputs.output_path",
    ),
}

CAPABILITY_PARAMETER_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "component.data_queue.v1": {
        "capacity_bits": ("capacity", "capacity_bits", "queue_capacity_bits", "queue_capacity", "capacity_mb", "capacity_mbit", "buffer_bits"),
        "initial_queue_bits": ("initial_bits", "initial_queue", "initial_backlog_bits", "initial_queue_bits", "queue_initial_bits"),
        "generated_bps": ("generated_rate", "generation_rate", "generation_rate_bps", "generated_rate_bps", "data_generation_rate_bps", "source_rate_bps", "generated_kbps", "generated_mbps"),
        "downlink_bps": ("downlink_rate", "downlink_rate_bps", "downlink_data_rate_bps", "tx_rate_bps", "transmit_rate_bps", "downlink_kbps", "downlink_mbps"),
        "generated_profile_bps": ("generation_profile", "generated_profile", "generated_profile_bps"),
        "downlink_profile_bps": ("downlink_profile", "downlink_profile_bps", "tx_profile_bps"),
    },
    "component.onboard_storage.v1": {
        "capacity_bits": ("capacity", "storage_capacity", "storage_capacity_bits", "storage_capacity_mb", "storage_capacity_mbit", "capacity_bits", "capacity_mb", "capacity_mbit"),
        "high_watermark": ("high_watermark", "high_watermark_ratio", "storage_high_watermark"),
        "initial_stored_bits": ("initial_bits", "initial_storage_bits", "initial_stored", "initial_stored_bits", "stored_bits_initial"),
        "generated_bps": ("generated_rate", "generation_rate", "generation_rate_bps", "generated_rate_bps", "data_generation_rate_bps", "generated_kbps", "generated_mbps"),
        "downlink_bps": ("downlink_rate", "downlink_rate_bps", "downlink_data_rate_bps", "tx_rate_bps", "downlink_kbps", "downlink_mbps"),
        "generated_profile_bps": ("generation_profile", "generated_profile", "generated_profile_bps"),
        "downlink_profile_bps": ("downlink_profile", "downlink_profile_bps", "tx_profile_bps"),
    },

    "component.ground_station.v1": {
        "min_elevation_deg": ("minimum_elevation_deg", "min_elevation", "elevation_mask_deg", "仰角阈值"),
        "max_range_m": ("maximum_range_m", "max_range", "max_range_km", "最大距离"),
        "ground_lat_deg": ("latitude_deg", "lat_deg", "ground_latitude_deg", "地面站纬度"),
        "ground_lon_deg": ("longitude_deg", "lon_deg", "ground_longitude_deg", "地面站经度"),
        "spacecraft_radius_m": ("spacecraft_radius", "orbit_radius_m", "orbit_radius_km", "卫星轨道半径"),
    },
    "component.antenna.v1": {
        "peak_gain_dbi": ("peak_gain", "antenna_gain_dbi", "gain_dbi", "天线增益"),
        "half_power_beamwidth_deg": ("beamwidth_deg", "hp_bw_deg", "半功率波束宽度"),
        "off_boresight_deg": ("off_boresight", "pointing_error_deg", "离轴角", "指向误差"),
    },
    "component.transmitter.v1": {
        "max_tx_power_w": ("max_power_w", "tx_power_w", "发射功率"),
        "requested_rate_bps": ("requested_rate", "data_rate_bps", "requested_mbps", "请求速率"),
        "available_power_w": ("available_power", "power_available_w", "可用功率"),
    },
    "component.link_budget.v1": {
        "raw_rate_bps": ("raw_rate", "data_rate_bps", "raw_rate_mbps", "原始速率"),
        "tx_power_w": ("transmit_power_w", "transmitter_power_w", "发射功率"),
        "slant_range_m": ("range_m", "slant_range", "slant_range_km", "链路距离"),
    },
    "component.power_sink.v1": {
        "base_w": ("nominal_power_w", "power_w", "load_power_w", "负载功率"),
    },
    "component.heater.v1": {
        "max_power_w": ("heater_power_w", "max_heater_power_w", "加热器功率"),
        "setpoint_k": ("heater_setpoint_k", "setpoint_c", "heater_setpoint_c", "设定温度"),
        "node_temp_k": ("temperature_k", "node_temperature_k", "node_temp_c", "节点温度"),
    },
    "component.radiator.v1": {
        "area_m2": ("radiator_area_m2", "散热器面积"),
        "node_temp_k": ("temperature_k", "node_temperature_k", "node_temp_c", "节点温度"),
        "heat_load_w": ("load_w", "thermal_load_w", "heat_w", "热负载"),
    },
    "component.thermal_node.v1": {
        "initial_temp_k": ("initial_temperature_k", "initial_temp_c", "初始温度"),
        "power_w": ("heat_power_w", "thermal_power_w", "热功率"),
        "tau_s": ("time_constant_s", "thermal_tau_s", "热时间常数"),
    },
    "component.payload.v1": {
        "observation_power_w": ("payload_power_w", "instrument_power_w", "载荷功率"),
        "data_rate_bps": ("payload_data_rate_bps", "data_rate_mbps", "数据率"),
        "pointing_error_deg": ("pointing_error", "指向误差"),
    },
    "component.payload_sensor.v1": {
        "nominal_data_rate_bps": ("data_rate_bps", "sensor_data_rate_bps", "sensor_data_rate_mbps", "传感器数据率"),
        "pointing_error_deg": ("pointing_error", "指向误差"),
        "cloud_fraction": ("cloud_cover", "cloud", "云量"),
    },
    "orbit_environment.leo_simple.v1": {
        "altitude_m": ("altitude", "altitude_m", "altitude_km", "orbit_altitude", "orbit_altitude_m", "orbit_altitude_km"),
        "inclination_deg": ("inclination", "inclination_deg", "orbit_inclination_deg"),
        "raan_deg": ("raan", "raan_deg"),
        "arg_lat0_deg": ("arg_lat0", "arg_lat0_deg", "initial_argument_of_latitude_deg"),
    },
    "subsystem.comm.basic_ground_pass.v1": {
        "data_generation_rate_bps": ("generated_bps", "generated_rate_bps", "generation_rate_bps", "data_rate_bps", "data_generation_rate", "generated_mbps"),
        "storage_capacity_bits": ("storage_capacity", "storage_capacity_bits", "capacity_bits", "storage_capacity_mb"),
        "downlink_rate_bps": ("downlink_bps", "downlink_rate", "downlink_rate_bps", "tx_rate_bps", "downlink_mbps"),
        "transmitter_power_w": ("tx_power_w", "transmitter_power", "transmitter_power_w", "comm_power_w"),
    },
}

_UNIT_MULTIPLIERS: dict[str, dict[str, float]] = {
    "s": {"s": 1.0, "sec": 1.0, "second": 1.0, "seconds": 1.0, "min": 60.0, "minute": 60.0, "minutes": 60.0, "h": 3600.0, "hr": 3600.0, "hour": 3600.0, "hours": 3600.0},
    "m": {"m": 1.0, "meter": 1.0, "meters": 1.0, "km": 1000.0, "kilometer": 1000.0, "kilometers": 1000.0},
    "bit": {"bit": 1.0, "bits": 1.0, "b": 1.0, "kbit": 1e3, "kb": 8e3, "mbit": 1e6, "mb": 8e6, "gbit": 1e9, "gb": 8e9},
    "bit/s": {"bps": 1.0, "bit/s": 1.0, "bits/s": 1.0, "kbps": 1e3, "kbit/s": 1e3, "mbps": 1e6, "mbit/s": 1e6, "gbps": 1e9, "gbit/s": 1e9},
    "W": {"w": 1.0, "watt": 1.0, "watts": 1.0, "kw": 1e3},
    "deg": {"deg": 1.0, "degree": 1.0, "degrees": 1.0},
    "ratio": {"ratio": 1.0, "fraction": 1.0, "%": 0.01, "percent": 0.01},
    "Wh": {"wh": 1.0, "kwh": 1000.0},
}

_VALUE_RE = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*([A-Za-z/%]+)?\s*$")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _get_path(data: Mapping[str, Any], dotted_path: str, default: Any = None) -> Any:
    cur: Any = data
    for part in dotted_path.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _has_path(data: Mapping[str, Any], dotted_path: str) -> bool:
    sentinel = object()
    return _get_path(data, dotted_path, sentinel) is not sentinel


def _set_path(data: dict[str, Any], dotted_path: str, value: Any) -> None:
    cur: dict[str, Any] = data
    parts = dotted_path.split(".")
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _pop_path(data: dict[str, Any], dotted_path: str) -> Any:
    cur: Any = data
    parts = dotted_path.split(".")
    for part in parts[:-1]:
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    if not isinstance(cur, dict):
        return None
    return cur.pop(parts[-1], None)


def _canonical_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    low = str(unit).strip().lower()
    if low in {"bit", "bits"}:
        return "bit"
    if low in {"bit/s", "bits/s", "bps"}:
        return "bit/s"
    if low in {"s", "sec", "second", "seconds"}:
        return "s"
    if low in {"m", "meter", "meters"}:
        return "m"
    if low in {"deg", "degree", "degrees"}:
        return "deg"
    if low in {"w", "watt", "watts"}:
        return "W"
    if low in {"ratio", "fraction", "%", "percent"}:
        return "ratio"
    if low == "wh":
        return "Wh"
    return unit


def coerce_value_to_unit(value: Any, target_unit: str | None, *, alias_path: str | None = None) -> Any:
    """Coerce numeric strings or unit-specific alias suffixes to target units."""

    target_unit = _canonical_unit(target_unit)
    if target_unit is None:
        return value
    if isinstance(value, list):
        return [coerce_value_to_unit(item, target_unit, alias_path=alias_path) for item in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        numeric = float(value)
    elif isinstance(value, str):
        m = _VALUE_RE.match(value)
        if not m:
            return value
        numeric = float(m.group(1))
        unit = (m.group(2) or "").lower()
        if unit:
            mult = _UNIT_MULTIPLIERS.get(target_unit, {}).get(unit)
            if mult is not None:
                return numeric * mult
            return numeric
    else:
        return value

    alias = (alias_path or "").lower().replace(".", "_")
    if target_unit == "s":
        if alias.endswith("_min") or alias.endswith("minutes"):
            return numeric * 60.0
        if alias.endswith("_h") or alias.endswith("_hr") or alias.endswith("hours"):
            return numeric * 3600.0
    if target_unit == "m" and alias.endswith("_km"):
        return numeric * 1000.0
    if target_unit == "bit":
        if alias.endswith("_mb") or alias.endswith("_mbyte") or alias.endswith("_megabytes"):
            return numeric * 8e6
        if alias.endswith("_gb") or alias.endswith("_gbyte") or alias.endswith("_gigabytes"):
            return numeric * 8e9
        if alias.endswith("_mbit") or alias.endswith("_mbits"):
            return numeric * 1e6
        if alias.endswith("_gbit") or alias.endswith("_gbits"):
            return numeric * 1e9
    if target_unit == "bit/s":
        if alias.endswith("_kbps"):
            return numeric * 1e3
        if alias.endswith("_mbps"):
            return numeric * 1e6
        if alias.endswith("_gbps"):
            return numeric * 1e9
    if target_unit == "ratio" and (alias.endswith("_pct") or alias.endswith("_percent")):
        return numeric * 0.01
    return numeric


def _parameter_alignments_from_contract(contract: Mapping[str, Any]) -> list[FieldAlignment]:
    cid = str(contract.get("capability_id", ""))
    params = contract.get("parameters") if isinstance(contract.get("parameters"), Mapping) else {}
    aliases_by_param = CAPABILITY_PARAMETER_ALIASES.get(cid, {})
    rows: list[FieldAlignment] = []
    for name, meta in params.items():
        if not isinstance(name, str):
            continue
        m = meta if isinstance(meta, Mapping) else {}
        default = m.get("default")
        unit = _canonical_unit(m.get("unit") if isinstance(m.get("unit"), str) else None)
        # Add a few mechanical aliases if they do not duplicate the canonical path.
        mechanical = [name]
        if unit == "m" and name.endswith("_m"):
            mechanical.append(name[:-2] + "_km")
        if unit == "bit" and name.endswith("_bits"):
            mechanical.extend([name[:-5], name[:-5] + "_mb", name[:-5] + "_mbit"])
        if unit == "bit/s" and name.endswith("_bps"):
            mechanical.extend([name[:-4] + "_kbps", name[:-4] + "_mbps"])
        alias_paths = []
        for alias in list(aliases_by_param.get(name, ())) + mechanical:
            alias_paths.append(f"parameters.{alias}")
            alias_paths.append(alias)
        alias_paths = [a for a in dict.fromkeys(alias_paths) if a != f"parameters.{name}"]
        rows.append(FieldAlignment(
            canonical_path=f"parameters.{name}",
            adapter_path=f"parameters.{name}",
            source_parameter=name,
            unit=unit,
            aliases=tuple(alias_paths),
            default=default,
            required=bool(m.get("required", False)),
        ))
    return rows


def alignment_catalog_for_contract(contract: Mapping[str, Any]) -> list[FieldAlignment]:
    """Return TaskSpec/adapter/source mapping rows for one capability contract."""

    rows = [
        FieldAlignment("simulation.duration_s", "simulation.duration_s", "duration_s", "s", COMMON_FIELD_ALIASES["simulation.duration_s"], 600.0, True),
        FieldAlignment("simulation.sample_s", "simulation.sample_s", "sample_s", "s", COMMON_FIELD_ALIASES["simulation.sample_s"], 60.0, True),
        FieldAlignment("outputs.output_root", "outputs.output_root", "output_root", None, COMMON_FIELD_ALIASES["outputs.output_root"], None, False),
    ]
    rows.extend(_parameter_alignments_from_contract(contract))
    if str(contract.get("capability_id")) in {"orbit_environment.leo_simple.v1", "whole_spacecraft.basic_power_orbit.v1", "whole_spacecraft.basic_power_attitude_orbit.v1", "whole_spacecraft.basic_power_thermal_orbit.v1"}:
        rows.extend([
            FieldAlignment("orbit_environment.altitude_m", "orbit_environment.altitude_m", "altitude_m", "m", ("parameters.altitude", "parameters.altitude_km", "parameters.orbit_altitude_km", "altitude_km", "orbit_altitude_km"), 500000.0, False),
            FieldAlignment("orbit_environment.inclination_deg", "orbit_environment.inclination_deg", "inclination_deg", "deg", ("parameters.inclination", "parameters.inclination_deg", "inclination_deg"), 51.6, False),
        ])
    return rows


def alignment_catalog_for_capability(capability_id: str) -> list[FieldAlignment]:
    from .capability_registry import get_capability

    return alignment_catalog_for_contract(get_capability(capability_id).data)


def build_alignment_catalog() -> dict[str, list[dict[str, Any]]]:
    from .capability_registry import list_capabilities

    return {contract.capability_id: [row.to_dict() for row in alignment_catalog_for_contract(contract.data)] for contract in list_capabilities()}


def alignment_repair_hints(spec: Mapping[str, Any], *, capability: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return deterministic alias/default hints without mutating the spec."""

    contract = capability or {}
    if not contract and isinstance(spec.get("capability_id"), str):
        try:
            from .capability_registry import get_capability
            contract = get_capability(str(spec["capability_id"])).data
        except Exception:
            contract = {}
    hints: list[dict[str, Any]] = []
    for row in alignment_catalog_for_contract(contract):
        if _has_path(spec, row.canonical_path):
            continue
        for alias in row.aliases:
            if _has_path(spec, alias):
                hints.append({
                    "code": "taskspec_src_alias",
                    "path": alias,
                    "message": f"Use {row.canonical_path} instead of alias {alias}.",
                    "canonical_path": row.canonical_path,
                })
                break
        else:
            if row.default is not None and row.required:
                hints.append({
                    "code": "taskspec_src_default",
                    "path": row.canonical_path,
                    "message": f"Missing {row.canonical_path}; default {row.default!r} can be applied.",
                    "canonical_path": row.canonical_path,
                    "default": row.default,
                })
    return hints


def align_task_spec_to_source(spec: Mapping[str, Any], *, capability: Mapping[str, Any] | None = None, fill_defaults: bool = True) -> AlignmentResult:
    """Normalize common TaskSpec aliases and units against capability contract metadata.

    The returned spec remains a normal TaskSpec.  Repairs are recorded under
    ``metadata.repair_log`` and defaults under ``metadata.normalized_defaults`` so
    dataset manifests can audit local deterministic normalization.
    """

    out = copy.deepcopy(dict(spec))
    contract = capability or {}
    if not contract and isinstance(out.get("capability_id"), str):
        try:
            from .capability_registry import get_capability
            contract = get_capability(str(out["capability_id"])).data
        except Exception:
            contract = {}

    repairs: list[AlignmentRepair] = []
    defaults: dict[str, Any] = {}
    for row in alignment_catalog_for_contract(contract):
        alias_used: str | None = None
        alias_value: Any = None
        for alias in row.aliases:
            if _has_path(out, alias):
                alias_used = alias
                alias_value = _pop_path(out, alias)
                break
        if alias_used is not None:
            value = coerce_value_to_unit(alias_value, row.unit, alias_path=alias_used)
            action = "alias_override" if _has_path(out, row.canonical_path) else "alias"
            _set_path(out, row.canonical_path, value)
            repairs.append(AlignmentRepair(action, alias_used, row.canonical_path, f"Mapped alias {alias_used} to {row.canonical_path}.", value))
            continue
        if _has_path(out, row.canonical_path):
            current = _get_path(out, row.canonical_path)
            coerced = coerce_value_to_unit(current, row.unit, alias_path=row.canonical_path)
            if coerced != current:
                _set_path(out, row.canonical_path, coerced)
                repairs.append(AlignmentRepair("unit_normalize", row.canonical_path, row.canonical_path, f"Normalized {row.canonical_path} to {row.unit}.", coerced))
            continue
        if fill_defaults and row.default is not None and row.required:
            _set_path(out, row.canonical_path, row.default)
            defaults[row.canonical_path] = row.default
            repairs.append(AlignmentRepair("default", None, row.canonical_path, f"Applied default for missing {row.canonical_path}.", row.default))

    # C7: mirror legacy top-level faults/degradations/constraints into modifiers for the
    # scenario-modifier layer.  Keep legacy fields for backwards compatibility.
    modifiers = out.get("modifiers")
    if not isinstance(modifiers, dict):
        modifiers = {}
        out["modifiers"] = modifiers
    if "faults" not in modifiers and isinstance(out.get("faults"), list) and out.get("faults"):
        mirrored = []
        for item in out.get("faults") or []:
            if isinstance(item, Mapping):
                payload = dict(item)
                payload.setdefault("target", payload.get("target") or payload.get("target_id"))
                payload.setdefault("severity", payload.get("magnitude"))
                mirrored.append(payload)
        if mirrored:
            modifiers["faults"] = mirrored
            repairs.append(AlignmentRepair("modifier_mirror", "faults", "modifiers.faults", "Mirrored legacy faults into modifiers.faults.", len(mirrored)))
    if "degradations" not in modifiers and isinstance(out.get("degradations"), list) and out.get("degradations"):
        modifiers["degradations"] = list(out.get("degradations") or [])
        repairs.append(AlignmentRepair("modifier_mirror", "degradations", "modifiers.degradations", "Mirrored legacy degradations list into modifiers.degradations.", len(modifiers["degradations"])))
    if "constraints" not in modifiers and isinstance(out.get("constraints"), list) and out.get("constraints"):
        modifiers["constraints"] = list(out.get("constraints") or [])
        repairs.append(AlignmentRepair("modifier_mirror", "constraints", "modifiers.constraints", "Mirrored legacy constraints list into modifiers.constraints.", len(modifiers["constraints"])))

    # Standard output defaults are useful for exported scripts and datasets.
    outputs = out.get("outputs")
    if not isinstance(outputs, dict):
        outputs = {}
        out["outputs"] = outputs
    standard_output_defaults = {
        "trace_format": "csv",
        "include_summary": True,
        "include_trace": True,
        "include_labels": True,
        "include_manifest": True,
    }
    for key, value in standard_output_defaults.items():
        if key not in outputs and fill_defaults:
            outputs[key] = value
            defaults[f"outputs.{key}"] = value

    metadata = out.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        out["metadata"] = metadata
    existing_log = metadata.get("repair_log") if isinstance(metadata.get("repair_log"), list) else []
    if repairs:
        metadata["repair_log"] = list(existing_log) + [r.to_dict() for r in repairs]
    existing_defaults = metadata.get("normalized_defaults") if isinstance(metadata.get("normalized_defaults"), Mapping) else {}
    merged_defaults = {**dict(existing_defaults), **defaults}
    if merged_defaults:
        metadata["normalized_defaults"] = merged_defaults
    metadata.setdefault("taskspec_src_alignment", {})
    if isinstance(metadata["taskspec_src_alignment"], dict):
        metadata["taskspec_src_alignment"].update({
            "schema_version": "s5.taskspec_src_alignment.v1",
            "capability_id": str(contract.get("capability_id", out.get("capability_id", ""))),
            "repair_count": len(repairs),
        })
    return AlignmentResult(task_spec=out, repairs=tuple(repairs), normalized_defaults=defaults)


# Backward-friendly alias used by tests and future tooling.
normalize_task_spec_for_source = align_task_spec_to_source


__all__ = [
    "AlignmentRepair",
    "AlignmentResult",
    "FieldAlignment",
    "align_task_spec_to_source",
    "alignment_catalog_for_capability",
    "alignment_catalog_for_contract",
    "alignment_repair_hints",
    "build_alignment_catalog",
    "coerce_value_to_unit",
    "normalize_task_spec_for_source",
]
