"""Experimental Basilisk mrpFeedback integral-memory checkpoint (test only).

The Basilisk C module has persistent priorTime, int_sigma and z even if
its input guidance, spacecraft state and scheduler are identical. This
pilot proves only algorithm-level memory transfer on one known graph.
Message buses, task histories and physical closed-loop state are not
serialized; it MUST NOT be exposed as a production restart interface.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

SCHEMA = "basilisk-mrp-feedback-memory-pilot.v1"
PROFILE = "fixed-guidance-600ms-mrp-feedback-ki-positive-v1"
_PERIOD_NS = 600_000_000
_STATIC_SCALARS = ("K", "P", "Ki", "integralLimit", "controlLawType")
_STATIC_VECTORS = {"ISCPntB_B": 9, "knownTorquePntB_B": 3}
_MEMORY_VECTORS = ("int_sigma", "z")


def _sha(data: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        data, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _vector(value: Any, size: int, name: str) -> list[float]:
    try:
        seq = list(value)
    except TypeError as exc:
        raise ValueError(f"FSW_CHECKPOINT_VECTOR_INVALID:{name}") from exc
    if len(seq) != size or any(
        isinstance(x, bool) or not isinstance(x, (float, int))
        or not math.isfinite(float(x)) for x in seq
    ):
        raise ValueError(f"FSW_CHECKPOINT_VECTOR_INVALID:{name}")
    return [float(x) for x in seq]


def _read(module: Any) -> dict[str, Any]:
    scalars = {}
    for key in _STATIC_SCALARS:
        value = getattr(module, key)
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            raise ValueError(f"FSW_CHECKPOINT_SCALAR_INVALID:{key}")
        scalars[key] = int(value) if key == "controlLawType" else float(value)
    return {
        "prior_time_ns": int(module.priorTime),
        "memory": {key: _vector(getattr(module, key), 3, key) for key in _MEMORY_VECTORS},
        "config": {
            **scalars,
            **{key: _vector(getattr(module, key), size, key)
               for key, size in _STATIC_VECTORS.items()},
        },
        "cached_rw_count": int(module.rwConfigParams.numRW),
    }


def _validate(fields: dict[str, Any], checkpoint_ns: int) -> None:
    if type(checkpoint_ns) is not int or checkpoint_ns <= 0 or checkpoint_ns % 200_000_000:
        raise ValueError("FSW_CHECKPOINT_TIME_INVALID")
    if not isinstance(fields, dict) or set(fields) != {
        "prior_time_ns", "memory", "config", "cached_rw_count",
    }:
        raise ValueError("FSW_CHECKPOINT_FIELDS_INVALID")
    expected_prior = (checkpoint_ns // _PERIOD_NS) * _PERIOD_NS
    if type(fields["prior_time_ns"]) is not int or fields["prior_time_ns"] != expected_prior or expected_prior <= 0:
        raise ValueError("FSW_CHECKPOINT_PRIOR_TIME_INVALID")
    if type(fields["cached_rw_count"]) is not int or fields["cached_rw_count"] != 0:
        raise ValueError("FSW_CHECKPOINT_RW_CONFIGURATION_UNSUPPORTED")
    mem = fields["memory"]
    if not isinstance(mem, dict) or set(mem) != set(_MEMORY_VECTORS):
        raise ValueError("FSW_CHECKPOINT_MEMORY_INVALID")
    for key in _MEMORY_VECTORS:
        _vector(mem[key], 3, key)
    config = fields["config"]
    if not isinstance(config, dict) or set(config) != set(_STATIC_SCALARS) | set(_STATIC_VECTORS):
        raise ValueError("FSW_CHECKPOINT_CONFIGURATION_INVALID")
    for key in _STATIC_SCALARS:
        x = config[key]
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
            raise ValueError(f"FSW_CHECKPOINT_SCALAR_INVALID:{key}")
    if config["Ki"] <= 0 or config["integralLimit"] <= 0 or config["controlLawType"] != 0:
        raise ValueError("FSW_CHECKPOINT_UNSUPPORTED_CONTROL_MODE")
    for key, size in _STATIC_VECTORS.items():
        _vector(config[key], size, key)


def capture_mrp_feedback_memory(module: Any, *, checkpoint_ns: int) -> dict[str, Any]:
    """Read original native C-backed module state after scheduled updates."""
    fields = _read(module)
    _validate(fields, checkpoint_ns)
    content = {
        "schema_version": SCHEMA, "profile": PROFILE,
        "checkpoint_ns": checkpoint_ns, "fields": fields,
        "test_only": True, "module_memory_subset_import_supported": True,
        "message_history_restored": False,
        "entire_fsw_state_restored": False,
        "full_spacecraft_resume_supported": False,
    }
    content["digest_sha256"] = _sha(content)
    return content


def validate_mrp_feedback_memory(document: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ValueError("FSW_CHECKPOINT_DOCUMENT_INVALID")
    content = dict(document)
    digest = content.pop("digest_sha256", None)
    if digest != _sha(content):
        raise ValueError("FSW_CHECKPOINT_DIGEST_INVALID")
    if content.get("schema_version") != SCHEMA or content.get("profile") != PROFILE:
        raise ValueError("FSW_CHECKPOINT_PROFILE_INVALID")
    if (content.get("test_only") is not True
            or content.get("module_memory_subset_import_supported") is not True
            or content.get("message_history_restored") is not False
            or content.get("entire_fsw_state_restored") is not False
            or content.get("full_spacecraft_resume_supported") is not False):
        raise ValueError("FSW_CHECKPOINT_UNSUPPORTED_RESUME_CLAIM")
    _validate(content.get("fields"), content.get("checkpoint_ns"))
    return content["fields"]


def restore_mrp_feedback_memory_fresh(module: Any, document: dict[str, Any]) -> None:
    """ONLY into a fresh initialized but unadvanced identical native graph."""
    fields = validate_mrp_feedback_memory(document)
    initial = _read(module)
    if (initial["prior_time_ns"] != 0
            or any(any(abs(x) > 1e-14 for x in initial["memory"][key])
                   for key in _MEMORY_VECTORS)
            or initial["config"] != fields["config"]
            or initial["cached_rw_count"] != fields["cached_rw_count"]):
        raise ValueError("FSW_CHECKPOINT_FRESH_GRAPH_OR_CONFIG_REQUIRED")
    module.priorTime = fields["prior_time_ns"]
    module.int_sigma = list(fields["memory"]["int_sigma"])
    module.z = list(fields["memory"]["z"])
    if _read(module) != fields:
        raise ValueError("FSW_CHECKPOINT_MEMORY_WRITEBACK_FAILED")
