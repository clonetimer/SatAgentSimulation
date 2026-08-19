from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sat_sim.capability_agent import (
    CapabilityAgentRequest,
    _capability_id_punctuation_alias,
    _coerce_common_llm_aliases,
    _external_draft_capability_id,
    _replace_external_draft_capability_id,
)
from sat_sim.lmstudio_acceptance import _extract_native_models
from sat_sim.task_validator import validate_task_spec


def _request(text: str) -> CapabilityAgentRequest:
    return CapabilityAgentRequest(request=text, backend="openai_compatible")


def test_punctuation_only_capability_alias_can_be_restored_without_template_fallback() -> None:
    draft = {
        "schema_version": "0.1.0",
        "capability_id": "component.fuel.tank.v1",
        "parameters": {"initial_mass_kg": 2.0},
    }

    assert _capability_id_punctuation_alias(
        "component.fuel.tank.v1",
        "component.fuel_tank.v1",
    )
    assert not _capability_id_punctuation_alias(
        "component.battery.v1",
        "component.fuel_tank.v1",
    )
    repaired = _replace_external_draft_capability_id(
        draft,
        "component.fuel_tank.v1",
    )
    assert repaired["capability_id"] == "component.fuel_tank.v1"
    assert repaired["parameters"] == draft["parameters"]


def test_canonical_adcs_draft_restores_locked_subsystem_before_validation() -> None:
    draft = {
        "schema_version": "1.0",
        "task": {"id": "adcs_create"},
        "simulation": {"level": "subsystem", "duration_s": 20, "sample_s": 1},
        "model": {
            "capability_id": "subsystem.adcs_unified_native.v1",
            "target": {"level": "subsystem", "name": "adcs", "mode": "nominal"},
        },
        "outputs": {"qoi": ["adcs.pointing_error_deg", "adcs.rw_speed_rad_s_0"]},
    }
    normalized = _coerce_common_llm_aliases(draft, _request("创建ADCS统一运行图，运行20秒"))
    assert normalized["capability_id"] == "subsystem.adcs_unified_native.v1"
    assert normalized["simulation"]["level"] == "subsystem"
    assert normalized["simulation"]["subsystem"] == "adcs"
    assert normalized["target"]["name"] == "adcs"
    assert validate_task_spec(normalized).ok is True


def test_wrapped_canonical_whole_spacecraft_draft_keeps_identity_and_scaffold() -> None:
    draft = {
        "task_spec": {
            "schema_version": "1.0",
            "task": {"id": "whole_create"},
            "simulation": {"level": "whole_spacecraft", "duration_s": 40, "sample_s": 2},
            "model": {
                "capability_id": "whole_spacecraft.unified_native.v1",
                "target": {"level": "whole_spacecraft", "name": "whole_spacecraft", "mode": "nominal"},
            },
            "outputs": {"qoi": ["eps.battery_soc", "thermal.payload_temp_k"]},
            "assurance": {"allow_proxy": False, "parameter_profile": "demo"},
        }
    }
    assert _external_draft_capability_id(draft) == "whole_spacecraft.unified_native.v1"
    normalized = _coerce_common_llm_aliases(draft, _request("创建整星统一运行图，运行40秒"))
    assert normalized["capability_id"] == "whole_spacecraft.unified_native.v1"
    assert normalized["task_type"] == "whole_spacecraft"
    assert normalized["spacecraft"]["mission"]["template"] == "whole_spacecraft.unified_native.v1"
    assert normalized["assurance"]["allow_proxy"] is False
    assert validate_task_spec(normalized).ok is True


def test_explicit_contract_effect_overrides_model_legacy_fault_alias() -> None:
    draft = {
        "schema_version": "0.1.0",
        "task_id": "explicit_fault_effect",
        "task_type": "whole_spacecraft",
        "capability_id": "whole_spacecraft.unified_native.v1",
        "target": {"level": "whole_spacecraft", "name": "whole_spacecraft", "mode": "fault"},
        "simulation": {"duration_s": 12.0, "sample_s": 1.0},
        "spacecraft": {"mission": {"template": "registry_synthesized"}},
        "faults": [{
            "fault_id": "payload_off",
            "target": "payload",
            "target_type": "component",
            "fault_type": "instrument_off",
            "onset_time_s": 2.0,
            "duration_s": -1,
            "magnitude": 1.0,
        }],
    }
    normalized = _coerce_common_llm_aliases(
        draft,
        _request(
            "使用 capability_id=whole_spacecraft.unified_native.v1，"
            "模式为fault，使用事件 effect=payload_instrument_off，在2秒注入"
        ),
    )
    assert "faults" not in normalized
    event = normalized["modifiers"]["faults"][0]
    assert event["fault_type"] == "payload_instrument_off"
    assert event["target"] == "whole_spacecraft"
    assert event["target_type"] == "whole_spacecraft"


