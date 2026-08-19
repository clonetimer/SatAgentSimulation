"""Common helpers for component runner scenario outputs."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any


def to_jsonable(value: Any) -> Any:
    """Convert dataclasses, enums, tuples and nested containers to JSON-friendly values."""
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v) for v in value]
    return value


def scenario_result(
    component: str,
    scenario: str,
    description: str,
    result: Any,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Wrap a component working-scenario result with consistent metadata."""
    payload = to_jsonable(result)
    if isinstance(payload, dict):
        data = dict(payload)
    else:
        data = {"result": payload}
    if extra:
        data.update(to_jsonable(extra))
    data.update(
        {
            "mode": "normal",
            "component": component,
            "scenario": scenario,
            "description": description,
        }
    )
    return data


def scenario_error(component: str, scenario: str, description: str, exc: BaseException) -> dict[str, Any]:
    return {
        "mode": "normal",
        "component": component,
        "scenario": scenario,
        "description": description,
        "error": str(exc),
        "exception_type": type(exc).__name__,
    }
