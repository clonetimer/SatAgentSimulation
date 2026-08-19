"""Fail-closed registered telecommand catalog and role validation."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .models import ActorRole, Telecommand

ROLE_LEVEL = {
    ActorRole.VIEWER: 0,
    ActorRole.OPERATOR: 1,
    ActorRole.FAULT_OPERATOR: 2,
    ActorRole.ADMIN: 3,
}


class CommandValidationError(ValueError):
    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(detail)
        self.reason_code = reason_code


@dataclass(frozen=True)
class CommandContract:
    operation: str
    target: str
    required_role: ActorRole
    parameter_schema: dict[str, Any]
    units: dict[str, str]
    effect_fields: tuple[str, ...]


class CommandCatalog:
    def __init__(self, contracts: tuple[CommandContract, ...]) -> None:
        self._contracts = {contract.operation: contract for contract in contracts}
        if len(self._contracts) != len(contracts):
            raise ValueError("duplicate command operation in catalog")

    @classmethod
    def from_matrix(cls, path: Path) -> "CommandCatalog":
        payload = json.loads(path.read_text(encoding="utf-8"))
        contracts: list[CommandContract] = []
        for capability in payload["initial_release_capabilities"]:
            target = str(capability["object_id"])
            for raw in capability["commands"]:
                properties = dict(raw["parameters"])
                schema = {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "additionalProperties": False,
                    "required": sorted(properties),
                    "properties": properties,
                }
                Draft202012Validator.check_schema(schema)
                contracts.append(CommandContract(
                    operation=str(raw["operation"]),
                    target=target,
                    required_role=ActorRole(str(raw["required_role"])),
                    parameter_schema=schema,
                    units={str(key): str(value) for key, value in raw["units"].items()},
                    effect_fields=tuple(str(value) for value in raw["effect_fields"]),
                ))
        return cls(tuple(contracts))

    @classmethod
    def default(cls) -> "CommandCatalog":
        root = Path(__file__).resolve().parents[3]
        return cls.from_matrix(root / "configs" / "interactive" / "realtime_capability_matrix.json")

    def get(self, operation: str) -> CommandContract:
        try:
            return self._contracts[operation]
        except KeyError as exc:
            raise CommandValidationError("UNKNOWN_OPERATION", f"unregistered operation: {operation}") from exc

    def validate(self, command: Telecommand) -> CommandContract:
        contract = self.get(command.operation)
        if command.target != contract.target:
            raise CommandValidationError("TARGET_MISMATCH", f"operation {command.operation} requires target {contract.target}")
        if ROLE_LEVEL[command.actor_role] < ROLE_LEVEL[contract.required_role]:
            raise CommandValidationError("AUTHORIZATION_DENIED", f"role {command.actor_role} cannot execute {command.operation}")
        errors = sorted(Draft202012Validator(contract.parameter_schema).iter_errors(command.parameters), key=lambda item: list(item.path))
        if errors:
            detail = "; ".join(error.message for error in errors[:3])
            raise CommandValidationError("PARAMETER_VALIDATION_FAILED", detail)
        return contract

    def operations(self) -> tuple[str, ...]:
        return tuple(sorted(self._contracts))

    def public_contracts(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "operation": contract.operation,
                "target": contract.target,
                "required_role": contract.required_role.value,
                "parameter_schema": contract.parameter_schema,
                "units": contract.units,
                "effect_fields": contract.effect_fields,
            }
            for contract in sorted(self._contracts.values(), key=lambda item: item.operation)
        )
