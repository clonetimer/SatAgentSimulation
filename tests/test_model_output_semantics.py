from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import sat_sim.capability_agent as capability_agent
import sat_sim.lmstudio_acceptance as lm_acceptance
from sat_sim.lmstudio_acceptance import AcceptanceCase, run_lmstudio_acceptance
from sat_sim.nl_outputs import apply_output_intents_to_spec, resolve_output_intents
from sat_sim.unified_agent import UnifiedAgentRequest, run_unified_agent


def _verified_facade(model_id: str = "fixture-model"):
    evidence = {
        "verified": True,
        "model_id": model_id,
        "request_id": "fixture-request",
        "prompt_sha256": "a" * 64,
        "response_sha256": "b" * 64,
        "structured_output_sha256": "c" * 64,
    }
    return SimpleNamespace(to_dict=lambda: {
        "backend_identity": {
            "backend": "openai_compatible",
            "actual_model_used": True,
            "actual_model_execution_verified": True,
            "invocation_evidence": evidence,
        }
    })


class AliasBackend:
    name = "alias-fixture"
    last_raw_output = "{}"
    last_call = None

    def draft_task_spec(self, *, request, context):
        text = request.request
        if "整星" in text:
            return {
                "schema_version": "1.0",
                "task": {"id": "whole_create"},
                "simulation": {"level": "whole_spacecraft", "duration_s": 40, "sample_s": 2},
                "model": {
                    "capability_id": "whole_spacecraft.unified_native.v1",
                    "target": {"level": "whole_spacecraft", "name": "whole_spacecraft", "mode": "nominal"},
                },
                "outputs": {
                    "qoi": ["battery_soc", "payload_generated_bps", "downlink_delivered_bps", "thermal_temp_c"],
                    "plots": ["battery_soc", "payload_generated_bps", "downlink_delivered_bps", "thermal_temp_c"],
                },
            }
        if "本轮补充指令" in text:
            return {
                "schema_version": "1.0",
                "task": {"id": "adcs_refine"},
                "simulation": {"level": "subsystem", "duration_s": 30, "sample_s": 1},
                "model": {
                    "capability_id": "subsystem.adcs_unified_native.v1",
                    "target": {"level": "subsystem", "name": "adcs", "mode": "nominal"},
                },
                "outputs": {"qoi": ["rw.speed.0", "gyro_rate"]},
            }
        return {
            "schema_version": "1.0",
            "task": {"id": "adcs_create"},
            "simulation": {"level": "subsystem", "duration_s": 20, "sample_s": 1},
            "model": {
                "capability_id": "subsystem.adcs_unified_native.v1",
                "target": {"level": "subsystem", "name": "adcs", "mode": "nominal"},
            },
            "outputs": {
                "qoi": ["pointing_error", "rw.speed.0", "rw.speed.1", "rw.speed.2"],
                "plots": ["pointing_error", "rw.speed.0", "rw.speed.1", "rw.speed.2"],
            },
        }


def test_current_trace_fields_are_used_for_natural_language_resolution() -> None:
    adcs = resolve_output_intents(
        "输出姿态误差、三个反作用轮轮速和陀螺角速度",
        "subsystem.adcs_unified_native.v1",
    )
    whole = resolve_output_intents(
        "输出电池SOC、载荷数据率、下行数据率和温度",
        "whole_spacecraft.unified_native.v1",
    )
    assert "adcs.pointing_error_deg" in adcs.fields
    assert "adcs.rw.speed_rad_s_*" in adcs.fields
    assert "adcs.sensor.gyro_measured_rad_s_*" in adcs.fields
    assert whole.fields == (
        "eps.battery_soc",
        "payload.generated_bps",
        "comm.downlink_bps",
        "thermal.payload_temp_k",
    )


def test_existing_model_aliases_are_replaced_not_appended() -> None:
    spec = {
        "model": {"capability_id": "whole_spacecraft.unified_native.v1"},
        "outputs": {
            "qoi": ["battery_soc", "payload_generated_bps", "downlink_delivered_bps", "thermal_temp_c"],
            "plots": ["battery_soc"],
        },
    }
    normalized, _ = apply_output_intents_to_spec(
        spec,
        "输出电池SOC、载荷数据率、下行数据率和温度",
    )
    assert normalized["outputs"]["qoi"] == [
        "eps.battery_soc",
        "payload.generated_bps",
        "comm.downlink_bps",
        "thermal.payload_temp_k",
    ]
    assert "battery_soc" not in normalized["outputs"]["qoi"]
    assert normalized["metadata"]["agent"]["output_alias_replacements"]


