"""Integration-layer validation profiles and wiring helpers."""
from .whole_spacecraft_links import (
    WholeSpacecraftIntegrationContract,
    build_whole_spacecraft_integration_manifest,
    required_whole_spacecraft_contracts,
    wire_comm_transmitter_storage_drain,
    wire_payload_to_comm_data,
    wire_payload_to_comm_storage,
)

__all__ = [
    "WholeSpacecraftIntegrationContract",
    "build_whole_spacecraft_integration_manifest",
    "required_whole_spacecraft_contracts",
    "wire_comm_transmitter_storage_drain",
    "wire_payload_to_comm_data",
    "wire_payload_to_comm_storage",
]
