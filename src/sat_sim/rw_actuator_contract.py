"""Reaction-wheel actuator telemetry contract and motor-current derivation."""
from __future__ import annotations

import hashlib
import json
from importlib import resources
from pathlib import Path
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

RW_ACTUATOR_CONTRACT_SCHEMA_VERSION = "sat-sim.rw-actuator-telemetry-contract.v1"


class RWActuatorTelemetryContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = RW_ACTUATOR_CONTRACT_SCHEMA_VERSION
    contract_id: str
    status: str
    wheel_inertia_kg_m2: float = Field(gt=0)
    motor_torque_constant_nm_per_a: float = Field(gt=0)
    current_limit_a: float = Field(gt=0)
    command_source_message: str = "rwMotorTorqueOutMsg"
    current_formula: str = "I_A = tau_command_Nm / Kt_Nm_per_A"
    command_actual_time_alignment: str = "same_telemetry_sample"
    saturation_semantics: str = "signed_clip_to_current_limit"
    named_expert_frozen: bool = False
    approval_scope: str = "unapproved"
    approval_reference: str | None = None
    approved_by_id: str | None = None
    hardware_representative: bool = False
    flight_validated: bool = False
    contract_sha256: str = ""

    @model_validator(mode="after")
    def _hash(self) -> "RWActuatorTelemetryContract":
        payload = self.model_dump(exclude={"contract_sha256"})
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        object.__setattr__(self, "contract_sha256", digest)
        return self

    def motor_current_a(self, command_torque_nm: float) -> float:
        raw = float(command_torque_nm) / self.motor_torque_constant_nm_per_a
        return max(-self.current_limit_a, min(self.current_limit_a, raw))


def _contract_from_payload(raw: Mapping[str, Any]) -> RWActuatorTelemetryContract:
    params = raw["parameters"]
    formal_use = raw["formal_use"]
    return RWActuatorTelemetryContract(
        contract_id=raw["contract_id"], status=raw["status"],
        wheel_inertia_kg_m2=params["wheel_inertia_kg_m2"],
        motor_torque_constant_nm_per_a=params["motor_torque_constant_nm_per_a"],
        current_limit_a=params["current_limit_a"],
        command_source_message=raw["command_torque"]["source_message"],
        current_formula=raw["motor_current"]["formula"],
        command_actual_time_alignment=raw["command_torque"]["sampling_semantics"],
        saturation_semantics=raw["motor_current"]["saturation_semantics"],
        named_expert_frozen=not formal_use["named_expert_freeze_required"],
        approval_scope=formal_use.get("approval_scope", "unapproved"),
        approval_reference=formal_use.get("approval_reference"),
        approved_by_id=formal_use.get("approved_by_id"),
        hardware_representative=bool(formal_use.get("hardware_representative", False)),
        flight_validated=formal_use["flight_validated"],
    )


def load_rw_actuator_contract(path: str | Path) -> RWActuatorTelemetryContract:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return _contract_from_payload(raw)


def load_reference_rw_actuator_contract() -> RWActuatorTelemetryContract:
    ref = resources.files("sat_sim.actuator_contracts.adcs").joinpath("rw_engineering_reference_v1.json")
    return _contract_from_payload(json.loads(ref.read_text(encoding="utf-8")))


def contract_from_parameters(parameters: Mapping[str, Any]) -> RWActuatorTelemetryContract:
    ref = load_reference_rw_actuator_contract()
    kt = float(parameters.get("rw_motor_torque_constant_nm_per_a", ref.motor_torque_constant_nm_per_a))
    limit = float(parameters.get("rw_motor_current_limit_a", parameters.get("rw_max_torque_nm", 0.2) / kt))
    inertia = float(parameters.get("rw_wheel_inertia_kg_m2", ref.wheel_inertia_kg_m2))
    return ref.model_copy(update={
        "wheel_inertia_kg_m2": inertia,
        "motor_torque_constant_nm_per_a": kt,
        "current_limit_a": limit,
        "named_expert_frozen": bool(parameters.get("rw_actuator_contract_expert_frozen", False)),
    }).model_validate(ref.model_copy(update={
        "wheel_inertia_kg_m2": inertia,
        "motor_torque_constant_nm_per_a": kt,
        "current_limit_a": limit,
        "named_expert_frozen": bool(parameters.get("rw_actuator_contract_expert_frozen", False)),
    }).model_dump(exclude={"contract_sha256"}))


__all__ = [
    "RWActuatorTelemetryContract",
    "load_rw_actuator_contract",
    "load_reference_rw_actuator_contract",
    "contract_from_parameters",
]
