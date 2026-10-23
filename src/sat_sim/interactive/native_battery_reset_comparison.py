"""R9-I: integrity-checked real Basilisk public battery Reset counterexample.

All values are measurements from independently executed native Basilisk
whole-spacecraft graphs, never synthesized trajectory results.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from .native_whole_first_tick_diagnostics import verify_divergence_report

SCHEMA = "sat-native-public-battery-reset-lab-comparison.v1"
BASE = "R9H_RW_MEMORY_PLUS_APPLIED_COMMAND"
RESET = "R9I_RW_APPLIED_AND_BATTERY_PUBLIC_RESET"


def _digest(data: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode()).hexdigest()


def validate_battery_reset_barrier_report(document: dict[str, Any]) -> None:
    if not isinstance(document, dict):
        raise ValueError("R9I_REPORT_INVALID")
    p = dict(document)
    sig = p.pop("sha256", None)
    if sig != _digest(p):
        raise ValueError("R9I_DIGEST_INVALID")
    if set(p) != {
        "schema", "profile_base", "profile_reset", "source_pid",
        "baseline_pid", "reset_pid", "checkpoint_ns", "samples",
        "test_only", "public_reset_not_state_import",
        "cpp_protected_state_readback_verified",
        "native_previous_time_restored", "complete_message_history_restored",
        "whole_spacecraft_parity_proven", "automatic_resume_allowed",
        "automatic_tc_replay_allowed", "conclusion",
    } or p["schema"] != SCHEMA:
        raise ValueError("R9I_SCHEMA_INVALID")
    if (p["profile_base"] != BASE or p["profile_reset"] != RESET
        or p["test_only"] is not True
        or p["public_reset_not_state_import"] is not True
        or p["conclusion"] != "PUBLIC_BATTERY_RESET_NOT_RESTART_PROOF"
        or any(p[k] is not False for k in (
            "cpp_protected_state_readback_verified", "native_previous_time_restored",
            "complete_message_history_restored", "whole_spacecraft_parity_proven",
            "automatic_resume_allowed", "automatic_tc_replay_allowed",
        ))):
        raise ValueError("R9I_FALSE_RESTORE_CLAIM")
    ids=[p["source_pid"],p["baseline_pid"],p["reset_pid"]]
    if any(type(v) is not int or v<=0 for v in ids) or len(set(ids)) != 3:
        raise ValueError("R9I_PROCESS_ID_INVALID")
    if p["checkpoint_ns"] != 800_000_000 or type(p["checkpoint_ns"]) is not int:
        raise ValueError("R9I_CHECKPOINT_TIME_INVALID")
    samples=p["samples"]
    if not isinstance(samples,list) or len(samples)!=4:
        raise ValueError("R9I_SAMPLES_INVALID")
    for i,s in enumerate(samples):
        if not isinstance(s,dict) or set(s) != {
            "time_ns", "energy_baseline_abs_error_j", "energy_reset_abs_error_j",
            "energy_error_change_j", "reset_improved", "reset_changed",
        } or type(s["time_ns"]) is not int or s["time_ns"]!=1_000_000_000+i*200_000_000:
            raise ValueError("R9I_SAMPLE_TIME_INVALID")
        a,b,d=s["energy_baseline_abs_error_j"],s["energy_reset_abs_error_j"],s["energy_error_change_j"]
        if (any(type(v) not in (float,int) or not math.isfinite(v) for v in (a,b,d))
            or a<0 or b<0 or abs((b-a)-d)>1e-12*max(1.,a,b)
            or s["reset_improved"] is not (b<a)
            or s["reset_changed"] is not (a!=b)):
            raise ValueError("R9I_SAMPLE_ERROR_FORGED")


def build_battery_reset_barrier_report(
    *, baseline: list[dict[str, Any]], reset_seed: list[dict[str, Any]],
    source_pid: int, baseline_pid: int, reset_pid: int,
) -> dict[str, Any]:
    if len(baseline) !=4 or len(reset_seed)!=4:
        raise ValueError("R9I_UNPAIRED_SAMPLES")
    samples=[]
    for i,(a,b) in enumerate(zip(baseline,reset_seed)):
        verify_divergence_report(a);verify_divergence_report(b)
        if (a["source_pid"]!=source_pid or b["source_pid"]!=source_pid
            or a["child_pid"]!=baseline_pid or b["child_pid"]!=reset_pid
            or a["source_checkpoint_ns"]!=b["source_checkpoint_ns"]
            or a["source_checkpoint_ns"]!=800_000_000
            or a["comparison_time_ns"] != b["comparison_time_ns"]):
            raise ValueError("R9I_UNPAIRED_NATIVE_OWNER")
        if a["comparison_time_ns"] != 1_000_000_000+i*200_000_000:
            raise ValueError("R9I_INVALID_SAMPLE_GRID")
        def err(d):
            return next(x["abs_max_error"] for x in d["signals"] if x["owner_signal"]=="eps.battery_energy_j")
        old,new=err(a),err(b)
        samples.append({
            "time_ns":a["comparison_time_ns"],
            "energy_baseline_abs_error_j":old,
            "energy_reset_abs_error_j":new,
            "energy_error_change_j":new-old,
            "reset_improved":new<old,"reset_changed":new!=old,
        })
    record={
        "schema":SCHEMA,"profile_base":BASE,"profile_reset":RESET,
        "source_pid":source_pid,"baseline_pid":baseline_pid,
        "reset_pid":reset_pid,"checkpoint_ns":800_000_000,
        "samples":samples,"test_only":True,
        "public_reset_not_state_import":True,
        "cpp_protected_state_readback_verified":False,
        "native_previous_time_restored":False,
        "complete_message_history_restored":False,
        "whole_spacecraft_parity_proven":False,
        "automatic_resume_allowed":False,
        "automatic_tc_replay_allowed":False,
        "conclusion":"PUBLIC_BATTERY_RESET_NOT_RESTART_PROOF",
    }
    record["sha256"]=_digest(record)
    validate_battery_reset_barrier_report(record)
    return record
