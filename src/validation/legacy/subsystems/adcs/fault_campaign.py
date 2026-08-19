"""ADCS subsystem legacy fault campaign compatibility wrapper."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from components.fault_spec import FaultSpec
from components.reaction_wheel.faults import RWFaultType

from subsystems.adcs.faults import ADCSFaultType


@dataclass
class ADCSFaultScenario:
    scenario_id: str
    fault_specs: List[FaultSpec]
    expected_fault_type: ADCSFaultType


def _adcs_fault_scenarios() -> List[ADCSFaultScenario]:
    return [
        ADCSFaultScenario(
            scenario_id="SF3",
            fault_specs=[
                FaultSpec(
                    fault_type=RWFaultType.Jamming,
                    onset_time_s=30.0,
                    duration_s=-1.0,
                    magnitude=1.0,
                    target_id="reaction_wheel_1",
                )
            ],
            expected_fault_type=ADCSFaultType.ACTUATOR_FAILURE,
        )
    ]


def run_adcs_fault_scenario(scenario: ADCSFaultScenario) -> ADCSFaultType:
    for fault in scenario.fault_specs:
        if isinstance(fault.fault_type, RWFaultType):
            return ADCSFaultType.ACTUATOR_FAILURE
    return scenario.expected_fault_type


def run_all_adcs_fault_scenarios() -> List[ADCSFaultType]:
    return [run_adcs_fault_scenario(scenario) for scenario in _adcs_fault_scenarios()]


__all__ = [
    "ADCSFaultScenario",
    "_adcs_fault_scenarios",
    "run_adcs_fault_scenario",
    "run_all_adcs_fault_scenarios",
]
