"""Explicit logical-key registry for execution adapters."""
from __future__ import annotations

from threading import RLock
from typing import Callable

from .contracts import ExecutionRequest, validate_adapter_key
from .errors import AdapterNotFoundError, AdapterRegistrationError, AdapterResultError
from .result import RunResult

AdapterFactory = Callable[[], object]


class ExecutionAdapterRegistry:
    """Thread-safe registry without dynamic imports or user-supplied class paths."""

    def __init__(self) -> None:
        self._factories: dict[str, AdapterFactory] = {}
        self._lock = RLock()

    def register(self, adapter_key: str, factory: AdapterFactory, *, replace: bool = False) -> None:
        key = validate_adapter_key(adapter_key)
        if not callable(factory):
            raise AdapterRegistrationError("adapter factory must be callable")
        with self._lock:
            if key in self._factories and not replace:
                raise AdapterRegistrationError(f"adapter_key already registered: {key}")
            self._factories[key] = factory

    def register_instance(self, adapter: object, *, replace: bool = False) -> None:
        key = getattr(adapter, "adapter_key", None)
        key = validate_adapter_key(key)
        self.register(key, lambda adapter=adapter: adapter, replace=replace)

    def resolve(self, adapter_key: str) -> object:
        key = validate_adapter_key(adapter_key)
        with self._lock:
            factory = self._factories.get(key)
        if factory is None:
            raise AdapterNotFoundError(f"no execution adapter registered for key: {key}")
        adapter = factory()
        if getattr(adapter, "adapter_key", None) != key or not callable(getattr(adapter, "execute", None)):
            raise AdapterRegistrationError(f"factory for {key!r} returned an incompatible adapter")
        return adapter

    def execute(self, request: ExecutionRequest) -> RunResult:
        adapter = self.resolve(request.adapter_key)
        result = adapter.execute(request)
        if not isinstance(result, RunResult):
            raise AdapterResultError(f"adapter {request.adapter_key!r} returned {type(result).__name__}, expected RunResult")
        if result.request_id != request.request_id or result.adapter_key != request.adapter_key:
            raise AdapterResultError("adapter result identity does not match request")
        return result

    def keys(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._factories))


__all__ = ["AdapterFactory", "ExecutionAdapterRegistry"]
