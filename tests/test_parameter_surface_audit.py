from __future__ import annotations

from sat_sim.validation.parameter_surface_audit import (
    build_parameter_surface_audit, literal_runtime_keys, render_parameter_surface_markdown,
)


def test_source_lookup_is_ast_based_not_textual_or_physical_proof() -> None:
    source = '''
# values.get("comment_only")
doc = "values.get('string_only')"
def build(values, index):
    a = values.get("inertia_kg_m2", 1)
    b = values["battery_capacity_wh"]
    c = values.get(f"rw_initial_speed_rad_s_{index}", 0)
    return a, b, c
'''
    literal, prefix = literal_runtime_keys(source)
    assert literal == {"inertia_kg_m2", "battery_capacity_wh"}
    assert prefix == {"rw_initial_speed_rad_s_"}
    assert "comment_only" not in literal
    assert "string_only" not in literal


def test_each_active_component_and_subsystem_parameter_is_in_generated_form() -> None:
    report = build_parameter_surface_audit()
    assert report["schema_version"] == "sat-sim.parameter-surface-traceability.v1"
    assert report["status"] == "PASS", report["issues"]
    assert report["component_count"] == 24
    assert report["subsystem_count"] == 18
    assert report["selectable_subsystem_count"] == 12
    assert report["selectable_component_count"] == 24
    assert report["capability_count"] == 43  # 24 + 18 + integrated spacecraft
    assert report["form_issue_count"] == 0
    inactive = [c for c in report["capabilities"] if c["level"] == "subsystem" and not c["user_selectable"]]
    assert len(inactive) == 6
    assert all(c["lifecycle_status"] != "active" or not c["user_selectable"] for c in inactive)
    for item in report["capabilities"]:
        assert item["registered_parameter_count"] == item["form_parameter_count"]
        for entry in item["parameters"]:
            assert entry["form_path"] == "parameters.values." + entry["key"]
            assert not entry["physical_perturbation_verified_by_this_audit"]


def test_native_and_continuous_gaps_are_exposed_not_misreported_as_passed_physics() -> None:
    report = build_parameter_surface_audit()
    whole = report["whole_spacecraft"]
    assert report["physics_validated_by_this_audit"] is False
    assert report["source_lookup_is_not_physical_proof"] is True
    assert whole["continuous_initial_field_count"] == 60
    assert len(whole["continuous_groups"]["propulsion"]) == 9
    assert len(whole["continuous_groups"]["thermal"]) == 5
    assert whole["registered_parameter_count"] == 33
    assert "rw_configuration" in whole["native_source_keys_missing_whole_registry"]
    assert "gyro_base_noise_std_rad_s" in whole["native_source_keys_missing_whole_registry"]
    assert "adcs" in whole["continuous_groups"]
    assert len(whole["continuous_groups"]["adcs"]) == 32
    assert len(whole["continuous_groups"]["orbit"]) == 5
    assert "spacecraft_mass_kg" in whole["continuous_groups"]["orbit"]
    assert "sun_sensor_fov_deg" in whole["continuous_groups"]["adcs"]
    assert "rw_initial_speed_rad_s_0" not in whole["continuous_fields_without_source_lookup"]
    assert whole["native_keys_not_exposed_in_continuous_initial"]
    markdown = render_parameter_surface_markdown(report)
    assert "不代表物理生效验收通过" in markdown
    assert "rw_configuration" in markdown


def test_registry_parameter_dropped_from_form_is_a_blocking_coverage_failure(monkeypatch) -> None:
    from sat_sim.validation import parameter_surface_audit as audit

    actual = audit.capability_form_schema

    def broken(capability_id):
        value = actual(capability_id)
        if capability_id == audit.WHOLE_ID:
            value["fields"] = [f for f in value["fields"]
                               if f["path"] != "parameters.values.controller_k"]
        return value

    monkeypatch.setattr(audit, "capability_form_schema", broken)
    report = audit.build_parameter_surface_audit()
    assert report["status"] == "FAIL"
    assert {"capability_id": audit.WHOLE_ID, "parameter": "controller_k",
            "code": "REGISTRY_PARAMETER_NOT_IN_FORM"} in report["issues"]
