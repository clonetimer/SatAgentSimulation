from __future__ import annotations

from sat_sim.form_schema import capability_form_schema


EPS = "subsystem.eps.unified_native.v1"
COMM_DATA = "subsystem.comm_data.unified_native.v1"


def test_eps_guided_event_catalog_exposes_only_verified_aggregate_scenarios() -> None:
    schema = capability_form_schema(EPS)
    faults = {item["effect"]: item for item in schema["event_catalog"]["faults"]}
    degradations = {item["effect"]: item for item in schema["event_catalog"]["degradations"]}

    assert set(faults) == {"generation_storage_distribution_failure"}
    assert set(degradations) == {"energy_margin_degradation"}
    assert schema["event_catalog"]["constraints"] == []

    fault = faults["generation_storage_distribution_failure"]
    assert fault["magnitude_applicable"] is False
    assert "二值保护动作" in fault["instructions"]
    assert "eps.payload_load_enabled_w" in fault["evidence_fields"]
    assert "eps.nominal_solar_power_w" in fault["evidence_fields"]
    assert "magnitude" not in fault["default_event"]

    degradation = degradations["energy_margin_degradation"]
    assert degradation["magnitude_applicable"] is True
    assert degradation["default_event"]["magnitude"] == 0.2
    assert "eps.nominal_battery_capacity_j" in degradation["evidence_fields"]
    assert "eps.nominal_solar_power_w" in degradation["evidence_fields"]


def test_comm_data_guided_event_catalog_exposes_only_transmitter_rate_effects() -> None:
    schema = capability_form_schema(COMM_DATA)
    faults = {item["effect"]: item for item in schema["event_catalog"]["faults"]}
    degradations = {item["effect"]: item for item in schema["event_catalog"]["degradations"]}

    assert set(faults) == {"transmitter_rate_loss"}
    assert set(degradations) == {"transmitter_rate_degradation"}
    assert schema["event_catalog"]["constraints"] == []

    fault = faults["transmitter_rate_loss"]
    assert fault["magnitude_applicable"] is True
    assert fault["default_event"]["magnitude"] == 1.0
    assert "nodeBaudRate" in fault["instructions"]
    assert "comm_data.transmitter_storage_node_rate_abs_bps" in fault["evidence_fields"]

    degradation = degradations["transmitter_rate_degradation"]
    assert degradation["magnitude_applicable"] is True
    assert degradation["default_event"]["magnitude"] == 0.2
    assert "nodeBaudRate" in degradation["instructions"]
    assert "comm_data.nominal_transmitter_storage_node_baud_bps" in degradation["evidence_fields"]
