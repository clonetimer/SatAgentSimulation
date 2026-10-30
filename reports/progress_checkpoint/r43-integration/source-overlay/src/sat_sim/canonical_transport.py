"""Lossless TaskSpec JSON transport without changing the v1 hash contract.

JSON text is authoritative when supplied.  A companion browser object is only
a binding check: JSON numbers may compare across int/float, but booleans and
strings never compare as numbers.  Parsing precedes schema coercion.  The
returned value is canonicalized from the text, never from its lossy companion.
"""
from __future__ import annotations

import json
import math
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from .task_models import CANONICAL_TASK_SPEC_VERSION, LEGACY_TASK_SPEC_VERSION, CanonicalTaskSpec, canonicalize_task_spec

MAX_JSON_BYTES = 1024 * 1024
MAX_PAYLOAD_BYTES = 2 * MAX_JSON_BYTES
MAX_SAFE_INTEGER = 2**53 - 1
_MISSING = object()


class CanonicalTransportError(ValueError):
    """A bounded transport error with a stable machine-readable reason code."""

    def __init__(self, reason_code: str, message: str, path: str = "$") -> None:
        self.reason_code = "CANONICAL_TRANSPORT_" + reason_code
        self.path = path
        super().__init__(f"{self.reason_code}: {message} ({path})")


def _error(code: str, message: str, path: str = "$") -> CanonicalTransportError:
    return CanonicalTransportError(code, message, path)


def _walk(value: Any, *, safe_integers: bool, path: str = "$", active: set[int] | None = None) -> None:
    """Validate JSON values, including opaque metadata, without coercion."""
    if type(value) is str:
        try:
            value.encode("utf-8")
        except UnicodeError as exc:
            raise _error("OBJECT_INVALID", "JSON strings must be valid UTF-8", path) from exc
        return
    if value is None or type(value) is bool:
        return
    if type(value) in (int, float):
        if type(value) is float and not math.isfinite(value):
            raise _error("NON_FINITE", "JSON numbers must be finite", path)
        # Browser Number cannot identify all integers outside this interval.
        # Include integral floats: 1e20 is also an unsafe browser integer value.
        if safe_integers and (type(value) is int or value.is_integer()) and abs(value) > MAX_SAFE_INTEGER:
            raise _error("UNSAFE_INTEGER", "Companion objects cannot bind integers outside the browser safe range; use text only", path)
        return
    if not isinstance(value, Mapping) and type(value) is not list:
        raise _error("OBJECT_INVALID", "Only JSON objects, arrays and scalar values are supported", path)
    active = set() if active is None else active
    identity = id(value)
    if identity in active:
        raise _error("OBJECT_INVALID", "Cyclic objects are not JSON", path)
    active.add(identity)
    try:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if type(key) is not str:
                    raise _error("OBJECT_INVALID", "JSON object keys must be strings", path)
                try:
                    key.encode("utf-8")
                except UnicodeError as exc:
                    raise _error("OBJECT_INVALID", "JSON object keys must be valid UTF-8", path) from exc
                _walk(child, safe_integers=safe_integers, path=f"{path}.{key}", active=active)
        else:
            for index, child in enumerate(value):
                _walk(child, safe_integers=safe_integers, path=f"{path}[{index}]", active=active)
    finally:
        active.remove(identity)


