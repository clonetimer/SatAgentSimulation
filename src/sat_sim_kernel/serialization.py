"""Deterministic serialization and content hashing for kernel contracts."""
from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from .errors import KernelValidationError


def to_canonical_dict(value: Any) -> Any:
    """Convert supported immutable contracts to JSON-compatible canonical data."""

    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: to_canonical_dict(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Enum):
        return to_canonical_dict(value.value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise KernelValidationError("non-finite floats are not canonical JSON values")
        return value
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise KernelValidationError("canonical mappings require string keys")
            out[key] = to_canonical_dict(item)
        return {key: out[key] for key in sorted(out)}
    if isinstance(value, tuple):
        return [to_canonical_dict(item) for item in value]
    if isinstance(value, list):
        return [to_canonical_dict(item) for item in value]
    if isinstance(value, (set, frozenset)):
        raise KernelValidationError("sets are not deterministic kernel values; use a sorted tuple")
    raise KernelValidationError(f"unsupported canonical value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        to_canonical_dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def content_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


__all__ = ["canonical_json", "content_sha256", "to_canonical_dict"]
