"""Phase 3-H readiness assessment for promoting RW_JAM evidence.

This module does not simulate and does not infer missing telemetry.  It audits the
package-resident mapping contract, the local Basilisk runtime, the planned native
campaign, and the actuator parameter contract.  The result is fail-closed.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .diagnostic_mapping_bundle import build_astrograph_diagnostic_mapping_bundle
from .rw_actuator_contract import RWActuatorTelemetryContract

PHASE3H_READINESS_SCHEMA_VERSION = "sat-sim.phase3h-rw-jam-readiness.v1"
RW_JAM_FAULT_ID = "ADCS_RW_JAM"


def _sha(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


class ReadinessStatus(str, Enum):
    BLOCKED = "BLOCKED"
    READY_FOR_NATIVE_18_CASE = "READY_FOR_NATIVE_18_CASE"
    READY_FOR_A_LEVEL_APPROVAL = "READY_FOR_A_LEVEL_APPROVAL"
    READY_FOR_ASTROGRAPH_PROMOTION = "READY_FOR_ASTROGRAPH_PROMOTION"


class BasiliskRuntimeEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    installed: bool
    distribution_name: str = "bsk"
    distribution_version: str | None = None
    importable: bool = False
    approved_version: bool = False
    native_modules_verified: bool = False
    error: str | None = None


class ActuatorContractReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wheel_inertia_frozen: bool = False
    motor_torque_constant_frozen: bool = False
    current_limit_frozen: bool = False
    command_actual_time_alignment_frozen: bool = False
    saturation_semantics_frozen: bool = False
    contract_id: str | None = None
    contract_status: str | None = None
    approval_scope: str | None = None
    approval_reference: str | None = None
    approved_by_id: str | None = None
    hardware_representative: bool = False
    contract_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    wheel_inertia_kg_m2: float | None = Field(default=None, gt=0)
    motor_torque_constant_nm_per_a: float | None = Field(default=None, gt=0)
    current_limit_a: float | None = Field(default=None, gt=0)


def actuator_readiness_from_contract(contract: RWActuatorTelemetryContract) -> ActuatorContractReadiness:
    frozen = (
        contract.status == "frozen_engineering_simulation_baseline"
        and contract.named_expert_frozen
        and contract.approval_scope == "engineering_simulation_only"
        and bool(contract.approval_reference)
        and bool(contract.approved_by_id)
        and not contract.hardware_representative
        and not contract.flight_validated
    )
    return ActuatorContractReadiness(
        wheel_inertia_frozen=frozen,
        motor_torque_constant_frozen=frozen,
        current_limit_frozen=frozen,
        command_actual_time_alignment_frozen=frozen and bool(contract.command_actual_time_alignment),
        saturation_semantics_frozen=frozen and bool(contract.saturation_semantics),
        contract_id=contract.contract_id,
        contract_status=contract.status,
        approval_scope=contract.approval_scope,
        approval_reference=contract.approval_reference,
        approved_by_id=contract.approved_by_id,
        hardware_representative=contract.hardware_representative,
        contract_sha256=contract.contract_sha256,
        wheel_inertia_kg_m2=contract.wheel_inertia_kg_m2,
        motor_torque_constant_nm_per_a=contract.motor_torque_constant_nm_per_a,
        current_limit_a=contract.current_limit_a,
    )


class NativeCampaignReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_count: int = Field(default=18, ge=0)
    pair_count: int = Field(default=9, ge=0)
    task_spec_failures: int = Field(default=0, ge=0)
    executed_native_cases: int = Field(default=0, ge=0)
    a_level_cases: int = Field(default=0, ge=0)


class Phase3HReadinessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    basilisk_runtime: BasiliskRuntimeEvidence
    actuator_contract: ActuatorContractReadiness = Field(default_factory=ActuatorContractReadiness)
    campaign: NativeCampaignReadiness = Field(default_factory=NativeCampaignReadiness)
    mapping_bundle: dict[str, Any] | None = None


class Phase3HReadinessAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = PHASE3H_READINESS_SCHEMA_VERSION
    status: ReadinessStatus
    fault_id: str = RW_JAM_FAULT_ID
    required_channels: list[str]
    available_channels: list[str]
    missing_channels: list[str]
    model_input_channels: list[str]
    basilisk_runtime_ready: bool
    actuator_contract_ready: bool
    native_18_case_plan_ready: bool
    native_18_case_executed: bool
    a_level_approved: bool
    a_level_data_count: int
    formal_training_ready: bool = False
    blockers: list[str]
    next_actions: list[str]
    assessment_sha256: str = ""


def inspect_basilisk_runtime(*, approved_versions: tuple[str, ...] = ("2.11.0", "2.11.0+satfix1")) -> BasiliskRuntimeEvidence:
    try:
        version = importlib.metadata.version("bsk")
    except importlib.metadata.PackageNotFoundError:
        return BasiliskRuntimeEvidence(installed=False, error="bsk distribution is not installed")
    try:
        import Basilisk  # type: ignore
        from Basilisk.simulation import reactionWheelStateEffector  # type: ignore
        native_ok = reactionWheelStateEffector is not None
        return BasiliskRuntimeEvidence(
            installed=True, distribution_version=version, importable=True,
            approved_version=version in approved_versions, native_modules_verified=native_ok,
        )
    except Exception as exc:
        return BasiliskRuntimeEvidence(
            installed=True, distribution_version=version, importable=False,
            approved_version=version in approved_versions, native_modules_verified=False,
            error=f"{type(exc).__name__}: {exc}",
        )


class Phase3HReadinessGate:
    def assess(self, request: Phase3HReadinessRequest) -> Phase3HReadinessAssessment:
        bundle = request.mapping_bundle or build_astrograph_diagnostic_mapping_bundle()
        mapping = next(item for item in bundle.get("mappings", []) if item.get("fault_id") == RW_JAM_FAULT_ID)
        raw = (mapping.get("feature_contract") or {}).get("raw_features") or []
        required = sorted(item["channel"] for item in raw if item.get("required_for_formal_model_input"))
        available = sorted(item["channel"] for item in raw if item.get("availability") == "available")
        missing = sorted(set(required) - set(available))
        model_inputs = sorted(item["channel"] for item in raw if item.get("model_input_eligible"))
        blockers: list[str] = []
        blockers.extend(f"MISSING_REQUIRED_CHANNEL:{item}" for item in missing)

        runtime = request.basilisk_runtime
        runtime_ready = all((runtime.installed, runtime.importable, runtime.approved_version, runtime.native_modules_verified))
        if not runtime_ready:
            blockers.append("APPROVED_BASILISK_RUNTIME_NOT_READY")

        actuator = request.actuator_contract
        actuator_ready = all((
            actuator.wheel_inertia_frozen,
            actuator.motor_torque_constant_frozen,
            actuator.current_limit_frozen,
            actuator.command_actual_time_alignment_frozen,
            actuator.saturation_semantics_frozen,
            bool(actuator.contract_sha256),
            actuator.wheel_inertia_kg_m2 is not None,
            actuator.motor_torque_constant_nm_per_a is not None,
            actuator.current_limit_a is not None,
            actuator.approval_scope == "engineering_simulation_only",
            bool(actuator.approval_reference),
            bool(actuator.approved_by_id),
            not actuator.hardware_representative,
        ))
        if not actuator_ready:
            blockers.append("ACTUATOR_TELEMETRY_CONTRACT_NOT_FROZEN")

        campaign = request.campaign
        plan_ready = campaign.case_count == 18 and campaign.pair_count == 9 and campaign.task_spec_failures == 0
        if not plan_ready:
            blockers.append("NATIVE_18_CASE_PLAN_NOT_READY")
        native_executed = campaign.executed_native_cases == 18
        a_level_approved = campaign.a_level_cases == 18
        if not native_executed:
            blockers.append("NATIVE_18_CASE_NOT_EXECUTED")
        elif not a_level_approved:
            blockers.append("A_LEVEL_APPROVAL_NOT_COMPLETE")

        if not blockers:
            status = ReadinessStatus.READY_FOR_ASTROGRAPH_PROMOTION
        elif runtime_ready and actuator_ready and plan_ready and not missing and native_executed:
            status = ReadinessStatus.READY_FOR_A_LEVEL_APPROVAL
        elif runtime_ready and actuator_ready and plan_ready and not missing:
            status = ReadinessStatus.READY_FOR_NATIVE_18_CASE
        else:
            status = ReadinessStatus.BLOCKED
        actions = []
        if "command.adcs.rw.motor_torque_nm_0" in missing:
            actions.append("Repair the projection from Basilisk rw_torque.rwMotorTorqueOutMsg; the native command message already exists.")
        if "sensor.adcs.rw.motor_current_a_0" in missing:
            actions.append("Derive signed motor current from rwMotorTorqueOutMsg using I=tau/Kt and the explicit current limit.")
        if not actuator_ready:
            actions.append("Freeze wheel inertia, torque constant, current limit, synchronization and saturation semantics as a hashed actuator contract.")
        if not runtime_ready:
            actions.append("Install and verify the approved bsk runtime; do not enable proxy fallback.")
        if plan_ready and not native_executed:
            actions.append("Execute the 18-case/9-pair native Basilisk gate and obtain named expert A-level approval.")
        elif native_executed and not a_level_approved:
            actions.append("Freeze the three diagnostic signatures and obtain named expert approval for all 9 native Basilisk pairs.")
        payload = {
            "schema_version": PHASE3H_READINESS_SCHEMA_VERSION,
            "status": status.value,
            "fault_id": RW_JAM_FAULT_ID,
            "required_channels": required,
            "available_channels": available,
            "missing_channels": missing,
            "model_input_channels": model_inputs,
            "basilisk_runtime_ready": runtime_ready,
            "actuator_contract_ready": actuator_ready,
            "native_18_case_plan_ready": plan_ready,
            "native_18_case_executed": native_executed,
            "a_level_approved": a_level_approved,
            "a_level_data_count": campaign.a_level_cases,
            "formal_training_ready": status == ReadinessStatus.READY_FOR_ASTROGRAPH_PROMOTION,
            "blockers": sorted(set(blockers)),
            "next_actions": actions,
            "assessment_sha256": "",
        }
        payload["assessment_sha256"] = _sha({k: v for k, v in payload.items() if k != "assessment_sha256"})
        return Phase3HReadinessAssessment.model_validate(payload)


__all__ = [
    "PHASE3H_READINESS_SCHEMA_VERSION", "RW_JAM_FAULT_ID", "ReadinessStatus",
    "BasiliskRuntimeEvidence", "ActuatorContractReadiness", "NativeCampaignReadiness",
    "Phase3HReadinessRequest", "Phase3HReadinessAssessment", "Phase3HReadinessGate",
    "actuator_readiness_from_contract", "inspect_basilisk_runtime",
]
