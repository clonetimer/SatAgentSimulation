from __future__ import annotations

from copy import deepcopy
from dataclasses import fields
from pathlib import Path

from sat_sim.agent_policy import decide_agent_interaction
from sat_sim.capability_planner import CapabilityPlanResult
from sat_sim.capability_registry import (
    capability_summary_payload,
    get_capability,
    list_capabilities,
    validate_capability_integration_registry,
)
from sat_sim.coupling_requirements import (
    attach_required_couplings,
    required_couplings_for_text,
)
from sat_sim.execution_planner import plan_task_spec
from sat_sim.scenario_templates import instantiate_scenario_template
from sat_sim.task_models import canonicalize_task_spec, to_runtime_task_spec
from sat_sim.task_validator import validate_task_spec
from whole_spacecraft.schemas import WholeSpacecraftCouplingConfig


def _plan(capability_id: str) -> CapabilityPlanResult:
    return CapabilityPlanResult(
        request="test",
        supported=True,
        selected_capability_id=capability_id,
        selected_task_type=capability_id.split(".", 1)[0],
        allowed_capabilities=(capability_id,),
    )


def test_explicit_event_identifier_does_not_invent_physical_coupling() -> None:
    assert required_couplings_for_text(
        "使用事件 effect=false_eclipse，在2秒注入"
    ) == ()
    assert required_couplings_for_text(
        "食影必须影响太阳阵列发电和电池SOC"
    )


def test_all_capabilities_have_complete_model_library_integration_contracts() -> None:
    capabilities = list_capabilities()
    assert len(capabilities) == 60
    assert validate_capability_integration_registry() == []
    for capability in capabilities:
        integration = capability.integration_contract
        implementation = capability.data.get("implementation") or {}
        assert integration.source_modules == list(implementation.get("model_modules") or [])
        assert integration.source_modules
        assert set(integration.supported_couplings).isdisjoint(integration.unsupported_couplings)
        assert integration.implementation_class in {
            "official_basilisk_native",
            "mixed_native_project",
            "project_deterministic_model",
            "engineering_proxy",
            "orchestration_adapter",
        }


def test_capability_summary_exposes_source_modules_couplings_and_model_class() -> None:
    rows = {item["capability_id"]: item for item in capability_summary_payload()}
    whole = rows["whole_spacecraft.unified_native.v1"]["integration_contract"]
    assert "sat_sim.bsk_engine.unified_native" in whole["source_modules"]
    assert "eps_pdu_to_payload_activity" in whole["supported_couplings"]
    assert "adcs_pointing_to_comm_gate" in whole["unsupported_couplings"]
    assert whole["implementation_class"] == "mixed_native_project"


def test_required_couplings_propagate_to_runtime_resolved_spec_and_execution_plan() -> None:
    spec = instantiate_scenario_template("whole_spacecraft_unified_native", task_id="v0564_causal_chain")
    spec.setdefault("mission", {})["required_couplings"] = [
        "eps_pdu_to_payload_activity",
        "payload_activity_to_eps_thermal",
    ]
    validation = validate_task_spec(spec)
    assert validation.ok, validation.to_dict()

    canonical = canonicalize_task_spec(spec)
    runtime = to_runtime_task_spec(canonical)
    assert runtime["mission"]["required_couplings"] == [
        "eps_pdu_to_payload_activity",
        "payload_activity_to_eps_thermal",
    ]

    planning = plan_task_spec(canonical)
    assert planning.ok, planning.validation.to_dict()
    assert planning.resolved_spec is not None
    assert planning.execution_plan is not None
    assert planning.resolved_spec.required_couplings == runtime["mission"]["required_couplings"]
    assert planning.resolved_spec.source_modules == [
        "sat_sim.bsk_engine.unified_native",
        "sat_sim.bsk_engine.project_native_modules",
        "subsystems.comm_data.builder",
        "subsystems.thermal.thermal_network",
        "integration.resource_feedback",
    ]
    node = next(item for item in planning.execution_plan.nodes if item.node_id == "validate_causal_requirements")
    assert node.metadata["required_couplings"] == runtime["mission"]["required_couplings"]


def test_whole_spacecraft_level_is_not_a_wildcard_for_missing_causal_links() -> None:
    spec = instantiate_scenario_template("whole_spacecraft_unified_native", task_id="v0564_comm_pointing_block")
    spec.setdefault("mission", {})["required_couplings"] = ["adcs_pointing_to_comm_gate"]
    validation = validate_task_spec(spec)
    assert not validation.ok
    assert any(item.code == "MISSING_PHYSICAL_CAUSAL_LINK" for item in validation.errors)

    decision = decide_agent_interaction(
        "验证姿态误差影响下行通信",
        plan=_plan("whole_spacecraft.unified_native.v1"),
    )
    assert decision.action == "reject_missing_physical_coupling"


def test_composite_capability_accepts_declared_pointing_to_comm_causal_link() -> None:
    spec = instantiate_scenario_template("whole_nominal", task_id="v0564_composite_comm_pointing")
    spec.setdefault("mission", {})["required_couplings"] = ["adcs_pointing_to_comm_gate"]
    validation = validate_task_spec(spec)
    assert validation.ok, validation.to_dict()


def test_agent_detected_causal_intent_is_persisted_not_only_checked_in_memory() -> None:
    spec = instantiate_scenario_template("whole_spacecraft_unified_native", task_id="v0564_agent_attach")
    updated, attached = attach_required_couplings(spec, "验证低电量关闭载荷后停止数据生成并降低载荷热耗")
    assert "eps_pdu_to_payload_activity" in attached
    assert "payload_activity_to_eps_thermal" in attached
    assert updated["mission"]["required_couplings"] == list(attached)
    assert validate_task_spec(updated).ok


def test_required_core_spacecraft_environment_link_has_no_fake_disable_switch() -> None:
    names = {item.name for item in fields(WholeSpacecraftCouplingConfig)}
    assert "enable_adcs_spacecraft_to_orbit_environment" not in names
    contract = get_capability("whole_spacecraft.composite_digital_twin.v1")
    description = contract.data["parameters"]["coupling"]["description"]
    assert "required" in description.lower()


def test_runtime_accepts_causal_validation_plan_node(tmp_path: Path) -> None:
    from sat_sim.run_bundle import execute_prepared_run, prepare_run

    spec = instantiate_scenario_template("whole_spacecraft_unified_native", task_id="v0564_runtime_causal_node")
    spec["simulation"].update(duration_s=2, step_s=1, sample_s=1)
    spec.setdefault("mission", {})["required_couplings"] = ["eps_pdu_to_payload_activity"]
    prepared = prepare_run(spec, output_root=tmp_path / "runs")
    result = execute_prepared_run(
        prepared.bundle_root,
        expected_plan_sha256=prepared.execution_plan_sha256,
        max_attempts=1,
    )
    import json

    assert result.run_record.status.value == "SUCCEEDED"
    trace = json.loads((Path(result.bundle_root) / "runtime" / "execution_trace.json").read_text(encoding="utf-8"))
    assert any(event["node_id"] == "validate_causal_requirements" for event in trace["events"])
