from __future__ import annotations

from sat_sim.verification_matrix import COMPONENTS, SUBSYSTEMS, build_verification_matrix, matrix_stats, run_verification_item


def test_matrix_has_granular_component_coverage():
    items = build_verification_matrix()
    assert len(items) >= 320
    ids = [item.test_id for item in items]
    assert len(ids) == len(set(ids))
    component_items = [item for item in items if item.level == "component"]
    assert len(component_items) >= 300
    for component in COMPONENTS:
        c_items = [item for item in component_items if item.object == component]
        found = {item.scenario_type for item in c_items}
        assert "nominal" in found
        assert "batch" in found
        assert "fault" in found
        assert "degradation" in found
        assert "combined_default_set" in found
        assert "duration_boundary" in found
        assert "negative" in found
        assert "determinism" in found
        assert all(item.command.startswith("python scripts/run_engineering_verification_matrix.py") for item in c_items)
        assert all(item.pass_rule and item.failure_rule and item.judge_parameters for item in c_items)


def test_matrix_has_subsystem_and_release_coverage():
    items = build_verification_matrix()
    for subsystem in SUBSYSTEMS:
        assert [item for item in items if item.level == "subsystem" and item.object == subsystem]
    levels = {item.level for item in items}
    assert {"environment", "static_quality", "model_contract", "calibration", "agent", "release"}.issubset(levels)
    stats = matrix_stats(items)
    assert stats["item_count"] == len(items)
    assert stats["by_level"]["component"] >= 300


def test_representative_component_items_execute():
    matrix = {item.test_id: item for item in build_verification_matrix()}
    sample_ids = [
        "CMP-NOM-REACTION-WHEEL-NOMINAL",
        "CMP-FLT-REACTION-WHEEL-BEARING-SEIZURE",
        "CMP-DEG-REACTION-WHEEL-BEARING-FRICTION-GROWTH",
        "CMP-L2-REACTION-WHEEL-COMBINED-DEFAULT-SET",
        "CMP-BND-BATTERY-LONG-DURATION-86400S",
        "CMP-NEG-BATTERY-UNSUPPORTED-WORKING-SCENARIO",
        "CMP-DET-SOLAR-PANEL-REPEAT-STABILITY",
    ]
    for test_id in sample_ids:
        report = run_verification_item(matrix[test_id])
        assert report["status"] == "PASS", report
