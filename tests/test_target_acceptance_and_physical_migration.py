from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import urllib.error

import yaml

from sat_sim.capability_registry import get_adapter_for_capability, get_capability
from sat_sim.lmstudio_acceptance import AcceptanceCase, run_lmstudio_acceptance
from sat_sim.execution_planner import plan_task_spec
from sat_sim.scenario_templates import get_scenario_template


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


def _fake_agent_result(capability_id: str, outputs: list[str]):
    return SimpleNamespace(
        route=SimpleNamespace(provider_id="local-lmstudio"),
        validation=SimpleNamespace(ok=True),
        guards=SimpleNamespace(ok=True),
        compiled=object(),
        task_spec={
            "model": {"capability_id": capability_id},
            "outputs": {"plots": outputs},
        },
        reason_codes=(),
        ok=True,
        facade_result=_verified_facade(),
    )


def test_lmstudio_unreachable_is_not_reported_as_pass(monkeypatch, tmp_path: Path) -> None:
    def unreachable(*args, **kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("sat_sim.lmstudio_acceptance._request_json", unreachable)
    report = run_lmstudio_acceptance(
        base_url="http://127.0.0.1:65534/v1",
        output_dir=tmp_path,
        timeout_s=0.1,
    )
    assert report.ok is False
    assert report.overall_status == "NOT_EXECUTED_LMSTUDIO_UNREACHABLE"
    saved = json.loads((tmp_path / "lmstudio_acceptance_report.json").read_text(encoding="utf-8"))
    assert saved["execution_kind"] == "actual_lmstudio"


def test_protocol_fixture_cannot_be_upgraded_to_actual_lmstudio(monkeypatch, tmp_path: Path) -> None:
    def fake_request(url: str, **kwargs):
        if url.endswith("/v1/models"):
            return 200, {"data": [{"id": "fixture-model"}]}, 1.0
        if url.endswith("/api/v1/models"):
            return 200, {"models": [{"id": "fixture-model", "loaded": True, "max_context_length": 32768}]}, 1.0
        raise AssertionError(url)

    monkeypatch.setattr("sat_sim.lmstudio_acceptance._request_json", fake_request)
    monkeypatch.setattr(
        "sat_sim.lmstudio_acceptance.ModelProviderRegistry.probe_generation",
        lambda self, provider_id, timeout_s=None: {"ok": True, "structured_output_transport": "json_schema"},
    )
    monkeypatch.setattr(
        "sat_sim.lmstudio_acceptance.run_unified_agent",
        lambda request: _fake_agent_result("subsystem.adcs_unified_native.v1", ["attitude_error", "wheel_speed"]),
    )
    report = run_lmstudio_acceptance(
        base_url="http://fixture.invalid/v1",
        output_dir=tmp_path,
        execution_kind="protocol_fixture",
        cases=(AcceptanceCase(
            "fixture_case",
            "fixture",
            ("subsystem.adcs_unified_native.v1",),
            ("attitude_error", "wheel_speed"),
        ),),
    )
    assert report.ok is False
    assert report.overall_status == "PROTOCOL_FIXTURE_PASS_NOT_ACTUAL_LMSTUDIO"
    assert report.native_models["ok"] is True


def test_propulsion_unified_native_executes_real_basilisk_modules() -> None:
    spec = yaml.safe_load(Path("examples/subsystem_propulsion_unified_native_nominal.yaml").read_text(encoding="utf-8"))
    contract = get_capability("subsystem.propulsion.unified_native.v1")
    adapter = get_adapter_for_capability(contract.capability_id)
    assert adapter.validate(spec, contract.data) == ()
    planning = plan_task_spec(spec)
    assert planning.ok is True
    assert all(binding.binding_status == "resolved" for binding in planning.resolved_spec.output_bindings)
    result = adapter.run(spec, contract.data)

    assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded"
    assert result.summary["status"] == "PASS"
    assert result.summary["official_module_count"] == 3
    assert result.summary["project_native_module_count"] == 1
    assert result.summary["external_or_proxy_module_count"] == 0
    assert result.summary["propellant_used_kg"] > 0.0
    assert abs(result.summary["final_velocity_x_m_s"]) > 0.0
    assert set(result.metadata["model_source_boundary"]["basilisk_official_native"]) == {
        "spacecraft.Spacecraft",
        "thrusterDynamicEffector.ThrusterDynamicEffector",
        "fuelTank.FuelTank",
    }
    assert result.metadata["model_source_boundary"]["external_or_proxy"] == []

    preburn = [row for row in result.trace_rows if row["time_s"] < 0.5]
    active = [row for row in result.trace_rows if row["time_s"] >= 0.5]
    assert preburn and all(row["propulsion.thrust_force_n"] == 0.0 for row in preburn)
    assert any(row["propulsion.thrust_force_n"] > 0.0 for row in active)


def test_propulsion_templates_and_lifecycle_prefer_unified_native() -> None:
    burn = get_scenario_template("propulsion_burn")
    fuel = get_scenario_template("component_fuel_tank_nominal")
    thruster = get_scenario_template("component_thruster_nominal")
    assert burn["capability_id"] == "subsystem.propulsion.unified_native.v1"
    assert fuel["capability_id"] == "component.fuel_tank.v1"
    assert thruster["capability_id"] == "component.thruster.v1"
    assert burn["recommended"] is True

    current = get_capability("subsystem.propulsion.unified_native.v1")
    legacy = get_capability("subsystem.propulsion.source_native.v1")
    assert current.recommended is True
    assert current.exposed_to_agent is True
    assert legacy.lifecycle_status == "deprecated"
    assert legacy.replacement_capability_id == current.capability_id
    assert legacy.exposed_to_agent is False


def test_platform_acceptance_script_requires_actual_evidence() -> None:
    text = Path("scripts/run_platform_acceptance.py").read_text(encoding="utf-8")
    assert "sat-sim.platform-acceptance.v1" in text
    assert "platform.system()" in text
    assert "vllm_acceptance" in text
    assert "platform_acceptance_summary.json" in text
    assert 'unified_native_run' in text
