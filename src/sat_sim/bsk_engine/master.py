"""Project-owned BSKSim-style master container."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .types import BSKProcessSpec, BSKTaskSpec


def _sec_to_nano(seconds: float) -> int:
    try:
        from Basilisk.utilities import macros  # type: ignore
        return int(macros.sec2nano(float(seconds)))
    except Exception:
        return int(float(seconds) * 1_000_000_000)


@dataclass
class SatelliteBSKSim:
    """Small BSKSim-style orchestration object.

    The object owns the Basilisk ``SimBaseClass`` when Basilisk is available and
    records process/task declarations regardless of runtime availability.  This
    lets TaskSpec validation and UI previews work on machines without Basilisk,
    while real execution still uses Basilisk when installed.
    """

    use_basilisk: bool = True
    process_specs: list[BSKProcessSpec] = field(default_factory=list)
    task_specs: list[BSKTaskSpec] = field(default_factory=list)
    _sim: Any | None = None
    _processes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.use_basilisk:
            return
        try:
            from Basilisk.utilities import SimulationBaseClass  # type: ignore
            self._sim = SimulationBaseClass.SimBaseClass()
        except Exception:
            self._sim = None

    @property
    def basilisk_available(self) -> bool:
        return self._sim is not None

    def create_process(self, name: str, *, role: str, description: str = "") -> None:
        if name not in {p.name for p in self.process_specs}:
            self.process_specs.append(BSKProcessSpec(name=name, role=role, description=description))
        if self._sim is not None and name not in self._processes:
            self._processes[name] = self._sim.CreateNewProcess(name)

    def create_task(self, name: str, *, process: str, rate_s: float, description: str = "") -> None:
        if name not in {t.name for t in self.task_specs}:
            self.task_specs.append(BSKTaskSpec(name=name, process=process, rate_s=float(rate_s), description=description))
        if self._sim is not None:
            if process not in self._processes:
                self.create_process(process, role="auto", description="Auto-created process for task declaration")
            task = self._sim.CreateNewTask(name, _sec_to_nano(rate_s))
            self._processes[process].addTask(task)

    def run_empty_timeline(self, duration_s: float) -> dict[str, Any]:
        """Run the declared timeline even before all modules are migrated."""
        if self._sim is None:
            return {"basilisk_available": False, "executed": False}
        self._sim.InitializeSimulation()
        self._sim.ConfigureStopTime(_sec_to_nano(duration_s))
        self._sim.ExecuteSimulation()
        return {"basilisk_available": True, "executed": True}
