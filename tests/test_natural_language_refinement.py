from __future__ import annotations

import json
import copy
from pathlib import Path

from sat_sim.model_providers import ModelProviderRegistry
from sat_sim.nl_outputs import resolve_output_intents
from sat_sim.task_models import canonicalize_task_spec
from sat_sim.unified_agent import _merge_natural_language_refinement, run_unified_agent, UnifiedAgentRequest


def _base_adcs_spec() -> dict:
    return canonicalize_task_spec({
        "schema_version": "1.0.0",
        "task": {"id": "adcs_refinement_base", "name": "ADCS姿态收敛"},
        "simulation": {
            "level": "subsystem",
            "subsystem": "adcs",
            "duration_s": 60.0,
            "step_s": 1.0,
            "sample_s": 10.0,
            "backend": "selective_unified_basilisk_assembly",
        },
        "parameters": {"profile": "demo", "values": {"target_mode": "nadir", "wheel_geometry": "pyramid_4"}, "overrides": []},
        "events": {"faults": [], "degradations": [], "constraints": []},
        "outputs": {"output_root": "runs", "trace_format": "csv", "qoi": [], "plots": [], "include_summary": True, "include_trace": True, "include_labels": True, "include_manifest": True},
        "assurance": {"fidelity_level": "declared_by_capability", "claim_level": "analysis_only", "validation_profile": "default", "parameter_profile": "demo", "allow_proxy": False},
        "model": {"capability_id": "subsystem.adcs_fidelity.v1", "target": {"level": "subsystem", "name": "adcs", "mode": "nominal"}, "config": {"target_mode": "nadir", "wheel_geometry": "pyramid_4"}},
        "provenance": {"fields": {}, "assumptions": [], "pending_confirmations": []},
        "metadata": {},
    })


def test_v0539_output_intent_resolves_declared_adcs_fields() -> None:
    result = resolve_output_intents("输出姿态角和反作用轮转速曲线", "subsystem.adcs_fidelity.v1")
    assert "adcs.rw.speed_rad_s_0" in result.fields
    assert "adcs.rw.speed_rad_s_1" in result.fields
    assert "adcs.rw.speed_rad_s_2" in result.fields
    assert "adcs.pointing.error_deg" in result.fields
    assert result.ambiguous_intents, "姿态角必须记录歧义，不能静默改名为四元数"


def test_v0539_refinement_preserves_subsystem_and_adds_output_plots() -> None:
    base = _base_adcs_spec()
    candidate = {
        "schema_version": "1.0.0",
        "task": base["task"],
        "simulation": {"level": "subsystem", "duration_s": 60.0, "step_s": 1.0, "sample_s": 10.0, "backend": "selective_unified_basilisk_assembly"},
        "parameters": base["parameters"],
        "events": base["events"],
        "outputs": {"output_root": "runs", "trace_format": "csv", "qoi": [], "plots": [], "include_summary": True, "include_trace": True, "include_labels": True, "include_manifest": True},
        "assurance": base["assurance"],
        "model": {"capability_id": "subsystem.adcs_fidelity.v1", "target": {"level": "subsystem", "name": "adcs", "mode": "nominal"}},
        "provenance": base["provenance"],
        "metadata": {},
    }
    refined, changed = _merge_natural_language_refinement(base, candidate, "输出曲线参量，请选择姿态角和飞轮转速")
    assert refined["simulation"]["subsystem"] == "adcs"
    assert refined["model"]["capability_id"] == "subsystem.adcs_fidelity.v1"
    assert "adcs.rw.speed_rad_s_0" in refined["outputs"]["plots"]
    assert "adcs.pointing.error_deg" in refined["outputs"]["plots"]
    assert "outputs.plots" in changed
    assert refined["metadata"]["natural_language_patch_ops"]


def test_time_and_output_refinement_preserves_custom_hardware_parameters():
    base = _base_adcs_spec()
    base['parameters']['values'].update({'controller_k': 7.25, 'controller_p': 42.})
    candidate = copy.deepcopy(base)
    candidate['parameters']['values'].update({'controller_k': 3.5, 'controller_p': 30.})
    refined, changed = _merge_natural_language_refinement(
        base, candidate, '把仿真时长改成30秒，并追加输出陀螺角速度，保留原有能力和轮速输出。')
    assert refined['simulation']['duration_s'] == 30.
    assert refined['parameters']['values'] == base['parameters']['values']
    assert not any(path.startswith('parameters.values.') for path in changed)


