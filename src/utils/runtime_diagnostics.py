"""Runtime diagnostics for explicitly handled non-fatal exceptions.

V31 replaces silent ``except: pass`` paths with an auditable diagnostic event.
Critical categories are re-raised when strict diagnostics are enabled through
``SAT_SIM_STRICT_DIAGNOSTICS=1`` or :func:`strict_runtime_diagnostics`.
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Iterator, Mapping


class DiagnosticCategory(StrEnum):
    OPTIONAL_DEPENDENCY_PROBE = "optional_dependency_probe"
    VERSION_COMPATIBILITY_PROBE = "version_compatibility_probe"
    BEST_EFFORT_CLEANUP = "best_effort_cleanup"
    INPUT_COMPATIBILITY_FALLBACK = "input_compatibility_fallback"
    INTROSPECTION_FALLBACK = "introspection_fallback"
    AGENT_NORMALIZATION_FALLBACK = "agent_normalization_fallback"
    EVALUATION_FORMAT_FALLBACK = "evaluation_format_fallback"
    MESSAGE_READ_FAILURE = "message_read_failure"
    NATIVE_MAPPING_FAILURE = "native_mapping_failure"
    RUNTIME_INJECTION_FAILURE = "runtime_injection_failure"
    REGISTRY_SCHEMA_FAILURE = "registry_schema_failure"
    ARTIFACT_WRITE_FAILURE = "artifact_write_failure"


CRITICAL_CATEGORIES = frozenset({
    DiagnosticCategory.MESSAGE_READ_FAILURE,
    DiagnosticCategory.NATIVE_MAPPING_FAILURE,
    DiagnosticCategory.RUNTIME_INJECTION_FAILURE,
    DiagnosticCategory.REGISTRY_SCHEMA_FAILURE,
    DiagnosticCategory.ARTIFACT_WRITE_FAILURE,
})


@dataclass(frozen=True)
class RuntimeDiagnostic:
    code: str
    category: str
    location: str
    exception_type: str
    message: str
    severity: str
    count: int
    first_seen: str
    last_seen: str
    details: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RuntimeDiagnosticError(RuntimeError):
    def __init__(self, diagnostic: RuntimeDiagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(
            f"{diagnostic.code} at {diagnostic.location}: "
            f"{diagnostic.exception_type}: {diagnostic.message}"
        )


_LOCK = threading.RLock()
_EVENTS: dict[tuple[str, str, str, str, str], RuntimeDiagnostic] = {}
_STRICT_LOCAL = threading.local()
_MAX_EVENTS = 1024


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on", "strict"}


def strict_diagnostics_enabled() -> bool:
    local_value = getattr(_STRICT_LOCAL, "enabled", None)
    return bool(local_value) if local_value is not None else _env_truthy("SAT_SIM_STRICT_DIAGNOSTICS")


@contextmanager
def strict_runtime_diagnostics(enabled: bool = True) -> Iterator[None]:
    previous = getattr(_STRICT_LOCAL, "enabled", None)
    _STRICT_LOCAL.enabled = bool(enabled)
    try:
        yield
    finally:
        if previous is None:
            if hasattr(_STRICT_LOCAL, "enabled"):
                delattr(_STRICT_LOCAL, "enabled")
        else:
            _STRICT_LOCAL.enabled = previous


def record_runtime_diagnostic(
    *,
    code: str,
    category: DiagnosticCategory | str,
    location: str,
    exception: BaseException,
    details: Mapping[str, Any] | None = None,
    strict: bool | None = None,
) -> RuntimeDiagnostic:
    """Record a handled exception and optionally re-raise it in strict mode."""

    category_value = category if isinstance(category, DiagnosticCategory) else DiagnosticCategory(str(category))
    is_critical = category_value in CRITICAL_CATEGORIES
    severity = "error" if is_critical else "warning"
    message = str(exception)
    exc_type = type(exception).__name__
    key = (str(code), category_value.value, str(location), exc_type, message)
    timestamp = _now()
    payload_details = dict(details or {})
    with _LOCK:
        previous = _EVENTS.get(key)
        event = RuntimeDiagnostic(
            code=str(code), category=category_value.value, location=str(location),
            exception_type=exc_type, message=message, severity=severity,
            count=(previous.count + 1) if previous else 1,
            first_seen=previous.first_seen if previous else timestamp,
            last_seen=timestamp,
            details=payload_details or (previous.details if previous else {}),
        )
        _EVENTS[key] = event
        if len(_EVENTS) > _MAX_EVENTS:
            oldest_key = min(_EVENTS, key=lambda item: _EVENTS[item].last_seen)
            _EVENTS.pop(oldest_key, None)
    # ``strict=False`` may document a noncritical compatibility fallback, but it
    # must never disable the global strict policy for a critical category.
    should_raise = bool(strict) or (is_critical and strict_diagnostics_enabled())
    if should_raise:
        raise RuntimeDiagnosticError(event) from exception
    return event


def runtime_diagnostics(*, clear: bool = False) -> tuple[RuntimeDiagnostic, ...]:
    with _LOCK:
        values = tuple(sorted(_EVENTS.values(), key=lambda item: (item.category, item.code, item.location, item.exception_type, item.message)))
        if clear:
            _EVENTS.clear()
    return values


def runtime_diagnostics_report(*, clear: bool = False) -> dict[str, Any]:
    events = runtime_diagnostics(clear=clear)
    by_category: dict[str, int] = {}
    critical_values = {item.value for item in CRITICAL_CATEGORIES}
    for event in events:
        by_category[event.category] = by_category.get(event.category, 0) + event.count
    return {
        "strict_enabled": strict_diagnostics_enabled(),
        "event_count": sum(item.count for item in events),
        "unique_event_count": len(events),
        "critical_event_count": sum(item.count for item in events if item.category in critical_values),
        "by_category": dict(sorted(by_category.items())),
        "events": [item.to_dict() for item in events],
    }


def clear_runtime_diagnostics() -> None:
    with _LOCK:
        _EVENTS.clear()


__all__ = [
    "CRITICAL_CATEGORIES", "DiagnosticCategory", "RuntimeDiagnostic",
    "RuntimeDiagnosticError", "clear_runtime_diagnostics",
    "record_runtime_diagnostic", "runtime_diagnostics",
    "runtime_diagnostics_report", "strict_diagnostics_enabled",
    "strict_runtime_diagnostics",
]
