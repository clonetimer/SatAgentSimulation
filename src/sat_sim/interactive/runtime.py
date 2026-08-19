"""Runtime protocol for one persistent interactive simulation instance."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .models import Telecommand


@runtime_checkable
class PersistentSimulationRuntime(Protocol):
    @property
    def current_time_s(self) -> float: ...

    def prepare(self) -> None: ...

    def advance_to(self, stop_time_s: float) -> tuple[dict[str, Any], ...]: ...

    def apply_command(self, command: Telecommand) -> None: ...

    def read_delta(self) -> tuple[dict[str, Any], ...]: ...

    def finalize(self) -> dict[str, Any]: ...

    def abort(self) -> None: ...
