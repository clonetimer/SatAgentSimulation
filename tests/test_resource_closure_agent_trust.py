from __future__ import annotations

from copy import deepcopy

from integration.qoi_profiles.resource_closure import run_resource_closure_profiles
from sat_sim.agent_facade import AgentFacadeRequest, AgentFacadeStep, _backend_identity
from sat_sim.agent_policy import decide_agent_interaction
from sat_sim.capability_planner import CapabilityPlanResult, MissingParameter
from sat_sim.capability_registry import validate_spec_against_capability
from sat_sim.coupling_gate import build_coupling_completeness_gate
from sat_sim.coupling_requirements import validate_declared_required_couplings
from sat_sim.scenario_templates import instantiate_scenario_template


def _plan(capability_id: str, *missing: MissingParameter) -> CapabilityPlanResult:
    return CapabilityPlanResult(
        request="test",
        supported=True,
        selected_capability_id=capability_id,
        selected_task_type=capability_id.split(".", 1)[0],
        missing_parameters=tuple(missing),
        allowed_capabilities=(capability_id,),
    )


def test_resource_closure_profiles_close_all_seven_p1_links() -> None:
    report = run_resource_closure_profiles()
    assert report["status"] == "PASS"
    assert report["pass_count"] == 7
    assert report["fail_count"] == 0
    assert len(report["checks"]) == 7
    assert all(item["status"] == "PASS" for item in report["checks"])


def test_quantitative_request_requires_critical_physical_parameter_confirmation() -> None:
    missing = MissingParameter(
        name="battery_capacity_wh",
        source="capability.default",
        policy="defaultable",
        default=160.0,
        unit="Wh",
    )
    decision = decide_agent_interaction(
        "请做整星能源裕度的工程定量验收",
        plan=_plan("whole_spacecraft.unified_native.v1", missing),
    )
    assert decision.action == "ask_clarifying_question"
    assert decision.can_generate_script is False
    assert [item.field for item in decision.questions] == ["battery_capacity_wh"]


def test_generic_demo_request_can_use_recorded_defaults() -> None:
    missing = MissingParameter(
        name="battery_capacity_wh",
        source="capability.default",
        policy="defaultable",
        default=160.0,
        unit="Wh",
    )
    decision = decide_agent_interaction(
        "创建一个整星统一运行图演示",
        plan=_plan("whole_spacecraft.unified_native.v1", missing),
    )
    assert decision.action == "generate_minimal_script_with_defaults"
    assert decision.can_generate_script is True
    assert decision.defaulted_parameters[0]["name"] == "battery_capacity_wh"


def test_agent_rejects_cross_subsystem_causality_on_single_subsystem_capability() -> None:
    decision = decide_agent_interaction(
        "验证低电量关闭载荷后停止数据生成",
        plan=_plan("subsystem.eps.unified_native.v1"),
    )
    assert decision.action == "reject_missing_physical_coupling"
    assert decision.can_generate_script is False
    assert any(
        item.get("required_coupling_id") == "eps_pdu_to_payload_activity"
        for item in decision.unsupported_requirements
    )


def test_declared_required_coupling_is_blocked_for_non_whole_capability() -> None:
    spec = {"mission": {"required_couplings": ["eps_pdu_to_payload_activity"]}}
    issues = validate_declared_required_couplings(spec, supported_couplings=set())
    assert len(issues) == 1
    assert issues[0]["code"] == "MISSING_PHYSICAL_CAUSAL_LINK"


def test_unknown_parameter_is_not_silently_ignored() -> None:
    spec = instantiate_scenario_template("whole_spacecraft_unified_native", task_id="v0563_unknown_param")
    spec = deepcopy(spec)
    spec["parameters"]["values"]["imaginary_power_knob"] = 123.0
    issues = validate_spec_against_capability(spec)
    assert any(item.code == "UNCONSUMED_PARAMETER" for item in issues)


