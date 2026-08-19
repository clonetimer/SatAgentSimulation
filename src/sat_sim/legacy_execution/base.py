"""Shared validation for A2R Legacy execution adapters."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import ClassVar

from sat_sim_execution import ExecutionRequest, RunResult

from .runner_bridge import execute_legacy_runner


@dataclass(frozen=True)
class LegacyExecutionAdapterBase:
    adapter_key: ClassVar[str]
    route: ClassVar[str]

    def accepts(self, compiled: object) -> bool:
        raise NotImplementedError

    def execute(self, request: ExecutionRequest) -> RunResult:
        payload = request.legacy_payload
        if not isinstance(payload, Mapping) or payload.get("compiled") is None:
            raise TypeError(f"{self.adapter_key} requires a compiled task in legacy_payload")
        compiled = payload["compiled"]
        if not self.accepts(compiled):
            raise ValueError(
                f"compiled task_type={getattr(compiled, 'task_type', None)!r} is incompatible with {self.adapter_key}"
            )
        return execute_legacy_runner(request, route=self.route)


__all__ = ["LegacyExecutionAdapterBase"]
