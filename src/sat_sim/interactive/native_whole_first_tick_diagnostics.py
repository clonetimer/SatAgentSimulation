"""R9-F lab-only, non-authorizing whole-spacecraft post-import divergence report.

Two measurements must originate from separately executed *real Basilisk*
unified native graphs: uninterrupted source and imported fresh child. Numerical
agreement of any subset is never restart-readiness evidence by itself.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

SCHEMA = "sat-native-whole-first-tick-divergence.v1"
FIELDS = (
    ("adcs", "hub_position_m", "m", 1e-5),
    ("adcs", "hub_velocity_m_s", "m/s", 1e-7),
    ("adcs", "hub_sigma", "MRP", 1e-9),
    ("adcs", "hub_omega_rad_s", "rad/s", 1e-9),
    ("adcs", "reaction_wheel_speed_rad_s", "rad/s", 1e-7),
    ("eps", "battery_energy_j", "J", 1e-4),
    ("thermal", "payload_temp_k", "K", 1e-6),
    ("comm", "storage_bits", "bits", 1e-3),
)
# Names in the existing authoritative native StateData subset.
_NAMES = {
    "hub_position_m": "hub_position_m",
    "hub_velocity_m_s": "hub_velocity_m_s",
    "hub_sigma": "hub_mrp",
    "hub_omega_rad_s": "hub_omega_rad_s",
    "reaction_wheel_speed_rad_s": "reaction_wheel_speed_rad_s",
}
# Avoid silently substituting an invented field when native keys change.


def _digest(document: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _extract(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != {
        "time_ns", "vectors", "eps", "thermal", "comm",
    }:
        raise ValueError("R9F_INVALID_NATIVE_MEASUREMENT")
    if type(data["time_ns"]) is not int or data["time_ns"] <= 0:
        raise ValueError("R9F_INVALID_TIME")
    vectors = data["vectors"]
    if not isinstance(vectors, dict):
        raise ValueError("R9F_INVALID_NATIVE_VECTORS")
    out: dict[str, Any] = {}
    for owner, label, unit, threshold in FIELDS:
        if owner == "adcs":
            # Native names are explicitly matched to the actual StateData
            # registry, NOT to telemetry or a position-only proxy.
            raw = vectors.get(_NAMES[label])
        else:
            raw = data[owner].get(label) if isinstance(data[owner], dict) else None
        if type(raw) in (int, float):
            seq = [float(raw)]
        elif isinstance(raw, list) and raw and all(type(x) in (int, float) for x in raw):
            seq = [float(x) for x in raw]
        else:
            raise ValueError("R9F_MISSING_NATIVE_FIELD:" + owner + "." + label)
        if not all(math.isfinite(x) for x in seq):
            raise ValueError("R9F_NONFINITE_NATIVE_FIELD:" + owner + "." + label)
        if label == "reaction_wheel_speed_rad_s" and len(seq) not in (3, 4):
            raise ValueError("R9F_NATIVE_RW_COUNT_UNSUPPORTED")
        out[owner + "." + label] = (seq, threshold, unit)
    return out


def build_divergence_report(
    *, uninterrupted: dict[str, Any], imported_child: dict[str, Any],
    source_pid: int, child_pid: int, checkpoint_ns: int,
) -> dict[str, Any]:
    if (type(source_pid) is not int or type(child_pid) is not int
            or source_pid == child_pid or source_pid <= 0 or child_pid <= 0):
        raise ValueError("R9F_SEPARATE_NATIVE_PROCESS_REQUIRED")
    if type(checkpoint_ns) is not int or checkpoint_ns <= 0:
        raise ValueError("R9F_CHECKPOINT_TIME_INVALID")
    a, b = _extract(uninterrupted), _extract(imported_child)
    if (a.keys() != b.keys() or
            uninterrupted["time_ns"] != imported_child["time_ns"] or
            uninterrupted["time_ns"] <= checkpoint_ns):
        raise ValueError("R9F_DIFFERENT_OBSERVATION_PHASE")
    differences = []
    for name, (source, threshold, unit) in a.items():
        actual, other_threshold, other_unit = b[name]
        if len(source) != len(actual) or threshold != other_threshold or unit != other_unit:
            raise ValueError("R9F_NONISOMORPHIC_NATIVE_GRAPH:" + name)
        residual = max(abs(x-y) for x, y in zip(source, actual))
        differences.append({
            "owner_signal": name, "unit": unit,
            "abs_max_error": residual, "threshold": threshold,
            "above_threshold": residual > threshold,
        })
    failed = [d["owner_signal"] for d in differences if d["above_threshold"]]
    result = {
        "schema": SCHEMA,
        "profile": "R9F_ISOLATED_REAL_BASILISK_FIRST_TICK_ONLY",
        "source_pid": source_pid, "child_pid": child_pid,
        "source_checkpoint_ns": checkpoint_ns,
        "comparison_time_ns": uninterrupted["time_ns"],
        "test_only": True, "signals": differences,
        "above_threshold_signals": failed,
        "measured_subset_within_tolerance": not failed,
        "whole_spacecraft_restart_parity_proven": False,
        "native_integrator_and_hidden_memory_restored": False,
        "subsystem_history_restored": False,
        "automatic_resume_allowed": False,
        "automatic_tc_replay_allowed": False,
        "resume_supported": False,
        "conclusion": "OBSERVED_POST_IMPORT_DIVERGENCE" if failed
                      else "SAMPLED_SIGNALS_INCONCLUSIVE_NOT_RESUME_PROOF",
    }
    result["sha256"] = _digest(result)
    return result


def verify_divergence_report(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("R9F_REPORT_INVALID")
    content = dict(report)
    signature = content.pop("sha256", None)
    if signature != _digest(content):
        raise ValueError("R9F_REPORT_DIGEST_INVALID")
    if (content.get("schema") != SCHEMA or content.get("test_only") is not True
            or content.get("whole_spacecraft_restart_parity_proven") is not False
            or content.get("native_integrator_and_hidden_memory_restored") is not False
            or content.get("subsystem_history_restored") is not False
            or content.get("automatic_resume_allowed") is not False
            or content.get("automatic_tc_replay_allowed") is not False
            or content.get("resume_supported") is not False):
        raise ValueError("R9F_FALSE_RESUME_CLAIM")
    measured = content.get("signals")
    if (not isinstance(measured, list) or len(measured) != len(FIELDS) or
            [x.get("owner_signal") for x in measured if isinstance(x, dict)] != [
                owner + "." + name for owner, name, _, _ in FIELDS
            ]):
        raise ValueError("R9F_SIGNALS_INVALID")
    fail = []
    for item, (_, _, unit, threshold) in zip(measured, FIELDS):
        if not isinstance(item, dict) or set(item) != {
            "owner_signal", "unit", "abs_max_error", "threshold", "above_threshold",
        }:
            raise ValueError("R9F_SIGNAL_INVALID")
        d = item["abs_max_error"]
        if (type(d) not in (int, float) or not math.isfinite(d)
                or d < 0 or item["unit"] != unit
                or item["threshold"] != threshold
                or item["above_threshold"] is not (d > threshold)):
            raise ValueError("R9F_SIGNAL_INCONSISTENT")
        if d > threshold:
            fail.append(item["owner_signal"])
    if (content.get("above_threshold_signals") != fail or
        content.get("measured_subset_within_tolerance") is not (not fail) or
        content.get("conclusion") != (
            "OBSERVED_POST_IMPORT_DIVERGENCE" if fail
            else "SAMPLED_SIGNALS_INCONCLUSIVE_NOT_RESUME_PROOF"
        )):
        raise ValueError("R9F_REPORT_CONCLUSION_FORGED")
