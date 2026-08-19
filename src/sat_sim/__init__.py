"""Satellite Simulation Library (sat_sim)

A comprehensive satellite simulation library for end-to-end mission analysis.

Main features:
    - Multi-subsystem simulation (EPS, ADCS, Propulsion, Thermal)
    - Component degradation modeling
    - Mission gate monitoring
    - Basilisk integration for orbit and attitude dynamics
    - Whole spacecraft simulation framework

Quick start:
    >>> import sat_sim as ss
    >>> cfg = ss.BatteryConfig(capacity_wh=100.0)
    >>> state = ss.initialize_battery(cfg)
"""
from __future__ import annotations

from sat_sim.exceptions import (
    SatSimBaseException,
    ConfigError,
    ConfigRangeError,
    ConfigValueError,
    ConfigMissingError,
    ComponentError,
    SimulationError,
    validate_positive,
    validate_non_negative,
    validate_between_zero_one,
    validate_not_none,
    validate_range,
)

from components.dynamic_models import (
    BatteryState,
    ThermalNodeState,
    SolarPanelState,
    initialize_battery,
    step_battery,
    step_thermal_node,
    step_solar_tracking,
    v3,
    dot,
    cross,
    norm,
    unit,
    normalize_safe,
)

HAS_SCENARIOS = True
HAS_VISUALIZATION = True
HAS_BENCHMARK = True

from .astrograph_roundtrip import apply_astrograph_closure
from .rw_jam_readiness import (
    ActuatorContractReadiness, BasiliskRuntimeEvidence, NativeCampaignReadiness,
    Phase3HReadinessAssessment, Phase3HReadinessGate, Phase3HReadinessRequest,
    ReadinessStatus, inspect_basilisk_runtime,
)

__version__ = "0.7.8"

_LAZY_EXPORTS = {
    "BatteryConfig": ("sat_sim.config", "BatteryConfig"),
    "ThermalNodeConfig": ("sat_sim.config", "ThermalNodeConfig"),
    "SolarPanelConfig": ("sat_sim.config", "SolarPanelConfig"),
    "ThermalNetworkConfig": ("sat_sim.config", "ThermalNetworkConfig"),
    "build_nominal_thermal_network_config": ("sat_sim.config", "build_nominal_thermal_network_config"),
    "EpsConfig": ("sat_sim.config", "EpsConfig"),
    "AdcsConfig": ("sat_sim.config", "AdcsConfig"),
    "PropulsionConfig": ("sat_sim.config", "PropulsionConfig"),
    "WholeSpacecraftConfig": ("sat_sim.config", "WholeSpacecraftConfig"),
    "WholeSpacecraftRunConfig": ("sat_sim.config", "WholeSpacecraftRunConfig"),
    "get_config_class": ("sat_sim.config", "get_config_class"),
    "list_config_classes": ("sat_sim.config", "list_config_classes"),
    "list_scenarios": ("sat_sim.scenarios", "list_scenarios"),
    "get_scenario": ("sat_sim.scenarios", "get_scenario"),
    "battery_discharge_test": ("sat_sim.scenarios", "battery_discharge_test"),
    "battery_charge_test": ("sat_sim.scenarios", "battery_charge_test"),
    "thermal_node_test": ("sat_sim.scenarios", "thermal_node_test"),
    "thermal_network_simulation": ("sat_sim.scenarios", "thermal_network_simulation"),
    "solar_eclipse_cycle": ("sat_sim.scenarios", "solar_eclipse_cycle"),
    "combined_power_thermal": ("sat_sim.scenarios", "combined_power_thermal"),
    "plot_battery_profile": ("sat_sim.visualization", "plot_battery_profile"),
    "plot_thermal_profile": ("sat_sim.visualization", "plot_thermal_profile"),
    "plot_power_profile": ("sat_sim.visualization", "plot_power_profile"),
    "plot_multi_node_temperature": ("sat_sim.visualization", "plot_multi_node_temperature"),
    "plot_combined_power_thermal": ("sat_sim.visualization", "plot_combined_power_thermal"),
    "plot_eclipse_cycle": ("sat_sim.visualization", "plot_eclipse_cycle"),
    "save_figure": ("sat_sim.visualization", "save_figure"),
    "create_summary_dashboard": ("sat_sim.visualization", "create_summary_dashboard"),
    "has_matplotlib": ("sat_sim.visualization", "has_matplotlib"),
    "BenchmarkResult": ("sat_sim.benchmark", "BenchmarkResult"),
    "benchmark_battery": ("sat_sim.benchmark", "benchmark_battery"),
    "benchmark_thermal_node": ("sat_sim.benchmark", "benchmark_thermal_node"),
    "benchmark_solar_tracking": ("sat_sim.benchmark", "benchmark_solar_tracking"),
    "benchmark_combined": ("sat_sim.benchmark", "benchmark_combined"),
    "run_all_benchmarks": ("sat_sim.benchmark", "run_all_benchmarks"),
    "run_benchmark_suite": ("sat_sim.benchmark", "run_benchmark_suite"),
    "run_whole_spacecraft_native_case": ("whole_spacecraft.runner", "run_whole_spacecraft_native_case"),
    "run_whole_spacecraft_degradation_case": ("whole_spacecraft.runner", "run_whole_spacecraft_degradation_case"),
    "run_whole_spacecraft_fault_case": ("whole_spacecraft.runner", "run_whole_spacecraft_fault_case"),
    "run_whole_spacecraft_all_modes": ("whole_spacecraft.runner", "run_whole_spacecraft_all_modes"),
}


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    from importlib import import_module

    value = getattr(import_module(target[0]), target[1])
    globals()[name] = value
    return value