def test_backend_identity_distinguishes_template_from_unverified_model_request() -> None:
    template = _backend_identity(AgentFacadeRequest(request="demo", backend="template"))
    assert template["backend_class"] == "deterministic_template"
    assert template["actual_model_used"] is False
    assert template["actual_model_execution_verified"] is True

    unverified = _backend_identity(
        AgentFacadeRequest(request="demo", backend="openai_compatible", model_name="local-model"),
        steps=(AgentFacadeStep("backend.model_generate", "failed", "connection refused"),),
    )
    assert unverified["model_backend_requested"] is True
    assert unverified["actual_model_used"] is None
    assert unverified["actual_model_execution_verified"] is False
    assert unverified["claim"] == "model_backend_requested_not_verified"

    evidence = {
        "verified": True,
        "model_id": "local-model",
        "request_id": "response-1",
        "prompt_sha256": "a" * 64,
        "response_sha256": "b" * 64,
    }
    verified = _backend_identity(
        AgentFacadeRequest(request="demo", backend="openai_compatible", model_name="local-model"),
        steps=(AgentFacadeStep("backend.model_invocation", "complete", payload=evidence),),
    )
    assert verified["actual_model_used"] is True
    assert verified["actual_model_execution_verified"] is True


def test_new_direct_pass_supersedes_historical_partial_in_coupling_gate() -> None:
    gate = build_coupling_completeness_gate([
        {"checks": [{"id": "eps_pdu_to_payload_activity", "status": "PARTIAL"}]},
        {"checks": [{"id": "eps_pdu_to_payload_activity", "status": "PASS"}]},
    ])
    assert gate["status"] == "PASS"
    assert gate["release_blocking_count"] == 0
    assert gate["advisory_partial_count"] == 0


def test_recommended_whole_runtime_uses_message_driven_pdu_and_resource_feedback() -> None:
    from sat_sim.capability_registry import get_adapter_for_capability, get_capability

    capability_id = "whole_spacecraft.unified_native.v1"
    contract = get_capability(capability_id)
    adapter = get_adapter_for_capability(capability_id)

    def spec(initial_soc: float) -> dict:
        return {
            "task_id": f"v0563_mainline_soc_{initial_soc}",
            "simulation": {
                "level": "whole_spacecraft",
                "duration_s": 8.0,
                "step_s": 0.2,
                "sample_s": 1.0,
                "backend": "basilisk",
            },
            "model": {"capability_id": capability_id},
            "assurance": {"allow_proxy": False},
            "parameters": {
                "values": {
                    "initial_soc": initial_soc,
                    "payload_max_pointing_error_deg": 180.0,
                }
            },
            "outputs": {"plots": []},
        }

    nominal = adapter.run(spec(0.62), contract.data)
    low = adapter.run(spec(0.15), contract.data)

    assert nominal.summary["resource_feedback_closure_status"] == "PASS"
    # A constant enabled heater proves activity-to-power coupling, but not a
    # PDU transition.  v0.5.6.5 no longer treats field presence as transition
    # evidence.
    assert nominal.summary["heater_pdu_power_feedback_observed"] is False
    assert nominal.summary["resource_feedback_bridge_health"]["heater"]["stimulus_observed"] is True
    assert max(row["eps.loads.payload_power_w"] for row in nominal.trace_rows) > 0.0
    assert max(row["eps.loads.comm_power_w"] for row in nominal.trace_rows) > 0.0
    assert max(row["eps.loads.adcs_power_w"] for row in nominal.trace_rows) > 0.0
    assert max(row["eps.loads.heater_power_w"] for row in nominal.trace_rows) > 0.0

    active_rows = low.trace_rows[1:]
    assert active_rows
    assert all(row["eps.pdu.payload_enabled"] == 0 for row in active_rows)
    assert all(row["eps.pdu.comm_enabled"] == 0 for row in active_rows)
    assert all(row["eps.pdu.heater_enabled"] == 0 for row in active_rows)
    assert all(row["payload.generated_bps"] == 0.0 for row in active_rows)
    assert all(row["comm.downlink_bps"] == 0.0 for row in active_rows)
    assert all(row["eps.loads.payload_power_w"] == 0.0 for row in active_rows)
    assert all(row["eps.loads.comm_power_w"] == 0.0 for row in active_rows)
    assert all(row["eps.loads.heater_power_w"] == 0.0 for row in active_rows)
