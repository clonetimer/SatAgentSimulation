"""R9-G comparison of native whole-spacecraft partial-import ablation arms.

Read-only analysis of already measured REAL Basilisk first-tick residuals.
It never authorizes restart. Its two arms differ solely in the test-only
ReactionWheelStateEffector exposed C++ memory restore procedure.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from .native_whole_first_tick_diagnostics import verify_divergence_report

SCHEMA = "sat-native-rw-owner-memory-ablation.v1"
BASIC = "R9F_NATIVE_SUBSET_ONLY"
RW_MEMORY = "R9G_WITH_RW_CPP_MEMORY"
_ADCS = (
    "adcs.hub_sigma",
    "adcs.hub_omega_rad_s",
    "adcs.reaction_wheel_speed_rad_s",
)


def _hash(data: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def validate_ablation_record(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("R9G_ABLATION_REPORT_INVALID")
    content = dict(report)
    checksum = content.pop("sha256", None)
    if checksum != _hash(content):
        raise ValueError("R9G_ABLATION_REPORT_HASH_INVALID")
    expected = {
        "schema", "mode_baseline", "mode_rw_memory", "source_pid",
        "baseline_child_pid", "rw_memory_child_pid", "checkpoint_ns",
        "comparison_ns", "signal_deltas", "rw_cpp_memory_writeback_only",
        "test_only", "whole_spacecraft_resume_supported",
        "whole_graph_parity_proven", "automatic_tc_replay_allowed",
        "owner_closure_proven", "conclusion",
    }
    if set(content) != expected or content["schema"] != SCHEMA:
        raise ValueError("R9G_ABLATION_REPORT_SCHEMA_INVALID")
    if (content["mode_baseline"] != BASIC or content["mode_rw_memory"] != RW_MEMORY
        or content["rw_cpp_memory_writeback_only"] is not True
        or content["test_only"] is not True
        or any(content[k] is not False for k in (
            "whole_spacecraft_resume_supported", "whole_graph_parity_proven",
            "automatic_tc_replay_allowed", "owner_closure_proven",
        )) or content["conclusion"] != "PARTIAL_RW_OWNER_ABLATION_NOT_RECOVERY"):
        raise ValueError("R9G_ABLATION_FALSE_RESTORE_CLAIM")
    pids = [content["source_pid"], content["baseline_child_pid"],
            content["rw_memory_child_pid"]]
    if any(type(p) is not int or p <= 0 for p in pids) or len(set(pids)) != 3:
        raise ValueError("R9G_ABLATION_PROCESS_INVALID")
    if (type(content["checkpoint_ns"]) is not int or
        type(content["comparison_ns"]) is not int or
        not 0 < content["checkpoint_ns"] < content["comparison_ns"]):
        raise ValueError("R9G_ABLATION_TIME_INVALID")
    signals = content["signal_deltas"]
    if not isinstance(signals, list) or len(signals) != 8:
        raise ValueError("R9G_ABLATION_SIGNAL_COUNT_INVALID")
    expected_signal_names = (
        "adcs.hub_position_m", "adcs.hub_velocity_m_s",
        "adcs.hub_sigma", "adcs.hub_omega_rad_s",
        "adcs.reaction_wheel_speed_rad_s", "eps.battery_energy_j",
        "thermal.payload_temp_k", "comm.storage_bits",
    )
    if [s.get("owner_signal") for s in signals if isinstance(s, dict)] != list(expected_signal_names):
        raise ValueError("R9G_ABLATION_SIGNAL_ORDER_INVALID")
    for item in signals:
        if not isinstance(item, dict) or set(item) != {
            "owner_signal", "unit", "baseline_abs_max_error",
            "rw_memory_abs_max_error", "absolute_error_change",
            "improved", "changed",
        }:
            raise ValueError("R9G_ABLATION_SIGNAL_SCHEMA_INVALID")
        a = item["baseline_abs_max_error"]
        b = item["rw_memory_abs_max_error"]
        d = item["absolute_error_change"]
        if any(type(x) not in (int, float) or not math.isfinite(x) or x < 0
               for x in (a, b)) or type(d) not in (int, float) or not math.isfinite(d):
            raise ValueError("R9G_ABLATION_RESIDUAL_INVALID")
        if (abs(d - (b-a)) > 1e-12*max(1.,a,b)
            or item["improved"] is not (b < a)
            or item["changed"] is not (a != b)):
            raise ValueError("R9G_ABLATION_DERIVED_RESULT_FORGED")


def create_ablation_record(
    *, baseline: dict[str, Any], with_rw_memory: dict[str, Any],
    source_pid: int, baseline_child_pid: int, rw_memory_child_pid: int,
) -> dict[str, Any]:
    verify_divergence_report(baseline)
    verify_divergence_report(with_rw_memory)
    if (baseline["source_pid"] != source_pid or with_rw_memory["source_pid"] != source_pid
        or baseline["child_pid"] != baseline_child_pid
        or with_rw_memory["child_pid"] != rw_memory_child_pid
        or baseline["source_checkpoint_ns"] != with_rw_memory["source_checkpoint_ns"]
        or baseline["comparison_time_ns"] != with_rw_memory["comparison_time_ns"]):
        raise ValueError("R9G_ABLATION_COMPARISON_NOT_PAIRED")
    signals = []
    for a, b in zip(baseline["signals"], with_rw_memory["signals"]):
        if (a["owner_signal"] != b["owner_signal"]
            or a["unit"] != b["unit"]
            or a["threshold"] != b["threshold"]):
            raise ValueError("R9G_ABLATION_NONISOMORPHIC_SIGNALS")
        x, y = a["abs_max_error"], b["abs_max_error"]
        signals.append({
            "owner_signal": a["owner_signal"], "unit": a["unit"],
            "baseline_abs_max_error": x,
            "rw_memory_abs_max_error": y,
            "absolute_error_change": y-x, "improved": y < x,
            "changed": x != y,
        })
    record = {
        "schema": SCHEMA,
        "mode_baseline": BASIC,
        "mode_rw_memory": RW_MEMORY,
        "source_pid": source_pid,
        "baseline_child_pid": baseline_child_pid,
        "rw_memory_child_pid": rw_memory_child_pid,
        "checkpoint_ns": baseline["source_checkpoint_ns"],
        "comparison_ns": baseline["comparison_time_ns"],
        "signal_deltas": signals,
        "rw_cpp_memory_writeback_only": True,
        "test_only": True,
        "whole_spacecraft_resume_supported": False,
        "whole_graph_parity_proven": False,
        "automatic_tc_replay_allowed": False,
        "owner_closure_proven": False,
        "conclusion": "PARTIAL_RW_OWNER_ABLATION_NOT_RECOVERY",
    }
    record["sha256"] = _hash(record)
    validate_ablation_record(record)
    return record
