"""Engine-independent execution request contracts.

The package intentionally depends only on the Python standard library and
``sat_sim_kernel``.  Runtime-specific objects may appear only in the explicit
``legacy_payload`` compatibility slot during A2R migration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from collections.abc import Mapping, Sequence
from typing import Any

from sat_sim_kernel import ImplementationId, ModelRef, content_sha256

from .errors import ExecutionRequestError

EXECUTION_REQUEST_VERSION = "sat-sim.execution-request.v1"
_ADAPTER_KEY = re.compile(r"^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def validate_adapter_key(value: str) -> str:
    """Validate a logical adapter key and reject import/class-path syntax."""

    if not isinstance(value, str) or not _ADAPTER_KEY.fullmatch(value):
        raise ExecutionRequestError(f"invalid logical adapter_key: {value!r}")
    if any(token in value for token in (":", "/", "\\")):
        raise ExecutionRequestError("adapter_key must not contain import or filesystem syntax")
    return value


def _copy_mapping(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ExecutionRequestError(f"{label} must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise ExecutionRequestError(f"{label} requires string keys")
    return dict(value)


def _copy_mapping_sequence(value: Sequence[Mapping[str, Any]], label: str) -> tuple[dict[str, Any], ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise ExecutionRequestError(f"{label} must be a sequence of mappings")
    return tuple(_copy_mapping(item, label) for item in value)


@dataclass(frozen=True)
class ExecutionRequest:
    """One immutable request sent through ``ModelExecutionPort``."""

    request_id: str
    task_id: str
    execution_plan_sha256: str
    adapter_key: str
    model_ref: ModelRef | None = None
    implementation_id: ImplementationId | None = None
    capability_id: str | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    effects: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    requested_outputs: Sequence[str] = field(default_factory=tuple)
    resource_locks: Sequence[str] = field(default_factory=tuple)
    timeout_s: float = 300.0
    bundle_root: str | None = None
    output_root: str | None = None
    write_dataset: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)
    legacy_payload: Any = None
    schema_version: str = EXECUTION_REQUEST_VERSION

    def __post_init__(self) -> None:
        for value, label in ((self.request_id, "request_id"), (self.task_id, "task_id")):
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ExecutionRequestError(f"{label} must be a trimmed non-empty string")
        if not isinstance(self.execution_plan_sha256, str) or not _SHA256.fullmatch(self.execution_plan_sha256):
            raise ExecutionRequestError("execution_plan_sha256 must be a lowercase SHA-256 value")
        validate_adapter_key(self.adapter_key)
        if self.capability_id is not None and (not isinstance(self.capability_id, str) or not self.capability_id.strip()):
            raise ExecutionRequestError("capability_id must be None or a non-empty string")
        if not isinstance(self.timeout_s, (int, float)) or isinstance(self.timeout_s, bool) or self.timeout_s <= 0:
            raise ExecutionRequestError("timeout_s must be positive")
        outputs = tuple(self.requested_outputs)
        locks = tuple(self.resource_locks)
        if any(not isinstance(item, str) or not item.strip() for item in outputs):
            raise ExecutionRequestError("requested_outputs must contain non-empty strings")
        if any(not isinstance(item, str) or not item.strip() for item in locks):
            raise ExecutionRequestError("resource_locks must contain non-empty strings")
        object.__setattr__(self, "parameters", _copy_mapping(self.parameters, "parameters"))
        object.__setattr__(self, "effects", _copy_mapping_sequence(self.effects, "effects"))
        object.__setattr__(self, "requested_outputs", outputs)
        object.__setattr__(self, "resource_locks", locks)
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata, "metadata"))
        object.__setattr__(self, "timeout_s", float(self.timeout_s))
        if self.bundle_root is not None:
            object.__setattr__(self, "bundle_root", str(self.bundle_root))
        if self.output_root is not None:
            object.__setattr__(self, "output_root", str(self.output_root))

    def contract_payload(self) -> dict[str, Any]:
        """Return the auditable request data, excluding opaque Legacy objects."""

        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "task_id": self.task_id,
            "execution_plan_sha256": self.execution_plan_sha256,
            "adapter_key": self.adapter_key,
            "model_ref": str(self.model_ref) if self.model_ref is not None else None,
            "implementation_id": str(self.implementation_id) if self.implementation_id is not None else None,
            "capability_id": self.capability_id,
            "parameters": dict(self.parameters),
            "effects": [dict(item) for item in self.effects],
            "requested_outputs": list(self.requested_outputs),
            "resource_locks": list(self.resource_locks),
            "timeout_s": self.timeout_s,
            "bundle_root": self.bundle_root,
            "output_root": self.output_root,
            "write_dataset": self.write_dataset,
            "metadata": dict(self.metadata),
            "legacy_payload_present": self.legacy_payload is not None,
        }

    @property
    def request_sha256(self) -> str:
        return content_sha256(self.contract_payload())


__all__ = ["EXECUTION_REQUEST_VERSION", "ExecutionRequest", "validate_adapter_key"]
