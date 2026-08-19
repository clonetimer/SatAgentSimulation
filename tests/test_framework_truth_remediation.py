from __future__ import annotations

import copy
from pathlib import Path

import pytest

from sat_sim.adapter_base import SimulationResult
from sat_sim.adapters.whole_spacecraft_bsksim_coupled import WholeSpacecraftBSKSimCoupledAdapter
from sat_sim.adapters.whole_spacecraft_bsksim_foundation import WholeSpacecraftBSKSimFoundationAdapter
from sat_sim.bsk_engine.types import BSKEventSpec
from sat_sim.capability_registry import validate_spec_against_capability
from sat_sim.experiments.basilisk_mc_adapter import (
    build_basilisk_mc_plan,
    execute_basilisk_controller_variants,
)
from sat_sim.experiments.monte_carlo_engine import expand_monte_carlo
from sat_sim.fault_environment.fault_episode import build_fault_episode
from sat_sim.modifiers import apply_modifiers_to_result
from sat_sim.scenario_templates import instantiate_scenario_template


def _whole_spec(duration_s: float = 300.0) -> dict:
    return {
        "schema_version": "1.0.0",
        "task_id": "v0547_whole_truth",
        "task_type": "whole_spacecraft",
        "capability_id": "whole_spacecraft.bsksim_coupled.v1",
        "model": {
            "capability_id": "whole_spacecraft.bsksim_coupled.v1",
            "target": {"level": "whole_spacecraft", "name": "project_coupled", "mode": "nominal"},
        },
        "simulation": {"duration_s": duration_s, "step_s": 1.0, "sample_s": 10.0, "backend": "python"},
        "target": {"level": "whole_spacecraft", "name": "project_coupled", "mode": "nominal"},
        "parameters": {
            "values": {
                "initial_soc": 0.62,
                "initial_orbit_phase_rad": 6.2,
                "eclipse_fraction": 0.35,
                "solar_power_max_w": 95.0,
                "payload_data_rate_bps": 2.5e6,
                "downlink_rate_max_bps": 1.5e6,
            }
        },
        "outputs": {"plots": ["eps.battery_soc", "thermal.bus_temp_c", "data.storage_bits"]},
        "assurance": {"allow_proxy": True},
    }


def _event_spec(effect: str, category: str, parameters: dict | None = None) -> dict:
    spec = _whole_spec()
    bucket = {"fault": "faults", "degradation": "degradations", "constraint": "constraints"}[category]
    spec["events"] = {"faults": [], "degradations": [], "constraints": []}
    spec["events"][bucket] = [{
        "id": f"event_{effect}",
        "effect": effect,
        "target": "whole_spacecraft",
        "start_s": 50.0,
        "end_s": 200.0,
        "parameters": parameters or {},
    }]
    return spec


def _active_rows(result: SimulationResult) -> list[dict]:
    return [row for row in result.trace_rows if 50.0 <= float(row["time_s"]) <= 200.0]


def test_native_foundation_reports_instantiated_connected_recorded_runtime() -> None:
    spec = instantiate_scenario_template("bsksim_foundation_orbit_attitude", task_id="v0547_native_foundation")
    spec["simulation"].update({"duration_s": 5.0, "sample_s": 1.0})
    result = WholeSpacecraftBSKSimFoundationAdapter().run(spec)
    manifest = result.metadata["runtime_manifest"]
    assert result.summary["engine"] == "basilisk_native_foundation"
    assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded"
    assert result.summary["instantiated_module_count"] == 7
    assert result.summary["connected_message_count"] >= 7
    assert result.summary["recorder_count"] == 3
    assert "spacecraft" in manifest["instantiated_model_tags"]
    assert "spacecraft.scStateOutMsg" in manifest["recorded_sources"]
    assert result.trace_rows and "orbit.radius_m" in result.trace_rows[0]


