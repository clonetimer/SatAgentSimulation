from __future__ import annotations

import io
import math
import urllib.error
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from sat_sim.adcs.closed_loop import axis_angle_to_quaternion, quaternion_error
from sat_sim.api import create_app
from sat_sim.form_schema import capability_form_schema
from sat_sim.model_providers import ModelProviderProfile, ModelProviderRegistry
from sat_sim.scenario_templates import list_scenario_templates
from sat_sim.task_models import CanonicalTaskSpec


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=False,
        auth_mode="disabled",
    ))


def test_scenario_templates_cover_all_levels_and_filter_by_level_and_capability() -> None:
    catalog = list_scenario_templates()
    assert catalog["count"] == len(catalog["templates"])
    assert catalog["count"] >= 45
    assert len({item["template_id"] for item in catalog["templates"]}) == catalog["count"]
    assert {item["level"] for item in catalog["templates"]} == {
        "component", "subsystem", "orbit_environment", "whole_spacecraft"
    }
    component = list_scenario_templates(level="component")
    assert component["count"] == 24
    assert all(item["level"] == "component" for item in component["templates"])
    adcs = list_scenario_templates(capability_id="subsystem.adcs_fidelity.v1")
    assert adcs["count"] == 1
    assert any(item["name"] == "ADCS 姿态收敛" for item in adcs["templates"])
    imu = list_scenario_templates(object_id="component.imu")
    assert imu["count"] == 1
    assert imu["templates"][0]["execution_scope"] == "direct"


def test_schema_fields_and_outputs_are_self_describing_and_time_grid_is_declared() -> None:
    schema = capability_form_schema("subsystem.adcs_fidelity.v1")
    assert schema["fields"]
    assert all(field.get("label") and field.get("description") for field in schema["fields"])
    assert all(option.get("label") and option.get("description") for option in schema["outputs"]["summary_options"])
    assert all(option.get("label") and option.get("description") for option in schema["outputs"]["trace_options"])
    rule = next(rule for rule in schema["cross_field_rules"] if rule["rule_id"] == "time_grid_order")
    assert rule["expression"] == "simulation.step_s <= simulation.sample_s <= simulation.duration_s"
    labels = {field["path"]: field["label"] for field in schema["fields"]}
    assert labels["parameters.values.gyro_noise_std_deg_s"] == "陀螺噪声标准差"


def test_sample_interval_cannot_be_smaller_than_step_interval() -> None:
    spec = capability_form_schema("subsystem.adcs_fidelity.v1")["default_form"]
    spec["simulation"]["step_s"] = 2.0
    spec["simulation"]["sample_s"] = 1.0
    spec.pop("capability_id", None)
    with pytest.raises(ValidationError, match="step_s must not exceed sample_s"):
        CanonicalTaskSpec.model_validate(spec)


def test_quaternion_error_uses_hamilton_product_and_shortest_equivalent_sign() -> None:
    identity = (1.0, 0.0, 0.0, 0.0)
    qz_90 = axis_angle_to_quaternion((0.0, 0.0, 1.0), math.pi / 2.0)
    err = quaternion_error(qz_90, identity)
    assert err == pytest.approx(qz_90, abs=1e-12)
    assert quaternion_error(tuple(-x for x in qz_90), identity) == pytest.approx(qz_90, abs=1e-12)
    assert quaternion_error(qz_90, qz_90) == pytest.approx(identity, abs=1e-12)


