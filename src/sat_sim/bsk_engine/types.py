"""Typed contracts for the BSKSim-style execution foundation.

This layer intentionally mirrors the scene/dynamics/FSW split used by Basilisk
examples while keeping the public TaskSpec and Run Bundle contracts stable.  It
is not a wholesale copy of the official BSKSim tutorial scaffold; it is a small,
project-owned adapter layer that can gradually host Basilisk modules.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Mapping

BSK_ENGINE_SCHEMA_VERSION = "bsk-engine.v1"


@dataclass(frozen=True)
class BSKProcessSpec:
    name: str
    role: str
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BSKTaskSpec:
    name: str
    process: str
    rate_s: float
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BSKModuleSpec:
    tag: str
    kind: str
    task: str
    role: str
    source: str = "project_owned_bsksim_style"
    status: str = "declared"
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BSKConnectionSpec:
    source: str
    target: str
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BSKEventSpec:
    event_id: str
    category: str
    effect: str
    target: str
    start_s: float = 0.0
    end_s: float | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def active_at(self, time_s: float) -> bool:
        if time_s < self.start_s:
            return False
        if self.end_s is None:
            return True
        return time_s <= self.end_s

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["parameters"] = dict(self.parameters)
        return out


@dataclass(frozen=True)
class BSKRecorderSpec:
    field: str
    source: str
    sample_s: float
    unit: str = ""
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BSKExecutionPlan:
    schema_version: str
    engine: str
    scenario_id: str
    processes: tuple[BSKProcessSpec, ...]
    tasks: tuple[BSKTaskSpec, ...]
    modules: tuple[BSKModuleSpec, ...]
    connections: tuple[BSKConnectionSpec, ...]
    events: tuple[BSKEventSpec, ...]
    recorders: tuple[BSKRecorderSpec, ...]
    mode_request: str = "standby"
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "engine": self.engine,
            "scenario_id": self.scenario_id,
            "mode_request": self.mode_request,
            "processes": [x.to_dict() for x in self.processes],
            "tasks": [x.to_dict() for x in self.tasks],
            "modules": [x.to_dict() for x in self.modules],
            "connections": [x.to_dict() for x in self.connections],
            "events": [x.to_dict() for x in self.events],
            "recorders": [x.to_dict() for x in self.recorders],
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class BSKScenarioConfig:
    scenario_id: str
    capability_id: str
    duration_s: float = 60.0
    step_s: float = 1.0
    sample_s: float = 10.0
    mode_request: str = "standby"
    parameters: Mapping[str, Any] = field(default_factory=dict)
    events: tuple[BSKEventSpec, ...] = ()
    requested_outputs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "capability_id": self.capability_id,
            "duration_s": self.duration_s,
            "step_s": self.step_s,
            "sample_s": self.sample_s,
            "mode_request": self.mode_request,
            "parameters": dict(self.parameters),
            "events": [x.to_dict() for x in self.events],
            "requested_outputs": list(self.requested_outputs),
        }


@dataclass(frozen=True)
class BSKRunResult:
    status: str
    summary: dict[str, Any]
    trace_rows: tuple[dict[str, Any], ...]
    execution_plan: BSKExecutionPlan
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "summary": dict(self.summary),
            "trace_rows": list(self.trace_rows),
            "execution_plan": self.execution_plan.to_dict(),
            "metadata": dict(self.metadata),
        }