def test_explicit_event_parameters_survive_model_omission() -> None:
    draft = {
        "schema_version": "0.1.0",
        "task_id": "explicit_adcs_parameters",
        "task_type": "subsystem",
        "capability_id": "subsystem.adcs_unified_native.v1",
        "target": {"level": "subsystem", "name": "adcs", "mode": "fault"},
        "simulation": {"duration_s": 12.0, "sample_s": 1.0},
        "faults": [{
            "fault_id": "jam",
            "target": "reaction_wheel_0",
            "target_type": "actuator",
            "fault_type": "jamming",
            "onset_time_s": 2.0,
            "duration_s": -1,
            "magnitude": 1.0,
        }],
    }
    normalized = _coerce_common_llm_aliases(
        draft,
        _request(
            "使用 capability_id=subsystem.adcs_unified_native.v1，"
            "使用事件 effect=adcs_rw_jamming，参数为 {\"wheel_index\": 0}"
        ),
    )
    event = normalized["modifiers"]["faults"][0]
    assert event["fault_type"] == "adcs_rw_jamming"
    assert event["parameters"] == {"wheel_index": 0}


def test_explicit_degradation_normalizes_percentage_magnitude() -> None:
    draft = {
        "schema_version": "0.1.0",
        "task_id": "adcs_percentage_degradation",
        "task_type": "subsystem",
        "capability_id": "subsystem.adcs_unified_native.v1",
        "target": {"level": "subsystem", "name": "adcs", "mode": "degradation"},
        "simulation": {"duration_s": 12.0, "sample_s": 1.0},
        "degradations": [{
            "effect_id": "gyro_noise_increase",
            "onset_time_s": 0.0,
            "magnitude": 10.0,
        }],
    }
    normalized = _coerce_common_llm_aliases(
        draft,
        _request(
            "使用 capability_id=subsystem.adcs_unified_native.v1，"
            "模式为degradation，使用事件 effect=gyro_noise_increase"
        ),
    )
    event = normalized["modifiers"]["degradations"][0]
    assert event["degradation_type"] == "gyro_noise_increase"
    assert event["severity"] == pytest.approx(0.1)


def test_explicit_effect_normalizes_existing_modifier_shape() -> None:
    draft = {
        "schema_version": "0.1.0",
        "task_id": "propulsion_modifier_degradation",
        "task_type": "subsystem",
        "capability_id": "subsystem.propulsion.unified_native.v1",
        "target": {"level": "subsystem", "name": "propulsion", "mode": "degradation"},
        "simulation": {"duration_s": 12.0, "sample_s": 1.0},
        "modifiers": {
            "faults": [],
            "degradations": [{"effect": "performance_degradation", "onset_time_s": 0.0}],
            "constraints": [],
        },
    }
    normalized = _coerce_common_llm_aliases(
        draft,
        _request(
            "使用 capability_id=subsystem.propulsion.unified_native.v1，"
            "模式为degradation，使用事件 effect=performance_degradation"
        ),
    )
    event = normalized["modifiers"]["degradations"][0]
    assert event["degradation_type"] == "performance_degradation"
    assert event["target"] == "propulsion"


def test_aggregate_subsystem_effect_keys_are_contract_authoritative() -> None:
    draft = {
        "schema_version": "0.1.0",
        "task_id": "propulsion_fault",
        "task_type": "subsystem",
        "capability_id": "subsystem.propulsion.unified_native.v1",
        "target": {"level": "subsystem", "name": "propulsion", "mode": "fault"},
        "simulation": {"duration_s": 12.0, "sample_s": 1.0},
        "faults": [{
            "fault_id": "feed",
            "target": "propulsion",
            "target_type": "subsystem",
            "fault_type": "feed_failure",
            "onset_time_s": 2.0,
            "duration_s": -1,
            "magnitude": 1.0,
        }],
    }
    normalized = _coerce_common_llm_aliases(
        draft,
        _request("使用事件 effect=thrust_or_feed_failure，在2秒注入"),
    )
    assert normalized["modifiers"]["faults"][0]["fault_type"] == "thrust_or_feed_failure"