def test_alias_drafts_compile_for_adcs_and_whole_spacecraft(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(capability_agent, "_backend_from_request", lambda request: AliasBackend())
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
    assert adcs.compiled is not None
    assert adcs.task_spec["outputs"]["qoi"] == ["adcs.pointing_error_deg", "adcs.rw.speed_rad_s_*"]
    assert "REQUIRED_QOI_MISSING" not in adcs.reason_codes
    assert whole.ok is True
    assert whole.compiled is not None
    assert whole.task_spec["outputs"]["qoi"] == [
        "eps.battery_soc",
        "payload.generated_bps",
        "comm.downlink_bps",
        "thermal.payload_temp_k",
    ]
    assert "REQUIRED_QOI_MISSING" not in whole.reason_codes


def test_refinement_preserves_base_outputs_and_adds_registry_gyro(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(capability_agent, "_backend_from_request", lambda request: AliasBackend())
    base = run_unified_agent(UnifiedAgentRequest(
        input_kind="natural_language",
        request_text="创建一个ADCS统一运行图仿真，运行20秒，输出姿态误差和三个反作用轮轮速。",
        output_dir=tmp_path / "base",
        backend="openai_compatible",
        compile_if_valid=True,
    ))
    assert base.ok is True
    refined = run_unified_agent(UnifiedAgentRequest(
        input_kind="natural_language",
        request_text="把仿真时长改成30秒，并追加输出陀螺角速度，保留原有能力和轮速输出。",
        base_task_spec=base.task_spec,
        output_dir=tmp_path / "refined",
        backend="openai_compatible",
        compile_if_valid=True,
    ))
    assert refined.ok is True
    assert refined.compiled is not None
    assert refined.task_spec["simulation"]["duration_s"] == 30.0
    assert refined.task_spec["simulation"]["subsystem"] == "adcs"
    assert refined.task_spec["model"]["capability_id"] == "subsystem.adcs_unified_native.v1"
    assert "adcs.pointing_error_deg" in refined.task_spec["outputs"]["qoi"]
    assert "adcs.rw.speed_rad_s_*" in refined.task_spec["outputs"]["qoi"]
    assert "adcs.sensor.gyro_measured_rad_s_*" in refined.task_spec["outputs"]["qoi"]
    assert "REQUIRED_QOI_MISSING" not in refined.reason_codes


def test_acceptance_keeps_valid_base_even_when_first_compile_check_fails(monkeypatch, tmp_path: Path) -> None:
    def fake_request(url: str, **kwargs):
        if url.endswith("/v1/models"):
            return 200, {"data": [{"id": "fixture-model"}]}, 1.0
        if url.endswith("/api/v1/models"):
            return 200, {"models": [{"id": "fixture-model", "loaded": True}]}, 1.0
        raise AssertionError(url)

    calls = []

    def fake_agent(request):
        calls.append(request)
        if request.base_task_spec is None:
            return SimpleNamespace(
                route=SimpleNamespace(provider_id="local-lmstudio"),
                validation=SimpleNamespace(ok=True),
                guards=SimpleNamespace(ok=True),
                compiled=None,
                task_spec={
                    "model": {"capability_id": "subsystem.adcs_unified_native.v1"},
                    "outputs": {"qoi": ["adcs.pointing_error_deg"]},
                },
                reason_codes=("REQUIRED_QOI_MISSING",),
                ok=False,
                facade_result=_verified_facade(),
            )
        return SimpleNamespace(
            route=SimpleNamespace(provider_id="local-lmstudio"),
            validation=SimpleNamespace(ok=True),
            guards=SimpleNamespace(ok=True),
            compiled=object(),
            task_spec={
                "model": {"capability_id": "subsystem.adcs_unified_native.v1"},
                "outputs": {"qoi": ["adcs.rw.speed_rad_s_*", "adcs.sensor.gyro_measured_rad_s_*"]},
            },
            reason_codes=(),
            ok=True,
            facade_result=_verified_facade(),
        )

    monkeypatch.setattr(lm_acceptance, "_request_json", fake_request)
    monkeypatch.setattr(
        lm_acceptance.ModelProviderRegistry,
        "probe_generation",
        lambda self, provider_id, timeout_s=None: {"ok": True},
    )
    monkeypatch.setattr(lm_acceptance, "run_unified_agent", fake_agent)
    report = run_lmstudio_acceptance(
        base_url="http://fixture.invalid/v1",
        output_dir=tmp_path,
        execution_kind="protocol_fixture",
        cases=(
            AcceptanceCase(
                "base",
                "创建ADCS",
                ("subsystem.adcs_unified_native.v1",),
                ("rw.speed",),
            ),
            AcceptanceCase(
                "refine",
                "追加陀螺",
                ("subsystem.adcs_unified_native.v1",),
                ("rw.speed", "gyro"),
                base_case_id="base",
            ),
        ),
    )
    assert len(calls) == 2
    assert calls[1].base_task_spec is not None
    assert report.taskspec_cases[0]["passed"] is False
    assert report.taskspec_cases[1]["passed"] is True


def test_platform_vllm_status_uses_process_exit_code_and_structured_report() -> None:
    text = Path("scripts/run_platform_acceptance.py").read_text(encoding="utf-8")
    assert "vllm_rc == 0" in text
    assert "vllm_acceptance.stdout.log" in text
    assert 'summary.get("taskspec_passed") == 3' in text
    assert 'summary.get("fallback_detected") is False' in text