def test_parameter_refinement_applies_only_requested_parameter():
    base = _base_adcs_spec()
    base['parameters']['values'].update({'controller_k': 7.25, 'controller_p': 42.})
    candidate = copy.deepcopy(base)
    candidate['parameters']['values'].update({'controller_k': 5., 'controller_p': 30.})
    refined, changed = _merge_natural_language_refinement(
        base, candidate, 'Set controller_k to 5 and keep controller_p unchanged.')
    assert refined['parameters']['values']['controller_k'] == 5.
    assert refined['parameters']['values']['controller_p'] == 42.
    assert 'parameters.values.controller_k' in changed
    assert 'parameters.values.controller_p' not in changed


def test_parameter_refinement_accepts_display_label():
    base = _base_adcs_spec()
    candidate = copy.deepcopy(base)
    candidate['parameters']['values']['target_mode'] = 'sun'
    refined, _ = _merge_natural_language_refinement(base, candidate, '把姿态目标模式改成对日定向')
    assert refined['parameters']['values']['target_mode'] == 'sun'


def test_native_refinement_keeps_custom_controller_and_compiles():
    from sat_sim.form_schema import capability_form_schema
    from sat_sim.unified_agent import normalize_form_task_spec
    from sat_sim.task_validator import validate_task_spec
    from sat_sim.task_compiler import compile_task_spec

    base = canonicalize_task_spec(normalize_form_task_spec(
        capability_form_schema('subsystem.adcs_unified_native.v1')['default_form']))
    base['parameters']['values']['controller_k'] = 7.25
    candidate = copy.deepcopy(base)
    candidate['parameters']['values']['controller_k'] = 3.5
    refined, _ = _merge_natural_language_refinement(
        base, candidate, '把仿真时长改成30秒，并追加输出陀螺角速度，保留原有能力和轮速输出。')
    assert refined['parameters']['values']['controller_k'] == 7.25
    assert refined['simulation']['duration_s'] == 30.
    assert any('gyro' in field for field in refined['outputs']['plots'])
    validate_task_spec(refined).raise_for_errors()
    compiled = compile_task_spec(refined)
    assert compiled.metadata['capability_id'] == 'subsystem.adcs_unified_native.v1'


def test_v0539_template_multiturn_refinement_is_runnable() -> None:
    base = _base_adcs_spec()
    result = run_unified_agent(UnifiedAgentRequest(
        input_kind="natural_language",
        request_text="输出曲线参量，请选择姿态角和轮转速，并把运行时长改为80秒",
        base_task_spec=base,
        backend="template",
        compile_if_valid=True,
    ))
    assert result.ok, result.validation.to_dict()
    assert result.task_spec["simulation"]["subsystem"] == "adcs"
    assert result.task_spec["simulation"]["duration_s"] == 80.0
    assert any(field.startswith("adcs.rw.speed_rad_s_") for field in result.task_spec["outputs"]["plots"])
    assert "adcs.pointing.error_deg" in result.task_spec["outputs"]["plots"]


def test_v0539_lmstudio_saved_timeout_migrates_to_300(tmp_path: Path) -> None:
    cfg = tmp_path / "providers.json"
    cfg.write_text(json.dumps({
        "providers": [{
            "provider_id": "local-lmstudio",
            "label": "LM Studio",
            "backend": "openai_compatible",
            "location": "local",
            "tier": "L2",
            "model": "qwen/qwen3.5-9b",
            "base_url": "http://127.0.0.1:1234/v1",
            "timeout_s": 60,
            "structured_output": "json_schema",
            "max_output_tokens": 8000,
        }]
    }), encoding="utf-8")
    profile = ModelProviderRegistry(config_path=cfg).get("local-lmstudio")
    assert profile.timeout_s == 300.0
    assert profile.max_output_tokens == 8000
