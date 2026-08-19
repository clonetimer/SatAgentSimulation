from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from sat_sim.adapters.whole_spacecraft_composite_digital_twin import _build_configs
from sat_sim.coupling_validation_suite import _case_from_mapping
from whole_spacecraft.builder import build_whole_spacecraft_bus
from whole_spacecraft.schemas import WholeSpacecraftTraceRow
from subsystems.adcs.schemas import RwOnlyClosedLoopConfig


def test_v0574_matrix_requires_all_nineteen_active_runtime_couplings() -> None:
    matrix = json.loads(
        Path("configs/verification/whole_spacecraft_coupling_causality_v3.json")
        .read_text(encoding="utf-8")
    )
    assert matrix["schema_version"].startswith("v0574")
    assert len(matrix["cases"]) == 17
    assert len(matrix["required_runtime_couplings"]) == 19
    assert len(set(matrix["required_runtime_couplings"])) == 19
    cases = {item["case_id"]: item for item in matrix["cases"]}
    assert "spacecraft_state_to_eclipse_and_thermal_shadow" in cases
    assert "ground_access_geometry_to_rf_link_budget" in cases
    assert "gravity_to_spacecraft_orbit_dynamics" in cases
    assert "propulsion_activity_to_power_heat_and_orbit" in cases
    assert "eps_pdu_to_thermal_heaters" in cases
    assert "thermal_heaters_to_eps_load" in cases


def test_case_parser_preserves_setup_patches_for_case_specific_baseline() -> None:
    payload = {
        "case_id": "heater_pair",
        "description": "heater",
        "setup_patches": [
            {"path": "target.mode", "value": "fault"},
            {"path": "modifiers.faults", "value": [{"fault_type": "heater_stuck_on"}]},
        ],
        "patch": {"path": "parameters.initial_soc", "value": 0.15},
        "expectations": [
            {
                "coupling_id": "eps_pdu_to_heater_enable",
                "source_field": "pdu_heater_enabled",
                "response_field": "heater_active_count",
            }
        ],
    }
    case = _case_from_mapping(payload)
    assert case.setup_patches[0] == ("target.mode", "fault")
    assert case.setup_patches[1][0] == "modifiers.faults"
    assert case.patch_path == "parameters.initial_soc"


def test_composite_adapter_consumes_orbit_and_propulsion_causal_parameters() -> None:
    baseline = yaml.safe_load(
        Path("configs/verification/whole_spacecraft_coupling_baseline.yaml")
        .read_text(encoding="utf-8")
    )
    baseline["parameters"]["initial_orbit_radius_m"] = 8_000_000.0
    baseline["parameters"]["initial_orbit_phase_deg"] = 180.0
    baseline["parameters"]["propulsion_on_time_s"] = [20.0, 20.0]
    _run, structure = _build_configs(baseline)
    assert structure.initial_orbit_radius_m == 8_000_000.0
    assert structure.initial_orbit_phase_deg == 180.0
    assert structure.propulsion_on_time_s == (20.0, 20.0)


def test_whole_spacecraft_bus_applies_orbit_phase_to_initial_state() -> None:
    pytest.importorskip("Basilisk")
    bus = build_whole_spacecraft_bus(
        RwOnlyClosedLoopConfig(duration_s=0.0),
        initial_orbit_radius_m=7_000_000.0,
        initial_orbit_phase_deg=180.0,
    )
    position = [float(row[0]) for row in bus.hub.r_CN_NInit]
    velocity = [float(row[0]) for row in bus.hub.v_CN_NInit]
    assert position[0] == pytest.approx(-7_000_000.0, abs=1e-6)
    assert abs(position[1]) < 1e-6
    assert velocity[1] < 0.0


def test_trace_schema_contains_runtime_observables_for_all_remaining_couplings() -> None:
    fields = set(WholeSpacecraftTraceRow.__dataclass_fields__)
    required = {
        "adcs_control_power_w",
        "propulsion_cumulative_energy_j",
        "heater_eps_load_w",
        "heater_active_count",
        "pdu_heater_enabled",
        "eclipse_shadow_factor",
        "ground_slant_range_m",
        "rf_link_distance_m",
        "orbit_radius_m",
        "orbit_speed_m_s",
        "spacecraft_position_x_m",
        "thermal_adcs_temp_c",
        "thermal_structure_temp_c",
        "thermal_solar_panel_temp_c",
        "gravity_coupling_enabled",
        "propulsion_effector_coupling_enabled",
        "propellant_used_kg",
    }
    assert required <= fields
