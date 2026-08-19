"""Observation extraction for BSK-RL-style fault episodes."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class FaultObservation:
    time_s: float
    phase: str
    active: bool
    selected_values: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _time(row: Mapping[str, Any]) -> float:
    try:
        return float(row.get("time_s", 0.0))
    except Exception:
        return 0.0


def _select_values(row: Mapping[str, Any], prefixes: Sequence[str]) -> dict[str, Any]:
    selected: dict[str, Any] = {}
    for key, value in row.items():
        if key in ("time_s", "task_id", "case_id"):
            continue
        if any(str(key).startswith(prefix) for prefix in prefixes):
            selected[str(key)] = value
    return selected


def observations_for_window(
    trace_rows: Sequence[Mapping[str, Any]],
    start_s: float,
    end_s: float | None,
    observable_prefixes: Sequence[str],
) -> tuple[FaultObservation, ...]:
    if not trace_rows:
        return ()
    end = end_s if end_s is not None else max(_time(r) for r in trace_rows)
    before = [r for r in trace_rows if _time(r) < start_s]
    during = [r for r in trace_rows if start_s <= _time(r) <= end]
    after = [r for r in trace_rows if _time(r) > end]
    obs: list[FaultObservation] = []
    if before:
        row = before[-1]
        obs.append(FaultObservation(_time(row), "before", False, _select_values(row, observable_prefixes)))
    for row in during:
        obs.append(FaultObservation(_time(row), "during", True, _select_values(row, observable_prefixes)))
    if after:
        row = after[0]
        obs.append(FaultObservation(_time(row), "after", False, _select_values(row, observable_prefixes)))
    return tuple(obs)


__all__ = ["FaultObservation", "observations_for_window"]
