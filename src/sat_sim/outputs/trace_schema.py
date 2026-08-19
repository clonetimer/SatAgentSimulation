"""Trace schema helpers."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .standard_fields import FIELD_DTYPES, FIELD_UNITS


def infer_dtype(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"


def trace_schema_from_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    keys: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in keys:
                keys.append(str(key))
    schema: list[dict[str, str]] = []
    for key in keys:
        sample = next((row.get(key) for row in rows if row.get(key) is not None), None)
        schema.append({
            "name": key,
            "unit": FIELD_UNITS.get(key, "unknown"),
            "dtype": FIELD_DTYPES.get(key, infer_dtype(sample)),
        })
    return schema


__all__ = ["infer_dtype", "trace_schema_from_rows"]
