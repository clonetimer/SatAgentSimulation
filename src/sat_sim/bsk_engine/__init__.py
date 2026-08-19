"""BSKSim-style foundation for staged Basilisk migration."""
from .types import (
    BSK_ENGINE_SCHEMA_VERSION,
    BSKConnectionSpec,
    BSKEventSpec,
    BSKExecutionPlan,
    BSKModuleSpec,
    BSKProcessSpec,
    BSKRecorderSpec,
    BSKRunResult,
    BSKScenarioConfig,
    BSKTaskSpec,
)
from .master import SatelliteBSKSim
from .scenario_base import BSKScenarioBase, FoundationOrbitAttitudeScenario
from .scenario_factory import FOUNDATION_CAPABILITY_ID, config_from_task_spec, create_scenario

__all__ = [
    "BSK_ENGINE_SCHEMA_VERSION",
    "BSKConnectionSpec",
    "BSKEventSpec",
    "BSKExecutionPlan",
    "BSKModuleSpec",
    "BSKProcessSpec",
    "BSKRecorderSpec",
    "BSKRunResult",
    "BSKScenarioConfig",
    "BSKTaskSpec",
    "SatelliteBSKSim",
    "BSKScenarioBase",
    "FoundationOrbitAttitudeScenario",
    "FOUNDATION_CAPABILITY_ID",
    "config_from_task_spec",
    "create_scenario",
]
