from __future__ import annotations

import io
import json
import urllib.error

import pytest

from sat_sim.agent_facade import _sanitize_generated_draft
from sat_sim.capability_agent import build_capability_agent_prompt
from sat_sim.experiment_manager import expand_sweep, sweep_parameter_options
from sat_sim.form_schema import capability_form_schema
from sat_sim.llm_backends import OpenAIChatCompletionsBackend


def test_adcs_and_other_exposed_subsystems_register_runtime_component_curves() -> None:
    expected = {
        "subsystem.adcs_fidelity.v1": {"adcs.attitude.q_bn_0", "adcs.sensor.star_tracker_noise_rad", "adcs.environment.total_torque_norm_nm", "adcs.rw.momentum_nms_0"},
        "subsystem.eps.source_native.v1": {"eps.source_native.solar_power_w", "eps.source_native.battery_soc"},
        "subsystem.thermal.source_native.v1": {"thermal.source_native.battery_temp_k", "thermal.source_native.heater_power_w"},
        "subsystem.comm_data.source_native.v1": {"comm_data.source_native.queue_bits", "comm_data.source_native.downlink_rate_bps"},
        "subsystem.propulsion.source_native.v1": {"propulsion.source_native.total_impulse_ns", "propulsion.source_native.tank_pressure_pa"},
        "subsystem.payload.source_native.v1": {"payload.source_native.power_w", "payload.source_native.generated_bps"},
    }
    for capability_id, required in expected.items():
        options = capability_form_schema(capability_id)["outputs"]["trace_options"]
        names = {item["name"] for item in options}
        assert required <= names
        assert all(item.get("group_label") for item in options)
        assert all(
            item.get(key)
            for item in options
            for key in ("scope_id", "scope_label", "subsystem_id", "subsystem_label", "node_id", "node_label")
        )


def test_exposed_whole_spacecraft_capabilities_have_multi_domain_curve_coverage() -> None:
    checks = {
        "whole_spacecraft.orbit_adcs_fidelity.v1": ("orbit.", "adcs."),
        "whole_spacecraft.power_thermal_orbit_coupled.v1": ("orbit.", "eps.", "thermal."),
        "whole_spacecraft.comm_payload_mission_coupled.v1": ("orbit.", "comm.", "payload."),
        "whole_spacecraft.maneuver_orbit_attitude.v1": ("orbit.", "propulsion.", "adcs."),
    }
    for capability_id, prefixes in checks.items():
        names = {item["name"] for item in capability_form_schema(capability_id)["outputs"]["trace_options"]}
        for prefix in prefixes:
            assert any(name.startswith(prefix) for name in names), (capability_id, prefix)


def test_curve_selector_never_exposes_wildcard_pseudo_options() -> None:
    from sat_sim.capability_registry import list_capabilities

    for capability in list_capabilities():
        outputs = capability_form_schema(capability.capability_id)["outputs"]
        names = [item["name"] for item in outputs["trace_options"]]
        assert names == outputs["trace_fields"]
        assert outputs["unresolved_trace_patterns"] == []
        assert all(not any(token in name for token in ("*", "?", "[")) for name in names), capability.capability_id
        assert all(item["group_id"] != "other" for item in outputs["trace_options"]), capability.capability_id
        assert all("全部" not in item["label"] for item in outputs["trace_options"]), capability.capability_id
        assert all(
            not any(token in name for token in ("*", "?", "["))
            for name in capability_form_schema(capability.capability_id)["default_form"]["outputs"]["plots"]
        ), capability.capability_id

    whole = capability_form_schema("whole_spacecraft.unified_native.v1")
    whole_names = {item["name"] for item in whole["outputs"]["trace_options"]}
    assert "adcs.*" not in whole_names
    assert {"adcs.body_rate_rad_s_x", "adcs.body_rate_rad_s_y", "adcs.body_rate_rad_s_z"} <= whole_names
    assert {"eps.battery_soc", "thermal.battery_temp_k", "propulsion.thrust_force_n_0"} <= whole_names

    eps = capability_form_schema("subsystem.eps.basic.v1")
    eps_names = {item["name"] for item in eps["outputs"]["trace_options"]}
    assert "eps.*" not in eps_names
    assert {"eps.battery.soc", "eps.solar.array_power_w", "eps.pdu.efficiency"} <= eps_names


def test_parameter_sweep_options_are_registry_derived_and_arbitrary_paths_are_rejected() -> None:
    schema = capability_form_schema("subsystem.adcs_fidelity.v1")
    spec = schema["default_form"]
    options = sweep_parameter_options(spec)
    paths = {item["path"] for item in options}
    assert "simulation.duration_s" in paths
    assert "parameters.values.control_kp_nm_per_rad" in paths
    assert "parameters.values.environment_torques" not in paths
    variants = expand_sweep(spec, {"parameters.values.control_kp_nm_per_rad": [0.1, 0.2]})
    assert len(variants) == 2
    with pytest.raises(ValueError, match="unsupported sweep parameter"):
        expand_sweep(spec, {"parameters.values.not_a_real_parameter": [1, 2]})


