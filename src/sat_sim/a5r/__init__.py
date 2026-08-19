"""A5R subsystem model-asset vertical slices with lazy runtime imports."""
from __future__ import annotations


def __getattr__(name: str):  # noqa: ANN001
    if name in {"SubsystemGraphExecutionAdapter", "create_subsystem_execution_adapters"}:
        from .adapter import SubsystemGraphExecutionAdapter, create_subsystem_execution_adapters
        return {
            "SubsystemGraphExecutionAdapter": SubsystemGraphExecutionAdapter,
            "create_subsystem_execution_adapters": create_subsystem_execution_adapters,
        }[name]
    if name in {"A5Resolution", "make_subsystem_resolver", "resolve_subsystem_execution"}:
        from .resolution import A5Resolution, make_subsystem_resolver, resolve_subsystem_execution
        return {
            "A5Resolution": A5Resolution,
            "make_subsystem_resolver": make_subsystem_resolver,
            "resolve_subsystem_execution": resolve_subsystem_execution,
        }[name]
    raise AttributeError(name)


__all__ = [
    "A5Resolution",
    "SubsystemGraphExecutionAdapter",
    "create_subsystem_execution_adapters",
    "make_subsystem_resolver",
    "resolve_subsystem_execution",
]
