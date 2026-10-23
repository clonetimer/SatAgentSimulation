"""R9-H multi-step held RW-command ablation evidence, test-only.

Comparison uses only genuine Basilisk post-import executions. The original
post-fault-manager command is restored for one time sample, NOT the history
of model messages or any pending telecommand effects.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from .native_whole_first_tick_diagnostics import verify_divergence_report

SCHEMA = "sat-whole-native-held-motor-multistep-ablation.v1"
BASELINE = "R9G_WITH_RW_CPP_MEMORY"
HELD_COMMAND = "R9H_RW_MEMORY_PLUS_APPLIED_COMMAND"


def _digest(content: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        content, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")).hexdigest()


def validate_multistep_ablation(document: dict[str, Any]) -> None:
    if not isinstance(document, dict):
        raise ValueError("R9H_ABLATION_INVALID")
    content = dict(document)
    digest = content.pop("sha256", None)
    if digest != _digest(content):
        raise ValueError("R9H_ABLATION_DIGEST_INVALID")
    if set(content) != {
        "schema", "baseline_profile", "command_profile", "source_pid",
        "baseline_pid", "command_pid", "checkpoint_ns", "epochs",
        "test_only", "applied_command_single_sample", "complete_message_history",
        "whole_spacecraft_physical_parity_proven", "automatic_resume_allowed",
        "automatic_tc_replay_allowed", "conclusion",
    } or content["schema"] != SCHEMA:
        raise ValueError("R9H_ABLATION_SCHEMA_INVALID")
    if (content["baseline_profile"] != BASELINE
            or content["command_profile"] != HELD_COMMAND
            or content["test_only"] is not True
            or content["applied_command_single_sample"] is not True
            or content["complete_message_history"] is not False
            or content["whole_spacecraft_physical_parity_proven"] is not False
            or content["automatic_resume_allowed"] is not False
            or content["automatic_tc_replay_allowed"] is not False
            or content["conclusion"] != "LAB_MULTISTEP_RESIDUAL_ONLY_NOT_RESTART"):
        raise ValueError("R9H_ABLATION_FALSE_RESUME_CLAIM")
    ps = (content["source_pid"], content["baseline_pid"], content["command_pid"])
    if len(set(ps)) != 3 or any(type(v) is not int or v <= 0 for v in ps):
        raise ValueError("R9H_ABLATION_PID_INVALID")
    checkpoint = content["checkpoint_ns"]
    if type(checkpoint) is not int or checkpoint != 800_000_000:
        raise ValueError("R9H_ABLATION_CHECKPOINT_INVALID")
    epochs = content["epochs"]
    if not isinstance(epochs, list) or len(epochs) < 3 or len(epochs) > 5:
        raise ValueError("R9H_ABLATION_EPOCHS_INVALID")
    old = checkpoint
    for epoch in epochs:
        if not isinstance(epoch, dict) or set(epoch) != {"time_ns", "signals"}:
            raise ValueError("R9H_ABLATION_EPOCH_SCHEMA_INVALID")
        t = epoch["time_ns"]
        if type(t) is not int or t != old + 200_000_000:
            raise ValueError("R9H_ABLATION_GRID_INVALID")
        old = t
        signals = epoch["signals"]
        if not isinstance(signals, list) or len(signals) != 8:
            raise ValueError("R9H_ABLATION_SIGNALS_INVALID")
        for j, row in enumerate(signals):
            if not isinstance(row, dict) or set(row) != {
                "owner_signal", "baseline_abs_max_error",
                "command_abs_max_error", "absolute_delta",
                "improved", "changed",
            }:
                raise ValueError("R9H_ABLATION_SIGNAL_SCHEMA_INVALID")
            a, b, d = (row["baseline_abs_max_error"],
                       row["command_abs_max_error"], row["absolute_delta"])
            if any(type(x) not in (float, int) or not math.isfinite(x) for x in (a,b,d)) or a<0 or b<0:
                raise ValueError("R9H_ABLATION_NONFINITE_RESIDUAL")
            if row["owner_signal"] not in (
                "adcs.hub_position_m", "adcs.hub_velocity_m_s",
                "adcs.hub_sigma", "adcs.hub_omega_rad_s",
                "adcs.reaction_wheel_speed_rad_s", "eps.battery_energy_j",
                "thermal.payload_temp_k", "comm.storage_bits",
            ):
                raise ValueError("R9H_ABLATION_SIGNAL_UNKNOWN")
            if (abs((b-a)-d) > 1e-12*max(1.,a,b)
                or row["improved"] is not (b<a)
                or row["changed"] is not (a!=b)):
                raise ValueError("R9H_ABLATION_IMPROVEMENT_FORGED")
        if len(set(s["owner_signal"] for s in signals)) != 8:
            raise ValueError("R9H_ABLATION_SIGNAL_DUPLICATE")


def make_multistep_ablation(
    *, baseline_reports: list[dict[str, Any]],
    command_reports: list[dict[str, Any]],
    source_pid: int, baseline_pid: int, command_pid: int,
) -> dict[str, Any]:
    if (len(baseline_reports) != len(command_reports)
        or len(baseline_reports) < 3 or len(baseline_reports)>5):
        raise ValueError("R9H_ABLATION_PAIR_COUNT_INVALID")
    epochs = []
    for a,b in zip(baseline_reports,command_reports):
        verify_divergence_report(a)
        verify_divergence_report(b)
        if (a["source_pid"] != source_pid or b["source_pid"] != source_pid
            or a["child_pid"] != baseline_pid or b["child_pid"] != command_pid
            or a["source_checkpoint_ns"] != 800_000_000
            or b["source_checkpoint_ns"] != 800_000_000
            or a["comparison_time_ns"] != b["comparison_time_ns"]):
            raise ValueError("R9H_ABLATION_SOURCE_NOT_PAIRED")
        comparisons=[]
        for x,y in zip(a["signals"],b["signals"]):
            if (x["owner_signal"] != y["owner_signal"]
                or x["unit"] != y["unit"]
                or x["threshold"] != y["threshold"]):
                raise ValueError("R9H_ABLATION_SIGNAL_MISMATCH")
            old,new=x["abs_max_error"],y["abs_max_error"]
            comparisons.append({
                "owner_signal":x["owner_signal"],
                "baseline_abs_max_error":old,"command_abs_max_error":new,
                "absolute_delta":new-old,"improved":new<old,"changed":new!=old,
            })
        epochs.append({"time_ns":a["comparison_time_ns"],"signals":comparisons})
    data = {
        "schema":SCHEMA,"baseline_profile":BASELINE,
        "command_profile":HELD_COMMAND,"source_pid":source_pid,
        "baseline_pid":baseline_pid,"command_pid":command_pid,
        "checkpoint_ns":800_000_000,"epochs":epochs,
        "test_only":True,"applied_command_single_sample":True,
        "complete_message_history":False,
        "whole_spacecraft_physical_parity_proven":False,
        "automatic_resume_allowed":False,"automatic_tc_replay_allowed":False,
        "conclusion":"LAB_MULTISTEP_RESIDUAL_ONLY_NOT_RESTART",
    }
    data["sha256"]=_digest(data)
    validate_multistep_ablation(data)
    return data
