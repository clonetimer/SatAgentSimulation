"""Thermal subsystem package with lazy runtime exports."""
from __future__ import annotations

from .network_config import (
    HeaterParams,
    RadiatorParams,
    SolarPanelParams,
    ThermalConductionPath,
    ThermalNetworkConfig,
    ThermalNodeParams,
    apply_degradation_to_network_config,
    build_nominal_thermal_network_config,
)

_RUNTIME_EXPORTS = {
    "ThermalNetworkSysModel": ("thermal_network", "ThermalNetworkSysModel"),
    "run_thermal_network_simulation": ("thermal_network", "run_thermal_network_simulation"),
    "write_thermal_network_dataset": ("thermal_network", "write_thermal_network_dataset"),
    "run_all_network_modes": ("runner", "run_all_network_modes"),
    "run_network_nominal_case": ("runner", "run_network_nominal_case"),
    "run_network_degradation_case": ("runner", "run_network_degradation_case"),
    "run_network_fault_case": ("runner", "run_network_fault_case"),
}


def __getattr__(name: str):
    target = _RUNTIME_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    from importlib import import_module

    value = getattr(import_module(f"{__name__}.{target[0]}"), target[1])
    globals()[name] = value
    return value
