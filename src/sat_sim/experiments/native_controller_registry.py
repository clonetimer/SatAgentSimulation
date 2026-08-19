"""Verified parameter-path registry for the official Basilisk MonteCarloController."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NativeControllerParameter:
    task_path: str
    case_attribute: str
    recorder_summary_field: str | None = None
    sensitivity_summary_field: str | None = None
    unit: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_path": self.task_path,
            "case_attribute": self.case_attribute,
            "recorder_summary_field": self.recorder_summary_field,
            "sensitivity_summary_field": self.sensitivity_summary_field,
            "unit": self.unit,
        }


@dataclass(frozen=True)
class NativeControllerContract:
    capability_id: str
    case_kind: str
    parameters: tuple[NativeControllerParameter, ...]
    boundary: str

    @property
    def path_map(self) -> dict[str, str]:
        return {item.task_path: item.case_attribute for item in self.parameters}

    def parameter(self, path: str) -> NativeControllerParameter | None:
        return next((item for item in self.parameters if item.task_path == path), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "supported": True,
            "capability_id": self.capability_id,
            "case_kind": self.case_kind,
            "parameter_paths": [item.task_path for item in self.parameters],
            "parameters": [item.to_dict() for item in self.parameters],
            "boundary": self.boundary,
        }


_COMMON = (
    NativeControllerParameter("simulation.duration_s", "duration_s", unit="s"),
    NativeControllerParameter("simulation.step_s", "step_s", unit="s"),
    NativeControllerParameter("simulation.sample_s", "sample_s", unit="s"),
    NativeControllerParameter("parameters.values.initial_pointing_error_deg", "initial_pointing_error_deg", "initial_recorded_pointing_error_deg", "final_pointing_error_deg", "deg"),
    NativeControllerParameter("parameters.values.orbit_radius_m", "orbit_radius_m", sensitivity_summary_field="mean_orbit_radius_m", unit="m"),
    NativeControllerParameter("parameters.values.inclination_deg", "inclination_deg", sensitivity_summary_field="final_pointing_error_deg", unit="deg"),
)

_CONTRACTS = {
    "whole_spacecraft.bsksim_foundation.v1": NativeControllerContract(
        "whole_spacecraft.bsksim_foundation.v1",
        "foundation",
        _COMMON + (NativeControllerParameter("parameters.values.orbit_rate_rad_s", "orbit_rate_rad_s", sensitivity_summary_field="final_pointing_error_deg", unit="rad/s"),),
        "Official Controller creates, modifies, executes and retains the verified Basilisk foundation case.",
    ),
    "subsystem.adcs_unified_native.v1": NativeControllerContract(
        "subsystem.adcs_unified_native.v1",
        "unified_adcs",
        _COMMON + (
            NativeControllerParameter("parameters.values.controller_k", "controller_k", sensitivity_summary_field="final_pointing_error_deg"),
            NativeControllerParameter("parameters.values.controller_p", "controller_p", sensitivity_summary_field="final_pointing_error_deg"),
            NativeControllerParameter("parameters.values.rw_max_torque_nm", "rw_max_torque_nm", sensitivity_summary_field="max_rw_speed_rad_s", unit="N*m"),
        ),
        "Official Controller modifies a pickle-safe case factory that constructs the ADCS unified Basilisk Process/Task for every case.",
    ),
    "whole_spacecraft.unified_native.v1": NativeControllerContract(
        "whole_spacecraft.unified_native.v1",
        "unified_whole_spacecraft",
        _COMMON + (
            NativeControllerParameter("parameters.values.controller_k", "controller_k", sensitivity_summary_field="final_pointing_error_deg"),
            NativeControllerParameter("parameters.values.controller_p", "controller_p", sensitivity_summary_field="final_pointing_error_deg"),
            NativeControllerParameter("parameters.values.initial_soc", "initial_soc", "initial_recorded_soc", "final_battery_soc", "ratio"),
            NativeControllerParameter("parameters.values.battery_capacity_wh", "battery_capacity_wh", "initial_recorded_battery_capacity_wh", "final_battery_soc", "Wh"),
            NativeControllerParameter("parameters.values.solar_panel_area_m2", "solar_panel_area_m2", sensitivity_summary_field="max_solar_array_power_w", unit="m2"),
            NativeControllerParameter("parameters.values.solar_efficiency", "solar_efficiency", sensitivity_summary_field="max_solar_array_power_w", unit="ratio"),
            NativeControllerParameter("parameters.values.payload_power_w", "payload_power_w", sensitivity_summary_field="final_battery_soc", unit="W"),
            NativeControllerParameter("parameters.values.payload_data_rate_bps", "payload_data_rate_bps", "initial_recorded_payload_data_rate_bps", "final_storage_bits", "bit/s"),
            NativeControllerParameter("parameters.values.downlink_rate_bps", "downlink_rate_bps", sensitivity_summary_field="final_storage_bits", unit="bit/s"),
            NativeControllerParameter("parameters.values.storage_capacity_bits", "storage_capacity_bits", sensitivity_summary_field="final_storage_bits", unit="bit"),
            NativeControllerParameter("parameters.values.initial_payload_temp_k", "initial_payload_temp_k", "initial_recorded_payload_temp_k", "final_payload_temp_k", "K"),
        ),
        "Official Controller modifies a pickle-safe case factory that constructs the whole-spacecraft unified Basilisk Process/Task for every case.",
    ),
}


def native_controller_contract(capability_id: str) -> NativeControllerContract | None:
    return _CONTRACTS.get(str(capability_id))


def native_controller_capability_ids() -> tuple[str, ...]:
    return tuple(sorted(_CONTRACTS))


__all__ = [
    "NativeControllerParameter",
    "NativeControllerContract",
    "native_controller_contract",
    "native_controller_capability_ids",
]
