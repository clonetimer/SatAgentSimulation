from __future__ import annotations

from dataclasses import dataclass

import pytest

from sat_sim_execution import (
    AdapterNotFoundError,
    AdapterRegistrationError,
    ExecutionAdapterRegistry,
    ExecutionRequest,
    ExecutionRequestError,
    RunResult,
    RunState,
)


@dataclass(frozen=True)
class FakeAdapter:
    adapter_key: str = "test.adapter"

    def execute(self, request: ExecutionRequest) -> RunResult:
        return RunResult(
            request_id=request.request_id,
            task_id=request.task_id,
            execution_plan_sha256=request.execution_plan_sha256,
            adapter_key=self.adapter_key,
            state=RunState.SUCCEEDED,
            summary={"ok": True},
        )


def request(adapter_key: str = "test.adapter") -> ExecutionRequest:
    return ExecutionRequest(
        request_id="req-1",
        task_id="task-1",
        execution_plan_sha256="a" * 64,
        adapter_key=adapter_key,
    )


def test_registry_resolves_only_explicit_logical_keys() -> None:
    registry = ExecutionAdapterRegistry()
    registry.register_instance(FakeAdapter())
    result = registry.execute(request())
    assert result.state == RunState.SUCCEEDED
    assert registry.keys() == ("test.adapter",)


def test_registry_rejects_duplicates_unknown_keys_and_class_paths() -> None:
    registry = ExecutionAdapterRegistry()
    registry.register_instance(FakeAdapter())
    with pytest.raises(AdapterRegistrationError):
        registry.register_instance(FakeAdapter())
    with pytest.raises(AdapterNotFoundError):
        registry.resolve("missing.adapter")
    with pytest.raises(ExecutionRequestError):
        request("sat_sim.adapters:Adapter")
    with pytest.raises(ExecutionRequestError):
        request("../adapter")


def test_execution_request_hash_excludes_opaque_legacy_object() -> None:
    first = ExecutionRequest(
        request_id="req-1",
        task_id="task-1",
        execution_plan_sha256="b" * 64,
        adapter_key="test.adapter",
        parameters={"gain": 1.0},
        legacy_payload=object(),
    )
    second = ExecutionRequest(
        request_id="req-1",
        task_id="task-1",
        execution_plan_sha256="b" * 64,
        adapter_key="test.adapter",
        parameters={"gain": 1.0},
        legacy_payload={"different": object()},
    )
    assert first.request_sha256 == second.request_sha256
    assert first.contract_payload()["legacy_payload_present"] is True
