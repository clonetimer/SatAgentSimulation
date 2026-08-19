"""TaskSpec document utilities.

This module is deliberately lightweight: it does not import Basilisk, subsystem
builders, or whole-spacecraft runners.  It is safe to use in Agent tooling,
CI validation, documentation generation, and UI form backends.
"""
from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .task_models import (
    CANONICAL_TASK_SPEC_VERSION,
    LEGACY_TASK_SPEC_VERSION,
    canonicalize_task_spec,
    is_canonical_task_spec,
    migrate_legacy_task_spec,
    to_runtime_task_spec,
)

# Backward-compatible name used by legacy adapters/templates. New public Agent
# contracts should use CANONICAL_TASK_SPEC_VERSION.
TASK_SPEC_VERSION = LEGACY_TASK_SPEC_VERSION
DATASET_MANIFEST_VERSION = "0.1.0"


class TaskSpecError(ValueError):
    """Raised when a TaskSpec document cannot be loaded or normalized."""


@dataclass(frozen=True)
class TaskSpecDocument:
    """Loaded TaskSpec with optional source metadata."""

    data: dict[str, Any]
    path: Path | None = None
    raw_text: str | None = None

    @property
    def task_id(self) -> str:
        task = self.data.get("task")
        if isinstance(task, Mapping):
            return str(task.get("id", ""))
        return str(self.data.get("task_id", ""))

    @property
    def task_type(self) -> str:
        simulation = self.data.get("simulation")
        if is_canonical_task_spec(self.data) and isinstance(simulation, Mapping):
            return str(simulation.get("level", ""))
        return str(self.data.get("task_type", ""))

    @property
    def schema_version(self) -> str:
        return str(self.data.get("schema_version", ""))

    @property
    def spec_hash(self) -> str:
        return spec_sha256(self.data)


def _load_yaml(text: str) -> Any:
    try:
        import yaml  # type: ignore
    except Exception as exc:  # pragma: no cover - environment dependent
        raise TaskSpecError("YAML input requires PyYAML. Use JSON or install pyyaml.") from exc
    return yaml.safe_load(text)


def load_mapping(path: str | Path) -> dict[str, Any]:
    """Load a JSON/YAML file and require a mapping/object root."""

    path = Path(path)
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            data = json.loads(text)
        elif suffix in {".yaml", ".yml"}:
            data = _load_yaml(text)
        else:
            # Try JSON first to keep the dependency optional for .txt-like files.
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = _load_yaml(text)
    except Exception as exc:
        if isinstance(exc, TaskSpecError):
            raise
        raise TaskSpecError(f"failed to parse {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise TaskSpecError(f"{path} root must be a mapping/object")
    return data


def load_task_spec(path: str | Path) -> TaskSpecDocument:
    """Load a TaskSpec JSON/YAML document."""

    path = Path(path)
    text = path.read_text(encoding="utf-8")
    data = load_mapping(path)
    return TaskSpecDocument(data=dict(data), path=path, raw_text=text)


def canonical_json(data: Mapping[str, Any]) -> str:
    """Return deterministic JSON text for hashing/reproducibility."""

    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def spec_sha256(data: Mapping[str, Any]) -> str:
    """Return SHA-256 of the canonical TaskSpec payload."""

    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def get_path(data: Mapping[str, Any], dotted_path: str, default: Any = None) -> Any:
    """Fetch a dotted path from a nested mapping."""

    cur: Any = data
    for part in dotted_path.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return default
        cur = cur[part]
    return cur


def as_number(data: Mapping[str, Any], dotted_path: str, default: float | None = None) -> float | None:
    """Fetch a dotted path and coerce it to float, rejecting bools."""

    value = get_path(data, dotted_path, default)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TaskSpecError(f"{dotted_path} must be a number")
    return float(value)


def write_json(path: str | Path, payload: Mapping[str, Any]) -> Path:
    """Write a JSON object with stable formatting."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n", encoding="utf-8")
    return path


__all__ = [
    "TASK_SPEC_VERSION",
    "CANONICAL_TASK_SPEC_VERSION",
    "LEGACY_TASK_SPEC_VERSION",
    "DATASET_MANIFEST_VERSION",
    "TaskSpecDocument",
    "TaskSpecError",
    "load_mapping",
    "load_task_spec",
    "canonical_json",
    "spec_sha256",
    "get_path",
    "as_number",
    "write_json",
    "canonicalize_task_spec",
    "migrate_legacy_task_spec",
    "to_runtime_task_spec",
    "is_canonical_task_spec",
]