def _encode(value: Mapping[str, Any], *, pretty: bool = False) -> tuple[str, int]:
    try:
        _walk(value, safe_integers=False)
        if pretty:
            text = json.dumps(dict(value), ensure_ascii=False, allow_nan=False, indent=2)
        else:
            text = json.dumps(dict(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        return text, len(text.encode("utf-8"))
    except CanonicalTransportError:
        raise
    except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
        raise _error("OBJECT_INVALID", "TaskSpec must be a finite UTF-8 JSON object") from exc


def _canonical(value: Mapping[str, Any], *, object_compatibility: bool = False) -> dict[str, Any]:
    try:
        version = value.get("schema_version")
        if object_compatibility:
            # No sidecar means the pre-existing API contract, including old
            # version aliases/drafts handled by the established migration.
            result = canonicalize_task_spec(value)
        elif version == CANONICAL_TASK_SPEC_VERSION:
            # A malformed declared-v1 document must fail its own schema rather
            # than silently migrate as an unspecified legacy draft.
            result = CanonicalTaskSpec.model_validate(dict(value)).model_dump(mode="json", exclude_none=True)
        elif version in (None, LEGACY_TASK_SPEC_VERSION):
            result = canonicalize_task_spec(value)
        else:
            raise _error("SCHEMA_INVALID", "Unsupported TaskSpec schema version")
        # Schema models allow Any metadata/config: check those after migration too.
        _walk(result, safe_integers=False)
        return result
    except CanonicalTransportError:
        raise
    except (TypeError, ValueError, KeyError, RecursionError) as exc:
        raise _error("SCHEMA_INVALID", "TaskSpec failed canonical schema validation") from exc


@lru_cache(maxsize=1)
def _wire_v1_validator():
    from jsonschema import Draft202012Validator

    return Draft202012Validator(CanonicalTaskSpec.model_json_schema())


def _validate_wire_schema(value: Mapping[str, Any]) -> None:
    """Require actual v1 JSON types before Pydantic's coercion/defaults.

    Legacy text must identify an actual document (task_id, simulation and a
    public task_type/capability_id discriminator). After that it retains the
    existing migration contract and canonical model validation; its runtime
    aliases are not redefined. Only omitted-text object mode can retain the
    historical empty/draft-to-default-task migration behavior.
    """
    version = value.get("schema_version")
    if version != CANONICAL_TASK_SPEC_VERSION:
        identity = value.get("task_id")
        discriminator = any(type(value.get(key)) is str and value[key].strip() for key in ("task_type", "capability_id"))
        if (version not in (None, LEGACY_TASK_SPEC_VERSION)
                or type(identity) is not str or not identity.strip()
                or not isinstance(value.get("simulation"), Mapping)
                or not discriminator):
            raise _error("SCHEMA_INVALID", "Text must identify a v1 TaskSpec or a legacy TaskSpec with task_id, simulation and task_type/capability_id")
        return
    from jsonschema.exceptions import ValidationError

    try:
        _wire_v1_validator().validate(value)
    except ValidationError as exc:
        raise _error("SCHEMA_INVALID", "TaskSpec text must satisfy the public v1 JSON Schema before coercion") from exc


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _error("DUPLICATE_KEY", "Duplicate JSON object keys are ambiguous")
        result[key] = value
    return result


def _finite_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise _error("NON_FINITE", "JSON floating-point number overflows the supported finite range")
    return value


def _non_json_constant(token: str) -> Any:
    raise _error("NON_FINITE", "NaN and Infinity are not supported JSON numbers")


def _same(left: Any, right: Any, path: str = "$") -> None:
    """Compare JSON structure and values; Python's True == 1 is insufficient."""
    if type(left) in (int, float) and type(right) in (int, float):
        matches = left == right
    elif isinstance(left, Mapping) and isinstance(right, Mapping):
        if left.keys() != right.keys():
            raise _error("VALUE_MISMATCH", "Text and object have different keys", path)
        for key in left:
            _same(left[key], right[key], f"{path}.{key}")
        return
    elif type(left) is list and type(right) is list:
        if len(left) != len(right):
            raise _error("VALUE_MISMATCH", "Text and object have different array lengths", path)
        for index, (a, b) in enumerate(zip(left, right)):
            _same(a, b, f"{path}[{index}]")
        return
    else:
        matches = type(left) is type(right) and left == right
    if not matches:
        raise _error("VALUE_MISMATCH", "Text and object have different JSON values or types", path)


def parse_canonical_transport(
    task_spec: Mapping[str, Any] | None,
    task_spec_json: Any = _MISSING,
) -> dict[str, Any]:
    """Validate a TaskSpec object or bind an authoritative JSON text sidecar.

    Omitted text preserves the existing object-only Python contract. Explicit
    null, empty or non-string text is rejected. Text is limited to 1 MiB UTF-8;
    text plus compact companion object is limited to 2 MiB. Text-only input may
    retain large integers; paired browser objects reject *all* integer-valued
    numbers outside +/- (2**53-1), including integral floats. No supplied hash
    or validation/cache flag is consulted.
    """
    if task_spec is not None and not isinstance(task_spec, Mapping):
        raise _error("OBJECT_INVALID", "TaskSpec companion must be an object")
    if task_spec_json is _MISSING:
        if task_spec is None:
            raise _error("MISSING_TASK_SPEC", "A TaskSpec object or JSON text is required")
        _, object_bytes = _encode(task_spec)
        if object_bytes > MAX_PAYLOAD_BYTES:
            raise _error("PAYLOAD_TOO_LARGE", "TaskSpec object exceeds the 2 MiB transport limit")
        return _canonical(task_spec, object_compatibility=True)
    if type(task_spec_json) is not str:
        raise _error("RAW_TYPE", "Explicit TaskSpec JSON text must be a string")
    if not task_spec_json.strip():
        raise _error("RAW_EMPTY", "Explicit TaskSpec JSON text cannot be empty")
    try:
        raw_bytes = len(task_spec_json.encode("utf-8"))
    except UnicodeError as exc:
        raise _error("JSON_INVALID", "TaskSpec text must be valid UTF-8") from exc
    if raw_bytes > MAX_JSON_BYTES:
        raise _error("RAW_TOO_LARGE", "TaskSpec JSON text exceeds the 1 MiB limit")
    try:
        parsed = json.loads(task_spec_json, object_pairs_hook=_pairs, parse_float=_finite_float, parse_constant=_non_json_constant)
    except CanonicalTransportError:
        raise
    except (ValueError, TypeError, RecursionError) as exc:
        raise _error("JSON_INVALID", "TaskSpec text is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise _error("JSON_INVALID", "TaskSpec text must contain a JSON object")
    if task_spec is not None:
        _, object_bytes = _encode(task_spec)
        if raw_bytes + object_bytes > MAX_PAYLOAD_BYTES:
            raise _error("PAYLOAD_TOO_LARGE", "TaskSpec text and companion exceed the 2 MiB transport limit")
        try:
            _walk(parsed, safe_integers=True)
            _walk(task_spec, safe_integers=True)
            _same(parsed, task_spec)
        except RecursionError as exc:
            raise _error("OBJECT_INVALID", "TaskSpec structure exceeds the supported nesting") from exc
    _validate_wire_schema(parsed)
    return _canonical(parsed)


def task_spec_json(spec: Mapping[str, Any]) -> str:
    """Validate, then serialize the *current* object's structure and numbers.

    The canonicalized result is intentionally not serialized here: an original
    legacy document or a v1 document with omitted defaults must still bind its
    own companion object. Consumers canonicalize only after checking that pair.
    Python's JSON encoder preserves integral floats and negative floating zero.
    Standard two-space indentation makes this authoritative text directly
    readable in the expert editor without a lossy browser parse/stringify.
    """
    if not isinstance(spec, Mapping):
        raise _error("OBJECT_INVALID", "TaskSpec must be an object")
    text, size = _encode(spec, pretty=True)
    if size > MAX_JSON_BYTES:
        raise _error("RAW_TOO_LARGE", "TaskSpec JSON text exceeds the 1 MiB limit")
    _validate_wire_schema(spec)
    _canonical(spec)
    return text


__all__ = ["CanonicalTransportError", "parse_canonical_transport", "task_spec_json"]