def test_unknown_explicit_effect_cannot_override_contract() -> None:
    draft = {
        "schema_version": "0.1.0",
        "task_id": "unknown_effect",
        "task_type": "whole_spacecraft",
        "capability_id": "whole_spacecraft.unified_native.v1",
        "target": {"level": "whole_spacecraft", "name": "whole_spacecraft", "mode": "fault"},
        "simulation": {"duration_s": 12.0, "sample_s": 1.0},
        "spacecraft": {"mission": {"template": "registry_synthesized"}},
        "faults": [{
            "fault_id": "bad",
            "target": "payload",
            "target_type": "component",
            "fault_type": "instrument_off",
            "onset_time_s": 2.0,
            "duration_s": -1,
            "magnitude": 1.0,
        }],
    }
    normalized = _coerce_common_llm_aliases(
        draft,
        _request("使用事件 effect=not_a_registered_effect，在2秒注入"),
    )
    assert normalized["faults"][0]["fault_type"] == "instrument_off"


def test_explicit_degradation_is_promoted_to_runtime_modifier() -> None:
    draft = {
        "schema_version": "0.1.0",
        "task_id": "explicit_degradation",
        "task_type": "whole_spacecraft",
        "capability_id": "whole_spacecraft.unified_native.v1",
        "target": {
            "level": "whole_spacecraft",
            "name": "whole_spacecraft",
            "mode": "degradation",
        },
        "simulation": {"duration_s": 12.0, "sample_s": 1.0},
        "spacecraft": {"mission": {"template": "registry_synthesized"}},
        "degradations": [{
            "degradation_id": "solar_loss",
            "target": "solar_panel",
            "target_type": "component",
            "degradation_type": "efficiency_loss",
            "onset_time_s": 0.0,
            "duration_s": 12.0,
            "magnitude": 0.1,
        }],
    }
    normalized = _coerce_common_llm_aliases(
        draft,
        _request(
            "使用 capability_id=whole_spacecraft.unified_native.v1，"
            "模式为degradation，使用事件 effect=solar_panel_efficiency_loss，在0秒注入"
        ),
    )
    assert "degradations" not in normalized
    event = normalized["modifiers"]["degradations"][0]
    assert event["degradation_type"] == "solar_panel_efficiency_loss"
    assert event["target"] == "whole_spacecraft"


def test_planner_route_repair_is_counted_as_model_fallback() -> None:
    from types import SimpleNamespace

    from sat_sim.agent_facade import _model_fallback_used

    backend_result = SimpleNamespace(
        task_spec={},
        steps=(SimpleNamespace(name="planner_route_repair", payload={}),),
    )
    assert _model_fallback_used(backend_result) is True


def test_windows_unified_native_example_is_valid_and_has_spacecraft() -> None:
    spec = yaml.safe_load(Path("examples/whole_spacecraft_unified_native_nominal.yaml").read_text(encoding="utf-8"))
    assert spec["spacecraft"]["mission"]["template"] == "whole_spacecraft.unified_native.v1"
    assert validate_task_spec(spec).ok is True


def test_lmstudio_native_loaded_instances_marks_model_loaded() -> None:
    rows = _extract_native_models({
        "models": [{
            "id": "qwen/qwen3.5-9b",
            "loaded": False,
            "loaded_instances": [{"id": "qwen/qwen3.5-9b"}],
        }]
    })
    assert rows[0]["loaded"] is True


def test_platform_acceptance_uses_project_root_paths_and_exit_code() -> None:
    text = Path("scripts/run_platform_acceptance.py").read_text(encoding="utf-8")
    assert "ROOT = Path(__file__).resolve().parents[1]" in text
    assert 'ROOT / "scripts" / "run_vllm_acceptance.py"' in text
    assert 'ROOT / "examples" / "whole_spacecraft_unified_native_nominal.yaml"' in text
    assert "return 0 if required_failed == 0 else 1" in text


def test_planner_prefers_recommended_unified_runtime_routes() -> None:
    from sat_sim.capability_agent import DEFAULT_ALLOWED_CAPABILITIES
    from sat_sim.capability_planner import plan_capability_for_request

    adcs = plan_capability_for_request(
        "创建一个ADCS统一运行图仿真，运行20秒，输出姿态误差和三个反作用轮轮速。",
        allowed_capabilities=DEFAULT_ALLOWED_CAPABILITIES,
    )
    whole = plan_capability_for_request(
        "创建整星统一运行图仿真，运行40秒，输出电池SOC、载荷数据率、下行数据率和温度。",
        allowed_capabilities=DEFAULT_ALLOWED_CAPABILITIES,
    )
    assert adcs.selected_capability_id == "subsystem.adcs_unified_native.v1"
    assert whole.selected_capability_id == "whole_spacecraft.unified_native.v1"