def test_compact_local_prompt_fits_8k_context_with_safe_generation_budget() -> None:
    prompt = build_capability_agent_prompt("创建一个ADCS分系统姿态收敛仿真，运行60秒")
    assert len(prompt) < 12000
    assert "simulation.subsystem must be adcs" in prompt
    assert "operator_contract" not in prompt


def test_missing_canonical_subsystem_is_repaired_before_pydantic_validation() -> None:
    draft = {
        "schema_version": "1.0",
        "task": {"id": "adcs_test", "name": "ADCS测试"},
        "simulation": {"level": "subsystem", "duration_s": 60, "sample_s": 10, "step_s": 10},
        "model": {"capability_id": "subsystem.adcs_fidelity.v1", "target": {"level": "subsystem", "name": "adcs", "mode": "nominal"}},
        "parameters": {"profile": "demo", "values": {}},
        "events": {"faults": [], "degradations": []},
        "outputs": {"output_root": "runs/adcs_test", "trace_format": "csv", "qoi": [], "plots": [], "include_summary": True, "include_trace": True, "include_labels": True, "include_manifest": True},
        "assurance": {"fidelity_level": "declared_by_capability", "claim_level": "analysis_only", "validation_profile": "default", "parameter_profile": "demo", "allow_proxy": False},
    }
    repaired, repairs = _sanitize_generated_draft(draft, "创建ADCS姿态仿真")
    assert repaired["simulation"]["subsystem"] == "adcs"
    assert any(item["path"] == "simulation.subsystem" for item in repairs)


def test_context_window_http_error_does_not_retry_without_response_format(monkeypatch) -> None:
    calls = 0
    def fake_urlopen(req, timeout):
        nonlocal calls
        calls += 1
        raise urllib.error.HTTPError(req.full_url, 400, "bad request", {}, io.BytesIO(json.dumps({"error":"request (12666 tokens) exceeds the available context size (8192 tokens)"}).encode()))
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    backend = OpenAIChatCompletionsBackend(model="qwen", base_url="http://127.0.0.1:1234/v1", max_output_tokens=2048, structured_output="json_schema")
    result = backend.generate_text("x" * 9000)
    assert result.ok is False
    assert result.metadata["reason_code"] == "MODEL_CONTEXT_WINDOW_EXCEEDED"
    assert calls == 1


def test_openai_compatible_request_sends_frozen_seed(monkeypatch) -> None:
    bodies = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps(
                {
                    "id": "seeded-call",
                    "choices": [{"message": {"content": '{"schema_version":"1.0.0"}'}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }
            ).encode()

    def fake_urlopen(req, timeout):
        bodies.append(json.loads(req.data))
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    backend = OpenAIChatCompletionsBackend(
        model="qwen",
        base_url="http://127.0.0.1:1234/v1",
        temperature=0.0,
        seed=0,
        max_output_tokens=2048,
    )
    result = backend.generate_text("seeded")

    assert result.ok is True
    assert bodies == [
        {
            "model": "qwen",
            "messages": bodies[0]["messages"],
            "stream": False,
            "temperature": 0.0,
            "seed": 0,
            "max_tokens": 2048,
            "response_format": {"type": "json_object"},
        }
    ]
    assert result.metadata["seed"] == 0


def test_curve_options_exclude_namespaced_time_axis_and_use_domain_groups() -> None:
    comm = capability_form_schema("whole_spacecraft.comm_payload_mission_coupled.v1")["outputs"]["trace_options"]
    names = {item["name"] for item in comm}
    groups = {item["group_label"] for item in comm}
    assert "spacecraft.time_s" not in names
    assert {"载荷", "发射机", "下行链路", "数据存储"} <= groups
    maneuver = capability_form_schema("whole_spacecraft.maneuver_orbit_attitude.v1")["outputs"]["trace_options"]
    by_name = {item["name"]: item["group_label"] for item in maneuver}
    assert by_name["adcs.pointing_error_deg"] == "姿态指向"
    assert by_name["adcs.angular_rate_disturbance_rad_s"] == "机体角速度"
    hierarchy = {item["name"]: item for item in maneuver}
    assert hierarchy["orbit.radius_m"]["scope_label"] == "整星"
    assert hierarchy["adcs.pointing_error_deg"]["scope_label"] == "分系统"
    assert hierarchy["adcs.pointing_error_deg"]["subsystem_label"] == "姿态控制分系统"