def test_reaction_wheel_natural_language_target_speed_has_no_hallucinated_fault(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post("/tasks/parse", json={
            "input_kind": "natural_language",
            "request_text": "创建一个反作用轮，轮速初始值为100 r/min，在力矩作用下，提升到150 r/min",
            "backend": "template",
            "local_backend": "template",
            "routing_mode": "local",
            "compile_if_valid": True,
        })
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["ok"] is True
    spec = result["task_spec"]
    assert spec["model"]["capability_id"] == "component.reaction_wheel.v1"
    assert spec["events"]["faults"] == []
    assert spec["events"]["degradations"] == []
    assert spec["parameters"]["values"]["initial_wheel_speeds_rad_s"] == pytest.approx(100 * 2 * math.pi / 60)
    assert spec["parameters"]["values"]["command_torque_nm"] > 0


def test_lmstudio_probe_retries_without_response_format(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_http_json(url: str, **kwargs):
        body = dict(kwargs.get("body") or {})
        calls.append(body)
        if "response_format" in body:
            raise urllib.error.HTTPError(
                url, 400, "response_format unsupported", {},
                io.BytesIO(b'{"error":"unsupported response_format"}'),
            )
        return 200, {
            "choices": [{"message": {"content": '{"status":"ok","purpose":"sat-sim-provider-probe"}'}}],
            "usage": {"total_tokens": 12},
        }, 4.2

    monkeypatch.setattr("sat_sim.model_providers._http_json", fake_http_json)
    registry = ModelProviderRegistry(profiles=[ModelProviderProfile(
        provider_id="local-lmstudio",
        label="本地 LM Studio",
        backend="openai_compatible",
        location="local",
        tier="L2",
        model="qwen3.5-9b",
        base_url="http://127.0.0.1:1234/v1",
    )])
    result = registry.probe_generation("local-lmstudio")
    assert result["ok"] is True
    assert result["structured_output_transport"] == "prompt_only_json"
    assert len(calls) == 2
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]


def test_provider_catalog_exposes_saved_active_and_full_test_states(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        saved = client.post("/models/local-services", json={
            "service_type": "lmstudio",
            "model": "qwen3.5-9b",
            "base_url": "http://127.0.0.1:1234/v1",
        })
        assert saved.status_code == 200
        activated = client.post("/models/providers/local-lmstudio/activate")
        assert activated.status_code == 200
        providers = client.get("/models/providers").json()["catalog"]["providers"]
    lmstudio = next(item for item in providers if item["provider_id"] == "local-lmstudio")
    assert lmstudio["saved"] is True
    assert lmstudio["active"] is True
    assert lmstudio["availability_state"] in {"ENDPOINT_REACHABLE", "CONFIGURED_NOT_TESTED", "FULL_TEST_PASSED", "FULL_TEST_FAILED"}


def test_workbench_assets_explain_roles_and_preserve_new_task_semantics(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        index = client.get("/").text
        js = client.get("/assets/app.js").text
        css = client.get("/assets/styles.css").text
    assert "不启动仿真" in index
    assert "任务中心" in index and "可复用" in index
    assert "运行中心" in index and "执行记录" in index
    assert "当前编辑配置" in index and "任务中心已保存的任务" in index
    assert "基于当前配置继续补充" in index
    assert "另存为新任务" in index and "更新已打开任务" in index
    assert "Date.now().toString(36)" in js
    assert "field-error-message" in js
    assert "object_editor" in js
    assert ".field-description" in css and ".advanced-fields" in css


def test_natural_language_refinement_locks_capability_and_only_changes_requested_duration(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        base_response = client.post("/tasks/parse", json={
            "input_kind": "natural_language",
            "request_text": "创建一个反作用轮，轮速初始值为100 r/min，在力矩作用下，提升到150 r/min",
            "backend": "template",
            "local_backend": "template",
            "routing_mode": "local",
            "compile_if_valid": True,
        })
        assert base_response.status_code == 200
        base_result = base_response.json()["result"]
        assert base_result["ok"] is True
        base_spec = base_result["task_spec"]

        refine_response = client.post("/tasks/parse", json={
            "input_kind": "natural_language",
            "request_text": "把仿真时长改为80秒",
            "base_task_spec": base_spec,
            "backend": "template",
            "local_backend": "template",
            "routing_mode": "local",
            "compile_if_valid": True,
        })

    assert refine_response.status_code == 200
    result = refine_response.json()["result"]
    assert result["ok"] is True
    spec = result["task_spec"]
    assert spec["model"]["capability_id"] == "component.reaction_wheel.v1"
    assert spec["simulation"]["duration_s"] == pytest.approx(80.0)
    assert spec["parameters"] == base_spec["parameters"]
    assert spec["events"] == base_spec["events"]
    assert "NATURAL_LANGUAGE_REFINEMENT_APPLIED" in result["reason_codes"]
    assert "REFINEMENT_CAPABILITY_LOCKED" in result["reason_codes"]
    assert "REQUIRED_QOI_MISSING" not in result["reason_codes"]
