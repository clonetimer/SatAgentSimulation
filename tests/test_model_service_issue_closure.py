from __future__ import annotations

import json
from pathlib import Path

from sat_sim.adapters.component_reaction_wheel import ReactionWheelAdapter
from sat_sim.adapters.subsystem_adcs_fidelity import AdcsFidelityAdapter
from sat_sim.form_schema import capability_form_schema, output_label
from sat_sim.reporting import generate_run_report


def test_adcs_form_exposes_chinese_fault_and_degradation_catalog() -> None:
    schema = capability_form_schema("subsystem.adcs_fidelity.v1")
    faults = schema["event_catalog"]["faults"]
    degradations = schema["event_catalog"]["degradations"]
    constraints = schema["event_catalog"]["constraints"]
    assert {item["effect"] for item in faults} >= {
        "adcs_rw_jamming", "adcs_rw_motor_failure", "adcs_gyro_bias_step"
    }
    assert "adcs_reaction_wheel_speed_limit" not in {item["effect"] for item in faults}
    assert "adcs_reaction_wheel_speed_limit" in {item["effect"] for item in constraints}
    assert {item["effect"] for item in degradations} >= {
        "adcs_gyro_noise_increase", "adcs_rw_friction_increase"
    }
    assert all(item.get("label") and not item["label"].startswith("adcs_") for item in faults + degradations + constraints)


def test_reaction_wheel_modifier_fault_is_applied_by_physical_adapter() -> None:
    spec = {
        "capability_id": "component.reaction_wheel.v1",
        "task_id": "rw_fault_test",
        "task_type": "component",
        "target": {"level": "component", "name": "reaction_wheel", "mode": "fault"},
        "simulation": {"duration_s": 6.0, "sample_s": 1.0},
        "parameters": {"num_wheels": 1, "initial_wheel_speeds_rad_s": [100.0], "command_torque_nm": [0.01]},
        "faults": [],
        "modifiers": {
            "faults": [{
                "modifier_id": "jam_1", "target": "reaction_wheel_0", "fault_type": "rw_jamming",
                "onset_time_s": 2.0, "duration_s": 2.0, "severity": 1.0, "parameters": {},
            }],
            "degradations": [],
        },
    }
    result = ReactionWheelAdapter().run(spec)
    active = [row for row in result.trace_rows if 2.0 <= float(row["time_s"]) < 4.0]
    assert active
    assert all(row["label.fault_active"] is True for row in active)
    assert any(float(row["adcs.reaction_wheel.speed_rad_s_0"]) == 0.0 for row in active)
    assert result.summary["events"]["fault_count"] == 1
    assert result.metadata["modifiers_applied_by_adapter"] is True


def test_adcs_runtime_fault_changes_closed_loop_trace() -> None:
    spec = {
        "capability_id": "subsystem.adcs_fidelity.v1",
        "task_id": "adcs_fault_test",
        "task_type": "subsystem",
        "target": {"level": "subsystem", "name": "adcs", "mode": "fault"},
        "simulation": {"duration_s": 12.0, "sample_s": 2.0, "solver": {"step_s": 0.25}},
        "parameters": {"initial_attitude_error_deg": 12.0},
        "modifiers": {
            "faults": [{
                "modifier_id": "jam_1", "target": "adcs.reaction_wheel.0", "fault_type": "adcs_rw_jamming",
                "onset_time_s": 4.0, "duration_s": 4.0, "severity": 1.0, "parameters": {"wheel_index": 0},
            }],
            "degradations": [],
        },
    }
    result = AdcsFidelityAdapter().run(spec)
    active = [row for row in result.trace_rows if 4.0 <= float(row["time_s"]) < 8.0]
    assert active
    assert all(row["label.fault_active"] is True for row in active)
    assert all(float(row["adcs.control.applied_torque_nm_0"]) == 0.0 for row in active)
    assert result.summary["events"]["fault_count"] == 1
    assert result.summary["events"]["active_sample_count"] > 0


def test_unknown_output_title_does_not_translate_symbolic_unit_words() -> None:
    assert output_label("custom.velocity_rad_s") == "速度"
    assert "弧度" not in output_label("custom.velocity_rad_s")
    assert "时间" not in output_label("custom.velocity_rad_s")
    assert output_label("custom.temperature_deg") == "温度"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_report_localizes_core_metrics_and_reads_nested_dependency_versions(tmp_path: Path) -> None:
    root = tmp_path / "run_001"
    _write_json(root / "input/task_spec.json", {
        "task": {"task_id": "t1", "name": "姿态控制测试"},
        "simulation": {"duration_s": 20},
        "model": {"capability_id": "subsystem.adcs_fidelity.v1"},
    })
    _write_json(root / "run_record.json", {"status": "SUCCEEDED"})
    _write_json(root / "validation/validation_outcome.json", {"result": "PASS"})
    _write_json(root / "validation/claim_report.json", {"allowed_claims": ["simulation_execution_completed"]})
    _write_json(root / "results/metrics.json", {"metrics": {
        "qoi.adcs.final_pointing_error_deg": 0.25,
        "fidelity_level": "medium",
        "adapter_metadata.adcs1_fidelity.config.closed_loop.duration_s": 20,
    }})
    _write_json(root / "results/events.json", {"declared": []})
    _write_json(root / "results/assertions.json", {"status": "PASS", "results": []})
    _write_json(root / "results/plot_manifest.json", {"series": []})
    _write_json(root / "runtime/environment.json", {"python": {"version": "3.12"}, "platform": {"platform": "Windows"}, "primary_capability_id": "subsystem.adcs_fidelity.v1"})
    _write_json(root / "runtime/dependency_versions.json", {"dependencies": {"Basilisk": "2.11.0", "satellite-simulation-platform": "0.5.3.6"}})
    generate_run_report(root)
    html = (root / "results/report.html").read_text(encoding="utf-8")
    assert "最终姿态指向误差" in html
    assert "模型保真度等级" in html
    assert "中等保真度" in html
    assert "adapter_metadata" not in html
    assert "Basilisk</th><td>2.11.0" in html
    assert "包版本</th><td>0.5.3.6" in html
    assert "仿真执行已完成" in html
