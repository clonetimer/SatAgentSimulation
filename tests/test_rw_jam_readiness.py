from sat_sim.rw_jam_readiness import (
    ActuatorContractReadiness, BasiliskRuntimeEvidence, NativeCampaignReadiness,
    Phase3HReadinessGate, Phase3HReadinessRequest, ReadinessStatus,
    actuator_readiness_from_contract,
)
from sat_sim.rw_actuator_contract import load_reference_rw_actuator_contract


def test_current_package_is_blocked_by_real_interfaces_and_runtime():
    result = Phase3HReadinessGate().assess(Phase3HReadinessRequest(
        basilisk_runtime=BasiliskRuntimeEvidence(installed=False, error="missing"),
    ))
    assert result.status == ReadinessStatus.BLOCKED
    assert result.missing_channels == []
    assert "APPROVED_BASILISK_RUNTIME_NOT_READY" in result.blockers
    assert result.formal_training_ready is False
    assert "APPROVED_BASILISK_RUNTIME_NOT_READY" in result.blockers


def test_packaged_engineering_contract_is_frozen_for_simulation_only():
    contract = load_reference_rw_actuator_contract()
    readiness = actuator_readiness_from_contract(contract)

    assert contract.status == "frozen_engineering_simulation_baseline"
    assert contract.approval_scope == "engineering_simulation_only"
    assert contract.hardware_representative is False
    assert contract.flight_validated is False
    assert readiness.wheel_inertia_kg_m2 == 0.015
    assert readiness.motor_torque_constant_nm_per_a == 0.02
    assert readiness.current_limit_a == 10.0
    assert readiness.contract_sha256 == contract.contract_sha256
    assert len(readiness.contract_sha256 or "") == 64

    result = Phase3HReadinessGate().assess(Phase3HReadinessRequest(
        basilisk_runtime=BasiliskRuntimeEvidence(
            installed=True, distribution_version="2.11.0+satfix1",
            importable=True, approved_version=True, native_modules_verified=True,
        ),
        actuator_contract=readiness,
    ))
    assert result.status == ReadinessStatus.READY_FOR_NATIVE_18_CASE
    assert result.actuator_contract_ready is True
    assert result.blockers == ["NATIVE_18_CASE_NOT_EXECUTED"]


def test_closed_contract_is_ready_for_native_gate_but_not_promotion_before_execution():
    bundle = {
        "mappings": [{
            "fault_id": "ADCS_RW_JAM",
            "feature_contract": {"raw_features": [
                {"channel": name, "availability": "available", "required_for_formal_model_input": True, "model_input_eligible": True}
                for name in [
                    "sensor.adcs.rw.speed_rad_s_0", "command.adcs.rw.motor_torque_nm_0",
                    "estimate.adcs.rw.actual_torque_nm_0", "sensor.adcs.rw.motor_current_a_0",
                    "estimate.adcs.pointing_error_deg",
                ]
            ]},
        }]
    }
    result = Phase3HReadinessGate().assess(Phase3HReadinessRequest(
        basilisk_runtime=BasiliskRuntimeEvidence(
            installed=True, distribution_version="2.11.0", importable=True,
            approved_version=True, native_modules_verified=True,
        ),
        actuator_contract=ActuatorContractReadiness(
            wheel_inertia_frozen=True, motor_torque_constant_frozen=True, current_limit_frozen=True,
            command_actual_time_alignment_frozen=True, saturation_semantics_frozen=True,
            contract_sha256="a"*64, wheel_inertia_kg_m2=0.015,
            motor_torque_constant_nm_per_a=0.02, current_limit_a=10.0,
            approval_scope="engineering_simulation_only",
            approval_reference="test-approval", approved_by_id="test-reviewer",
        ),
        campaign=NativeCampaignReadiness(executed_native_cases=0, a_level_cases=0),
        mapping_bundle=bundle,
    ))
    assert result.status == ReadinessStatus.READY_FOR_NATIVE_18_CASE
    assert result.missing_channels == []
    assert result.formal_training_ready is False


def test_all_native_evidence_can_reach_promotion_readiness():
    bundle = {"mappings": [{"fault_id": "ADCS_RW_JAM", "feature_contract": {"raw_features": [
        {"channel": "sensor.adcs.rw.speed_rad_s_0", "availability": "available", "required_for_formal_model_input": True, "model_input_eligible": True},
    ]}}]}
    result = Phase3HReadinessGate().assess(Phase3HReadinessRequest(
        basilisk_runtime=BasiliskRuntimeEvidence(installed=True, distribution_version="2.11.0", importable=True, approved_version=True, native_modules_verified=True),
        actuator_contract=ActuatorContractReadiness(
            wheel_inertia_frozen=True, motor_torque_constant_frozen=True,
            current_limit_frozen=True, command_actual_time_alignment_frozen=True,
            saturation_semantics_frozen=True, contract_sha256="b"*64,
            wheel_inertia_kg_m2=0.015, motor_torque_constant_nm_per_a=0.02,
            current_limit_a=10.0, approval_scope="engineering_simulation_only",
            approval_reference="test-approval", approved_by_id="test-reviewer",
        ),
        campaign=NativeCampaignReadiness(executed_native_cases=18, a_level_cases=18), mapping_bundle=bundle,
    ))
    assert result.status == ReadinessStatus.READY_FOR_ASTROGRAPH_PROMOTION
    assert result.formal_training_ready is True


def test_native_execution_waits_for_separate_a_level_approval():
    result = Phase3HReadinessGate().assess(Phase3HReadinessRequest(
        basilisk_runtime=BasiliskRuntimeEvidence(
            installed=True, distribution_version="2.11.0+satfix1",
            importable=True, approved_version=True, native_modules_verified=True,
        ),
        actuator_contract=ActuatorContractReadiness(
            wheel_inertia_frozen=True, motor_torque_constant_frozen=True,
            current_limit_frozen=True, command_actual_time_alignment_frozen=True,
            saturation_semantics_frozen=True, contract_sha256="c"*64,
            wheel_inertia_kg_m2=0.015, motor_torque_constant_nm_per_a=0.02,
            current_limit_a=10.0, approval_scope="engineering_simulation_only",
            approval_reference="test-approval", approved_by_id="test-reviewer",
        ),
        campaign=NativeCampaignReadiness(executed_native_cases=18, a_level_cases=0),
    ))

    assert result.status == ReadinessStatus.READY_FOR_A_LEVEL_APPROVAL
    assert result.native_18_case_executed is True
    assert result.a_level_approved is False
    assert result.blockers == ["A_LEVEL_APPROVAL_NOT_COMPLETE"]
    assert result.formal_training_ready is False