__all__ = [
    "SatSimBaseException",
    "ConfigError",
    "ConfigRangeError",
    "ConfigValueError",
    "ConfigMissingError",
    "ComponentError",
    "SimulationError",
    "validate_positive",
    "validate_non_negative",
    "validate_between_zero_one",
    "validate_not_none",
    "validate_range",
    "BatteryConfig",
    "ThermalNodeConfig",
    "SolarPanelConfig",
    "ThermalNetworkConfig",
    "build_nominal_thermal_network_config",
    "EpsConfig",
    "AdcsConfig",
    "PropulsionConfig",
    "WholeSpacecraftConfig",
    "WholeSpacecraftRunConfig",
    "get_config_class",
    "list_config_classes",
    "BatteryState",
    "ThermalNodeState",
    "SolarPanelState",
    "initialize_battery",
    "step_battery",
    "step_thermal_node",
    "step_solar_tracking",
    "run_whole_spacecraft_native_case",
    "run_whole_spacecraft_degradation_case",
    "run_whole_spacecraft_fault_case",
    "run_whole_spacecraft_all_modes",
    "v3",
    "dot",
    "cross",
    "norm",
    "unit",
    "normalize_safe",
    "list_scenarios",
    "get_scenario",
    "battery_discharge_test",
    "battery_charge_test",
    "thermal_node_test",
    "thermal_network_simulation",
    "solar_eclipse_cycle",
    "combined_power_thermal",
    "plot_battery_profile",
    "plot_thermal_profile",
    "plot_power_profile",
    "plot_multi_node_temperature",
    "plot_combined_power_thermal",
    "plot_eclipse_cycle",
    "save_figure",
    "create_summary_dashboard",
    "has_matplotlib",
    "BenchmarkResult",
    "benchmark_battery",
    "benchmark_thermal_node",
    "benchmark_solar_tracking",
    "benchmark_combined",
    "run_all_benchmarks",
    "run_benchmark_suite",
    "apply_astrograph_closure",
    "ActuatorContractReadiness",
    "BasiliskRuntimeEvidence",
    "NativeCampaignReadiness",
    "Phase3HReadinessAssessment",
    "Phase3HReadinessGate",
    "Phase3HReadinessRequest",
    "ReadinessStatus",
    "inspect_basilisk_runtime",
    "__version__",
]

# TaskSpec-first API layer.  These imports are intentionally lightweight and do
# not require Basilisk, allowing Agent/CI/UI tooling to validate and compile
# TaskSpecs before simulation execution.
from sat_sim.task_spec import (  # noqa: E402,F401
    TASK_SPEC_VERSION,
    DATASET_MANIFEST_VERSION,
    TaskSpecDocument,
    TaskSpecError,
    load_task_spec,
    spec_sha256,
)
from sat_sim.task_validator import (  # noqa: E402,F401
    ValidationIssue,
    ValidationResult,
    validate_task_spec,
    validate_task_spec_file,
)
from sat_sim.task_compiler import (  # noqa: E402,F401
    CompiledTask,
    compile_task_spec,
    compile_whole_spacecraft_task,
    compile_orbit_environment_task,
)
from sat_sim.task_runner import (  # noqa: E402,F401
    TaskRunResult,
    run_compiled_task,
)
from sat_sim.unified_execution import (  # noqa: E402,F401
    UNIFIED_EXECUTION_VERSION,
    execute_compiled_task,
    get_execution_port,
    get_execution_registry,
)


from sat_sim.execution_planner import (  # noqa: E402,F401
    RESOLVED_SPEC_VERSION,
    EXECUTION_PLAN_VERSION,
    ResolvedSpec,
    ExecutionPlan,
    PlanningResult,
    plan_task_spec,
    validate_execution_plan,
)

from sat_sim.taskspec_alignment import (  # noqa: E402,F401
    align_task_spec_to_source,
    alignment_repair_hints,
    build_alignment_catalog,
)

__all__.extend([
    "TASK_SPEC_VERSION",
    "DATASET_MANIFEST_VERSION",
    "TaskSpecDocument",
    "TaskSpecError",
    "load_task_spec",
    "spec_sha256",
    "ValidationIssue",
    "ValidationResult",
    "validate_task_spec",
    "validate_task_spec_file",
    "CompiledTask",
    "compile_task_spec",
    "compile_whole_spacecraft_task",
    "compile_orbit_environment_task",
    "TaskRunResult",
    "run_compiled_task",
    "UNIFIED_EXECUTION_VERSION",
    "execute_compiled_task",
    "get_execution_port",
    "get_execution_registry",
    "RESOLVED_SPEC_VERSION",
    "EXECUTION_PLAN_VERSION",
    "ResolvedSpec",
    "ExecutionPlan",
    "PlanningResult",
    "plan_task_spec",
    "validate_execution_plan",
    "align_task_spec_to_source",
    "alignment_repair_hints",
    "build_alignment_catalog",
])