def test_explicit_capability_before_english_full_stop_is_authoritative() -> None:
    from sat_sim.capability_planner import plan_capability_for_request

    result = plan_capability_for_request(
        "Use capability whole_spacecraft.unified_native.v1. "
        "Inject effect payload_instrument_off from 2s."
    )

    assert result.selected_capability_id == "whole_spacecraft.unified_native.v1"


def test_optional_empty_profile_placeholders_are_omitted() -> None:
    draft = {
        "schema_version": "0.1.0",
        "task_id": "queue_degradation",
        "task_type": "component",
        "capability_id": "component.data_queue.v1",
        "target": {"level": "component", "name": "data_queue", "mode": "degradation"},
        "simulation": {"duration_s": 12.0, "sample_s": 1.0},
        "parameters": {
            "capacity_bits": 1_000_000.0,
            "generated_bps": 1_000.0,
            "generated_profile_bps": [],
            "downlink_profile_bps": [],
        },
        "modifiers": {
            "degradations": [{"effect": "throughput_decay", "onset_time_s": 0.0}]
        },
    }

    normalized = _coerce_common_llm_aliases(
        draft,
        _request(
            "Use capability_id=component.data_queue.v1 and set mode=degradation. "
            "Inject event effect=throughput_decay from 0s."
        ),
    )

    assert "generated_profile_bps" not in normalized["parameters"]
    assert "downlink_profile_bps" not in normalized["parameters"]


def test_explicit_solar_effect_preserves_structured_degradation_payload() -> None:
    draft = {
        "schema_version": "0.1.0",
        "task_id": "solar_degradation",
        "task_type": "component",
        "capability_id": "component.solar_panel.v1",
        "target": {"level": "component", "name": "solar_panel", "mode": "degradation"},
        "simulation": {"duration_s": 12.0, "sample_s": 1.0},
        "parameters": {"max_power_w": 120.0, "efficiency": 1.0},
        "degradations": [{
            "effect": "efficiency_loss_pct",
            "onset_time_s": 0.0,
            "magnitude": 20.0,
        }],
        "modifiers": {
            "degradations": [{
                "type": "efficiency_loss_pct",
                "magnitude": 20.0,
            }]
        },
    }

    normalized = _coerce_common_llm_aliases(
        draft,
        _request(
            "Use capability_id=component.solar_panel.v1 and set mode=degradation. "
            "Inject event effect=efficiency_loss_pct from 0s."
        ),
    )

    assert normalized["degradations"]["eps"]["solar_panel"]["efficiency_loss_pct"] == 20.0
    assert normalized["modifiers"]["degradations"] == []


def test_human_event_label_resolves_to_authoritative_effect() -> None:
    draft = {
        "schema_version": "0.1.0",
        "task_id": "whole_fault_alias",
        "task_type": "whole_spacecraft",
        "capability_id": "whole_spacecraft.unified_native.v1",
        "target": {"level": "whole_spacecraft", "name": "whole_spacecraft", "mode": "fault"},
        "simulation": {"duration_s": 12.0, "sample_s": 1.0},
        "spacecraft": {"mission": {"template": "whole_spacecraft.unified_native.v1"}},
        "faults": [{
            "fault_id": "wrong_model_alias",
            "target": "whole_spacecraft",
            "target_type": "whole_spacecraft",
            "fault_type": "sensor_degradation",
            "onset_time_s": 2.0,
            "duration_s": -1.0,
            "magnitude": 1.0,
        }],
    }

    normalized = _coerce_common_llm_aliases(
        draft,
        _request(
            "采用能力 whole_spacecraft.unified_native.v1，"
            "模拟“载荷仪器关闭（故障）”，从2秒开始。"
        ),
    )

    assert normalized["modifiers"]["faults"][0]["fault_type"] == "payload_instrument_off"


