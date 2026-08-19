"""Basilisk-backed simulation runners.

These modules must create and execute a Basilisk SimBaseClass simulation.  Pure
availability checks belong under :mod:`backends.basilisk.availability`, not here.

Architecture
------------

* :mod:`builder` - builds the whole-spacecraft graph
  (no execution).  Imports subsystem builders and assembles them.
* :mod:`runner` - runs the whole-spacecraft graph
  (calls Initialize / ExecuteSimulation).  Uses the builder.
* :mod:`legacy_whole_spacecraft_native_assembly` - the original
  all-in-one assembly script (kept for backward compatibility).
* :mod:`legacy_whole_spacecraft_message_runner` - the original
  message-chain runner (kept for backward compatibility).
"""

from .schemas import (
    WholeSpacecraftConfig,
    WholeSpacecraftGraph,
    WholeSpacecraftMissionGateTraceRow,
    WholeSpacecraftRunConfig,
    WholeSpacecraftSummary,
    WholeSpacecraftTraceRow,
)

_LAZY_EXPORTS = {
    "WholeSpacecraftMissionGate": ("_mission_gate", "WholeSpacecraftMissionGate"),
    "build_whole_spacecraft_graph": ("builder", "build_whole_spacecraft_graph"),
    "default_fault_scenarios": ("faults", "default_fault_scenarios"),
    "fault_source_coverage": ("faults", "fault_source_coverage"),
    "runtime_fault_specs_for_scenario": ("faults", "runtime_fault_specs_for_scenario"),
    "DegradationScenario": ("degradation", "DegradationScenario"),
    "default_degradation_scenarios": ("degradation", "default_degradation_scenarios"),
    "degradation_source_coverage": ("degradation", "degradation_source_coverage"),
    "get_degradation_scenario_config": ("degradation", "get_degradation_scenario_config"),
    "run_whole_spacecraft_all_modes": ("runner", "run_whole_spacecraft_all_modes"),
    "run_whole_spacecraft_degradation_case": ("runner", "run_whole_spacecraft_degradation_case"),
    "run_whole_spacecraft_fault_case": ("runner", "run_whole_spacecraft_fault_case"),
    "run_whole_spacecraft_effect_case": ("runner", "run_whole_spacecraft_effect_case"),
    "run_whole_spacecraft_native_case": ("runner", "run_whole_spacecraft_native_case"),
    "write_whole_spacecraft_native_dataset": ("runner", "write_whole_spacecraft_native_dataset"),
}


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    from importlib import import_module

    value = getattr(import_module(f"{__name__}.{target[0]}"), target[1])
    globals()[name] = value
    return value

__all__ = [
    "WholeSpacecraftConfig",
    "WholeSpacecraftGraph",
    "WholeSpacecraftMissionGate",
    "WholeSpacecraftMissionGateTraceRow",
    "WholeSpacecraftRunConfig",
    "WholeSpacecraftSummary",
    "WholeSpacecraftTraceRow",
    "build_whole_spacecraft_graph",
    "run_whole_spacecraft_native_case",
    "run_whole_spacecraft_degradation_case",
    "run_whole_spacecraft_fault_case",
    "run_whole_spacecraft_effect_case",
    "run_whole_spacecraft_all_modes",
    "write_whole_spacecraft_native_dataset",
    "default_fault_scenarios",
    "fault_source_coverage",
    "runtime_fault_specs_for_scenario",
    "DegradationScenario",
    "default_degradation_scenarios",
    "degradation_source_coverage",
    "get_degradation_scenario_config",
]
