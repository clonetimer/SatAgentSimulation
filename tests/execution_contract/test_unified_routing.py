from __future__ import annotations

import os
import subprocess
import sys

import pytest

from sat_sim.task_compiler import CompiledTask
from sat_sim.task_spec import TaskSpecError
from sat_sim.unified_execution import (
    ModelAssetResolverRegistry,
    adapter_key_for_compiled,
    build_execution_request,
    create_default_execution_registry,
    create_default_model_asset_resolver_registry,
)


def compiled(task_type: str, *, capability: bool = False) -> CompiledTask:
    return CompiledTask(
        schema_version="test",
        task_id=f"{task_type}-task",
        task_type=task_type,
        mode="nominal",
        backend="python",
        runner="example.runner",
        spec_hash="0" * 64,
        simulation={},
        outputs={},
        metadata={"capability_id": "component.test.v1"} if capability else {},
    )


def test_default_registry_contains_registered_capability_a3_to_a5_and_explicit_legacy_routes() -> None:
    assert create_default_execution_registry().keys() == (
        "basilisk.attitude_control_graph",
        "basilisk.comm_data_unified_graph",
        "basilisk.composite_digital_twin_graph",
        "basilisk.eps_unified_graph",
        "basilisk.propulsion_unified_graph",
        "capability.registered_adapter",
        "legacy.capability",
        "legacy.module",
        "legacy.orbit_environment",
        "legacy.whole_spacecraft",
        "python.thermal_source_native_graph",
    )


def test_default_model_asset_resolver_registry_contains_a3_to_a5_routes() -> None:
    assert create_default_model_asset_resolver_registry().keys() == (
        "basilisk.attitude_control_graph",
        "basilisk.comm_data_unified_graph",
        "basilisk.composite_digital_twin_graph",
        "basilisk.eps_unified_graph",
        "basilisk.propulsion_unified_graph",
        "python.thermal_source_native_graph",
    )


def test_model_asset_resolver_registry_rejects_duplicate_and_unknown_routes() -> None:
    def dummy_resolver(task_spec: object, *, requested_outputs: object = ()) -> object:
        return {"task_spec": task_spec, "requested_outputs": requested_outputs}

    registry = ModelAssetResolverRegistry()
    registry.register("test.resolver", dummy_resolver, diagnostics_key="test_diagnostics")

    assert registry.resolve("test.resolver", {"mode": "nominal"}, requested_outputs=("q",)) == (
        {"task_spec": {"mode": "nominal"}, "requested_outputs": ("q",)},
        "test_diagnostics",
    )
    with pytest.raises(TaskSpecError, match="duplicate model-asset resolver"):
        registry.register("test.resolver", dummy_resolver, diagnostics_key="duplicate")
    with pytest.raises(TaskSpecError, match="no model-asset resolver registered"):
        registry.resolve("missing.resolver", {}, requested_outputs=())


def test_adapter_route_is_derived_internally_from_compiled_task() -> None:
    assert adapter_key_for_compiled(compiled("component", capability=True)) == "capability.registered_adapter"
    assert adapter_key_for_compiled(compiled("component")) == "legacy.module"
    assert adapter_key_for_compiled(compiled("subsystem")) == "legacy.module"
    assert adapter_key_for_compiled(compiled("whole_spacecraft")) == "legacy.whole_spacecraft"
    assert adapter_key_for_compiled(compiled("orbit_environment")) == "legacy.orbit_environment"


def test_request_uses_plan_hash_and_auditable_legacy_metadata() -> None:
    task = compiled("component")
    request = build_execution_request(
        task,
        {"parameters": {"gain": 2}, "outputs": {"fields": ["x"]}},
        execution_plan_sha256="c" * 64,
        request_id="attempt-1",
        write_dataset=False,
    )
    assert request.adapter_key == "legacy.module"
    assert request.execution_plan_sha256 == "c" * 64
    assert request.parameters == {"gain": 2}
    assert request.requested_outputs == ("x",)
    assert request.metadata["historical_runner"] == "example.runner"


def test_default_registry_construction_does_not_import_basilisk() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(__import__("pathlib").Path(__file__).resolve().parents[2] / "src")
    code = (
        "import sys; "
        "from sat_sim.unified_execution import create_default_execution_registry; "
        "create_default_execution_registry(); "
        "assert not any(name == 'Basilisk' or name.startswith('Basilisk.') for name in sys.modules)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
