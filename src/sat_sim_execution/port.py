"""Execution port protocol and registry-backed implementation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .contracts import ExecutionRequest
from .result import RunResult


@runtime_checkable
class ModelExecutionAdapter(Protocol):
    adapter_key: str

    def execute(self, request: ExecutionRequest) -> RunResult:
        ...


@runtime_checkable
class ModelExecutionPort(Protocol):
    def execute(self, request: ExecutionRequest) -> RunResult:
        ...


@dataclass(frozen=True)
class RegistryExecutionPort:
    registry: "ExecutionAdapterRegistry"

    def execute(self, request: ExecutionRequest) -> RunResult:
        return self.registry.execute(request)


from .registry import ExecutionAdapterRegistry  # noqa: E402  (type cycle only)

__all__ = ["ModelExecutionAdapter", "ModelExecutionPort", "RegistryExecutionPort"]
