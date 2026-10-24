"""Prevent falsely claiming 3RW/4RW custom ≡ whole-spacecraft HR16."""
from __future__ import annotations

from sat_sim.validation.rw_cross_level_semantics import build_rw_cross_level_semantics


def test_rw_mapping_has_correct_real_hardware_boundaries():
    report = build_rw_cross_level_semantics()
    assert report["structure_status"] == "PASS"
    assert report["default_component_backend"] == "project_reference"
    assert report["native_component_option_registered"] is True
    assert report["component_hardware_family"] == "rwFactory.custom"
    assert report["whole_hardware_family"] == "rwFactory.Honeywell_HR16"
    assert report["supported_native_component_counts"] == [1, 3, 4]
    assert report["native_whole_spacecraft_counts"] == [3, 4]
    assert report["physical_parity_status"] == "NOT_VERIFIED"
    assert report["physical_cross_level_parity_verified_by_this_audit"] is False
    assert report["component_native_physics_verified_by_this_audit"] is False
    assert report["reference_backend_is_basilisk"] is False


def test_same_parameter_names_do_not_claim_identical_physics():
    report = build_rw_cross_level_semantics()
    rows = {x["component_parameter"]: x for x in report["relationships"]}
    assert rows["wheel_inertia_kg_m2"]["relationship"] == "different_hardware_family"
    assert "rw_max_momentum_nms_0" in rows["wheel_inertia_kg_m2"]["whole_parameter_candidates"]
    assert rows["command_torque_nm"]["relationship"] == "control_chain_not_equivalent"
    assert rows["max_motor_torque_nm"]["relationship"] == "shared_native_attribute_different_default"
    assert rows["damping_nms"]["relationship"] == "native_cviscous_conditional"
    assert all(x["physical_parity"] == "NOT_TESTED" for x in rows.values())
    assert all(x["component_registered"] for x in rows.values())
