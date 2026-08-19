"""Capability adapter base contracts.

Adapters are the production bridge from TaskSpec/CapabilityContract to concrete
model calls or deterministic script generation.  They should not call demo
``runner.py`` helpers.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from .task_validator import ValidationIssue


@dataclass(frozen=True)
class SimulationResult:
    """Normalized result returned by explicit capability adapters."""

    summary: dict[str, Any]
    trace_rows: tuple[dict[str, Any], ...]
    labels: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "trace_rows": list(self.trace_rows),
            "labels": self.labels,
            "metadata": self.metadata,
        }


class SimulationAdapter(Protocol):
    """Protocol implemented by explicit capability adapters."""

    capability_id: str

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        """Return adapter-specific validation issues."""

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        """Execute the capability against a TaskSpec mapping."""

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        """Generate deterministic Python code for this capability."""

    def output_schema(self, capability: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Return output schema metadata."""


__all__ = ["SimulationResult", "SimulationAdapter"]