def test_human_degradation_label_supplies_mode_and_catalog_event() -> None:
    draft = {
        "schema_version": "0.1.0",
        "task_id": "whole_degradation_alias",
        "task_type": "whole_spacecraft",
        "capability_id": "whole_spacecraft.unified_native.v1",
        "target": {"level": "whole_spacecraft", "name": "whole_spacecraft", "mode": "nominal"},
        "simulation": {"duration_s": 12.0, "sample_s": 1.0},
        "spacecraft": {"mission": {"template": "whole_spacecraft.unified_native.v1"}},
    }

    normalized = _coerce_common_llm_aliases(
        draft,
        _request(
            "采用能力 whole_spacecraft.unified_native.v1，"
            "模拟“太阳阵列帆板效率损耗（退化）”，从0秒开始。"
        ),
    )

    assert normalized["target"]["mode"] == "degradation"
    assert (
        normalized["modifiers"]["degradations"][0]["degradation_type"]
        == "solar_panel_efficiency_loss"
    )


def test_string_only_model_modifier_is_replaced_by_catalog_event() -> None:
    draft = {
        "schema_version": "0.1.0",
        "task_id": "eps_degradation_alias",
        "task_type": "subsystem",
        "capability_id": "subsystem.eps.unified_native.v1",
        "target": {"level": "subsystem", "name": "eps", "mode": "nominal"},
        "simulation": {"duration_s": 12.0, "sample_s": 1.0},
        "modifiers": {"degradations": ["energy_margin_degradation"]},
    }

    normalized = _coerce_common_llm_aliases(
        draft,
        _request(
            "采用能力 subsystem.eps.unified_native.v1，"
            "模拟“能量裕度退化（退化）”，从0秒开始。"
        ),
    )

    event = normalized["modifiers"]["degradations"][0]
    assert event["degradation_type"] == "energy_margin_degradation"
    assert isinstance(event["parameters"], dict)


def test_operator_contract_accepts_trace_and_trace_fields_aliases() -> None:
    from sat_sim.capability_registry import get_capability

    adcs = get_capability("subsystem.adcs_unified_native.v1").operator_contract
    whole = get_capability("whole_spacecraft.unified_native.v1").operator_contract
    assert "adcs.*" in adcs.observability.trace_fields
    assert "eps.*" in whole.observability.trace_fields
    assert "thermal.*" in whole.observability.trace_fields


def test_canonical_model_drafts_compile_through_unified_agent(monkeypatch, tmp_path: Path) -> None:
    import sat_sim.capability_agent as capability_agent
    from sat_sim.unified_agent import UnifiedAgentRequest, run_unified_agent

    class CanonicalBackend:
        name = "canonical-fixture"
        last_raw_output = "{}"
        last_call = None

        def draft_task_spec(self, *, request, context):
            if "整星" in request.request:
                return {
                    "task_spec": {
                        "schema_version": "1.0",
                        "task": {"id": "whole_create"},
                        "simulation": {"level": "whole_spacecraft", "duration_s": 40, "sample_s": 2},
                        "model": {
                            "capability_id": "whole_spacecraft.unified_native.v1",
                            "target": {"level": "whole_spacecraft", "name": "whole_spacecraft", "mode": "nominal"},
                        },
                        "outputs": {"qoi": ["eps.battery_soc", "payload.generated_bps", "comm.downlink_bps", "thermal.payload_temp_k"]},
                    }
                }
            return {
                "schema_version": "1.0",
                "task": {"id": "adcs_create"},
                "simulation": {"level": "subsystem", "duration_s": 20, "sample_s": 1},
                "model": {
                    "capability_id": "subsystem.adcs_unified_native.v1",
                    "target": {"level": "subsystem", "name": "adcs", "mode": "nominal"},
                },
                "outputs": {"qoi": ["adcs.pointing_error_deg", "adcs.rw.speed_rad_s_0"]},
            }

    monkeypatch.setattr(capability_agent, "_backend_from_request", lambda request: CanonicalBackend())
    adcs = run_unified_agent(UnifiedAgentRequest(
        input_kind="natural_language",
        request_text="创建一个ADCS统一运行图仿真，运行20秒，输出姿态误差和三个反作用轮轮速。",
        output_dir=tmp_path / "adcs",
        backend="openai_compatible",
        compile_if_valid=True,
    ))
    whole = run_unified_agent(UnifiedAgentRequest(
        input_kind="natural_language",
        request_text="创建整星统一运行图仿真，运行40秒，输出电池SOC、载荷数据率、下行数据率和温度。",
        output_dir=tmp_path / "whole",
        backend="openai_compatible",
        compile_if_valid=True,
    ))
    assert adcs.ok is True
    assert adcs.task_spec["simulation"]["subsystem"] == "adcs"
    assert whole.ok is True
    assert whole.task_spec["model"]["spacecraft"]["mission"]["template"] == "whole_spacecraft.unified_native.v1"