def test_backend_and_proxy_contracts_are_enforced_on_canonical_model_capability_id() -> None:
    native = instantiate_scenario_template("bsksim_foundation_orbit_attitude", task_id="v0547_backend_native")
    assert not [issue for issue in validate_spec_against_capability(native) if issue.severity == "error"]
    native["simulation"]["backend"] = "python"
    assert "BACKEND_CAPABILITY_MISMATCH" in {issue.code for issue in validate_spec_against_capability(native)}

    project = instantiate_scenario_template("bsksim_whole_spacecraft_strong_coupling", task_id="v0547_backend_project")
    assert not [issue for issue in validate_spec_against_capability(project) if issue.severity == "error"]
    project["assurance"]["allow_proxy"] = False
    assert "CAPABILITY_PROXY_NOT_ALLOWED" in {issue.code for issue in validate_spec_against_capability(project)}


def test_fault_window_without_expected_observables_is_not_physical_evidence() -> None:
    event = BSKEventSpec("rw_fault", "fault", "adcs_rw_jamming", "reaction_wheel", 10.0, 20.0)
    episode = build_fault_episode(event, ({"time_s": 0.0, "x": 1.0}, {"time_s": 15.0, "x": 1.0}))
    assert episode.evidence_status == "missing_observables"
    assert episode.evidence_summary["physical_effect_verified"] is False


def test_timeline_marker_is_explicitly_not_physical_evidence() -> None:
    event = BSKEventSpec("marker", "fault", "bsksim_fault_marker", "spacecraft", 10.0, 20.0)
    episode = build_fault_episode(event, (
        {"time_s": 0.0, "label.fault_active": False},
        {"time_s": 15.0, "label.fault_active": True},
    ))
    assert episode.evidence_status == "observed_timeline_only"
    assert episode.evidence_summary["physical_effect_verified"] is False


def test_generic_modifier_default_is_audit_only_and_does_not_mutate_physics() -> None:
    original = SimulationResult(
        summary={"status": "complete"},
        trace_rows=(
            {"time_s": 0.0, "eps.battery_soc": 0.8},
            {"time_s": 20.0, "eps.battery_soc": 0.7},
        ),
    )
    spec = {
        "events": {"faults": [{
            "id": "capacity",
            "effect": "capacity_drop",
            "target": "eps.battery",
            "start_s": 10.0,
            "parameters": {"remaining_capacity_ratio": 0.5},
        }]}
    }
    result = apply_modifiers_to_result(original, spec)
    assert [row["eps.battery_soc"] for row in result.trace_rows] == [0.8, 0.7]
    assert result.trace_rows[1]["label.modifier_active"] is True
    assert result.summary["generic_modifier_physics_verified"] is False
    assert result.metadata["modifier_evidence_classification"] == "schedule_annotation_only"


def test_default_whole_project_engine_covers_coupling_and_conservation() -> None:
    result = WholeSpacecraftBSKSimCoupledAdapter().run(_whole_spec())
    integrity = result.summary["coupling_integrity"]
    assert result.summary["backend_type"] == "project_coupled_equation_engine"
    assert result.metadata["model_source_boundary"]["uses_basilisk_runtime"] is False
    assert integrity["passed"] is True
    assert integrity["checks"]["coverage_sun_and_eclipse"] is True
    assert integrity["checks"]["coverage_payload_and_downlink"] is True
    assert integrity["checks"]["data_balance"] is True
    assert integrity["checks"]["energy_balance"] is True


