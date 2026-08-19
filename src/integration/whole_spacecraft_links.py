"""Whole-spacecraft integration wiring helpers.

The formal whole-spacecraft capability proof must come from the built graph and
``WholeSpacecraftCouplingConfig``/``coupling_matrix``.  This module only keeps
small Basilisk wiring helpers plus backward-compatible deprecated metadata for
older reports; it is not a source of capability truth.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WholeSpacecraftIntegrationContract:
    """Deprecated metadata shape retained for backward compatibility.

    New validation must inspect ``WholeSpacecraftGraph.coupling_matrix`` instead
    of using these report-only declarations as evidence.
    """

    name: str
    source_subsystem: str
    sink_subsystem: str
    interface: str
    support: str
    note: str


def wire_payload_to_comm_storage(instrument: Any, storage: Any) -> tuple[str, ...]:
    """Wire payload data generation into the Comm/Data storage unit."""

    storage.addDataNodeToModel(instrument.nodeDataOutMsg)
    return (
        "storage.addDataNodeToModel(instrument.nodeDataOutMsg)",
        f"instrument={getattr(instrument, 'ModelTag', type(instrument).__name__)}",
        f"storage={getattr(storage, 'ModelTag', type(storage).__name__)}",
    )


def wire_comm_transmitter_storage_drain(transmitter: Any, storage: Any) -> tuple[str, ...]:
    """Wire the transmitter to consume stored data through the storage unit."""

    transmitter.addStorageUnitToTransmitter(storage.storageUnitDataOutMsg)
    storage.addDataNodeToModel(transmitter.nodeDataOutMsg)
    return (
        "transmitter.addStorageUnitToTransmitter(storage.storageUnitDataOutMsg)",
        "storage.addDataNodeToModel(transmitter.nodeDataOutMsg)",
        f"transmitter={getattr(transmitter, 'ModelTag', type(transmitter).__name__)}",
        f"storage={getattr(storage, 'ModelTag', type(storage).__name__)}",
    )


def wire_payload_to_comm_data(instrument: Any, storage: Any, transmitter: Any) -> tuple[str, ...]:
    """Backward-compatible helper that wires both payload storage and drain links."""

    return wire_payload_to_comm_storage(instrument, storage) + wire_comm_transmitter_storage_drain(transmitter, storage)


def required_whole_spacecraft_contracts() -> tuple[WholeSpacecraftIntegrationContract, ...]:
    """Return deprecated report-only declarations.

    Kept so older imports and reports do not break.  Do not use this function
    to validate whole-spacecraft coupling capability; inspect the built graph
    coupling matrix.
    """

    deprecated = "deprecated_report_only; inspect WholeSpacecraftGraph.coupling_matrix"
    return (
        WholeSpacecraftIntegrationContract(
            name="payload_data_to_comm_storage",
            source_subsystem="payload",
            sink_subsystem="comm_data",
            interface="DataNodeUsageMsg -> SimpleStorageUnit.addDataNodeToModel",
            support=deprecated,
            note="Payload-to-storage wiring is validated from builder-created coupling records.",
        ),
        WholeSpacecraftIntegrationContract(
            name="comm_transmitter_to_storage_drain",
            source_subsystem="comm_data",
            sink_subsystem="comm_data",
            interface="SimpleTransmitter.storageUnitDataOutMsg + DataNodeUsageMsg drain",
            support=deprecated,
            note="Storage drain wiring is validated from builder-created coupling records.",
        ),
        WholeSpacecraftIntegrationContract(
            name="eps_loads_to_thermal_heat",
            source_subsystem="eps",
            sink_subsystem="thermal",
            interface="PowerNodeUsageMsg -> thermal scheduled/network heat inputs",
            support=deprecated,
            note="EPS-to-thermal bridge evidence is in coupling_matrix.",
        ),
        WholeSpacecraftIntegrationContract(
            name="thermal_heater_to_eps_load",
            source_subsystem="thermal",
            sink_subsystem="eps",
            interface="HeaterPowerFeedback.PowerNodeUsageMsg -> SimpleBattery.addPowerNodeToModel",
            support=deprecated,
            note="Thermal heater feedback evidence is in coupling_matrix.",
        ),
        WholeSpacecraftIntegrationContract(
            name="adcs_spacecraft_to_orbit_environment",
            source_subsystem="spacecraft_dynamics",
            sink_subsystem="orbit_environment",
            interface="SCStatesMsg -> eclipse/magnetic environment modules",
            support=deprecated,
            note="Spacecraft-state-to-environment evidence is in coupling_matrix.",
        ),
        WholeSpacecraftIntegrationContract(
            name="propulsion_effector_to_spacecraft",
            source_subsystem="propulsion",
            sink_subsystem="spacecraft_dynamics",
            interface="ThrusterDynamicEffector/FuelTank -> spacecraft dynamic/state effector",
            support=deprecated,
            note="Propulsion attachment evidence is in coupling_matrix.",
        ),
        WholeSpacecraftIntegrationContract(
            name="adcs_pointing_to_payload_comm_gate",
            source_subsystem="adcs",
            sink_subsystem="payload,comm_data",
            interface="AttGuidMsg + MissionGate -> instrument/transmitter baud commands",
            support=deprecated,
            note="MissionGate bridge evidence is in coupling_matrix.",
        ),
    )


def build_whole_spacecraft_integration_manifest() -> dict[str, Any]:
    """Return a deprecated compatibility manifest.

    New code should not call this from the builder.  Reports should use
    ``WholeSpacecraftGraph.coupling_matrix`` because it is produced only after
    schema-controlled links are actually built.
    """

    contracts = required_whole_spacecraft_contracts()
    return {
        "deprecated_report_only": True,
        "source_of_truth": "WholeSpacecraftGraph.coupling_matrix",
        "contract_count": len(contracts),
        "contracts": [c.__dict__ for c in contracts],
    }


__all__ = [
    "WholeSpacecraftIntegrationContract",
    "required_whole_spacecraft_contracts",
    "wire_payload_to_comm_storage",
    "wire_comm_transmitter_storage_drain",
    "wire_payload_to_comm_data",
    "build_whole_spacecraft_integration_manifest",
]