@pytest.mark.parametrize(
    ("effect", "category", "parameters", "field", "predicate"),
    [
        ("payload_instrument_off", "fault", {}, "payload.generated_data_bits", lambda rows: all(float(r["payload.generated_data_bits"]) == 0.0 for r in rows)),
        ("comm_data_downlink_link_loss", "fault", {}, "comm.downlinked_bits", lambda rows: all(float(r["comm.downlinked_bits"]) == 0.0 for r in rows)),
        ("eps_battery_capacity_loss", "fault", {"remaining_capacity_ratio": 0.5}, "eps.effective_battery_capacity_wh", lambda rows: all(float(r["eps.effective_battery_capacity_wh"]) < 900.0 for r in rows)),
        ("thermal_radiator_rejection_loss", "degradation", {"remaining_rejection_ratio": 0.4}, "thermal.effective_radiator_coeff_w_per_k", lambda rows: all(float(r["thermal.effective_radiator_coeff_w_per_k"]) < 1.8 for r in rows)),
        ("solar_panel_efficiency_loss", "degradation", {"remaining_efficiency_ratio": 0.5}, "eps.solar_efficiency_scale", lambda rows: all(float(r["eps.solar_efficiency_scale"]) == 0.5 for r in rows)),
        ("power_safe_mode_threshold", "constraint", {"soc_threshold": 0.9}, "payload.active", lambda rows: all(float(r["payload.active"]) == 0.0 and float(r["comm.downlinked_bits"]) == 0.0 for r in rows)),
        ("adcs_rw_jamming", "fault", {"pointing_error_multiplier": 1.5}, "adcs.pointing_error_deg", lambda rows: all("adcs_rw_jamming" in str(r["event.active_effects"]) for r in rows)),
    ],
)
def test_whole_events_modify_runtime_state_before_integration(
    effect: str,
    category: str,
    parameters: dict,
    field: str,
    predicate,
) -> None:
    result = WholeSpacecraftBSKSimCoupledAdapter().run(_event_spec(effect, category, parameters))
    active = _active_rows(result)
    assert active and all(field in row for row in active)
    assert predicate(active)
    episode = result.metadata["fault_environment"]["episodes"][0]
    assert episode["evidence_status"] == "observed"
    assert episode["evidence_summary"]["physical_effect_verified"] is True


def test_monte_carlo_has_reproducible_distinct_per_run_simulation_seeds() -> None:
    spec = instantiate_scenario_template("bsksim_foundation_orbit_attitude", task_id="v0547_mc_seed")
    dispersions = {"parameters.values.initial_pointing_error_deg": {"distribution": "uniform", "min": 2.0, "max": 8.0}}
    first = expand_monte_carlo(spec, dispersions, sample_count=4, seed=77)
    second = expand_monte_carlo(spec, dispersions, sample_count=4, seed=77)
    seeds = [item["simulation_seed"] for item in first]
    assert seeds == [item["simulation_seed"] for item in second]
    assert len(set(seeds)) == 4
    assert all(seed != 0 for seed in seeds)
    assert [item["task_spec"]["simulation"]["random_seed"] for item in first] == seeds


def test_official_controller_executes_only_verified_native_capability(tmp_path: Path) -> None:
    spec = instantiate_scenario_template("bsksim_foundation_orbit_attitude", task_id="v0547_mc_native")
    spec["simulation"].update({"duration_s": 3.0, "step_s": 1.0, "sample_s": 1.0})
    dispersions = {"parameters.values.initial_pointing_error_deg": {"distribution": "uniform", "min": 3.0, "max": 6.0}}
    variants = expand_monte_carlo(spec, dispersions, sample_count=2, seed=19)
    plan = build_basilisk_mc_plan(spec, dispersions, sample_count=2, seed=19)
    assert plan.eligible is True
    report = execute_basilisk_controller_variants(spec, variants, archive_dir=tmp_path / "native_mc", thread_count=1)
    assert report["controller_invoked"] is True
    assert report["controller"] == "Basilisk.utilities.MonteCarlo.Controller.Controller"
    assert report["failure_count"] == 0
    assert len(report["runs"]) == 2
    assert report["output_statistics"]["max_pointing_error_deg"]["count"] == 2
    requested = [run["parameters"]["parameters.values.initial_pointing_error_deg"] for run in report["runs"]]
    recorded = [run["summary"]["initial_recorded_pointing_error_deg"] for run in report["runs"]]
    assert len(set(requested)) == 2
    assert len(set(recorded)) == 2
    assert recorded == pytest.approx(requested, abs=1e-9)
    application = report["parameter_application_checks"]["parameters.values.initial_pointing_error_deg"]
    assert application["applied"] is True
    assert application["sensitivity_observed"] is True
    assert all(Path(run["controller_parameter_file"]).exists() for run in report["runs"])
    assert all(Path(run["controller_retention_file"]).exists() for run in report["runs"])

    project = _whole_spec()
    project_plan = build_basilisk_mc_plan(project, {"simulation.duration_s": {"distribution": "uniform", "min": 10, "max": 20}}, sample_count=2, seed=1)
    assert project_plan.eligible is False
    with pytest.raises(ValueError, match="not available"):
        execute_basilisk_controller_variants(project, variants, archive_dir=tmp_path / "should_refuse")
