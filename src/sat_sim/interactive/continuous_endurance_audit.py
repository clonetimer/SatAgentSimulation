"""R7g independent, READ-ONLY, fail-closed audit of endurance evidence.

This code may run in an unrelated Python process while the Basilisk session
continues, or after the original server has died. It never opens the SQLite
command writer, imports a simulator, manipulates PIDs, or restarts the satellite.

SHA-256 proves only accidental/unsophisticated alteration of an append-only
record, not authenticity: an adversary able to rewrite all files can forge it.
A short/partial recording never certifies 24h continuous operation.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .continuous_endurance import (
    EVIDENCE_FILE, SUMMARY_FILE, verify_endurance_evidence,
    verify_endurance_summary,
)

SCHEMA = "sat-external-endurance-audit.v1"
TERMINAL = {"COMPLETED", "FAILED", "ABORTED", "INTERRUPTED"}


def _iso_utc(raw: str) -> datetime:
    result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("ENDURANCE_UNZONED_OBSERVATION")
    return result.astimezone(timezone.utc)


def audit_endurance_workspace(
    workspace: Path, *, max_stale_s: float = 30.,
    rss_growth_warning_mib_h: float = 512.,
) -> dict[str, Any]:
    """Return a machine-readable verdict; no side effects or recovery actions.

    BLOCKED = invalid evidence/inconsistent source; INCOMPLETE = unfinalized
    run (including a live recording); NOT_QUALIFIED = complete but insufficient;
    REVIEW_ELIGIBLE = completed 24h evidence, requires separate human approval.
    """
    if (not math.isfinite(max_stale_s) or max_stale_s <= 0
            or not math.isfinite(rss_growth_warning_mib_h)
            or rss_growth_warning_mib_h < 0):
        raise ValueError("ENDURANCE_AUDIT_BAD_THRESHOLDS")
    workspace = Path(workspace)
    evidence_path = workspace / EVIDENCE_FILE
    summary_path = workspace / SUMMARY_FILE
    findings: list[str] = []
    baseline = {
        "schema_version": SCHEMA, "workspace": str(workspace),
        "evidence_present": evidence_path.is_file(),
        "summary_present": summary_path.is_file(),
        "native_resume_supported": False,
        "automatic_tc_replay_allowed": False,
        "automated_recovery_allowed": False,
        "qualified_24h_wallclock": False,
        "manual_review_required": True,
    }
    if not evidence_path.is_file():
        return {**baseline, "verdict": "INCOMPLETE", "reason_codes": ["EVIDENCE_MISSING"],
                "samples": 0}
    try:
        chain = verify_endurance_evidence(evidence_path)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return {**baseline, "verdict": "BLOCKED", "reason_codes": [
            "EVIDENCE_INTEGRITY_INVALID", type(exc).__name__], "samples": None}

    count = chain["samples"]
    if count == 0:
        findings.append("EVIDENCE_EMPTY")
    first_wall: float | None = None
    last_wall: float | None = None
    last_sample_utc: datetime | None = None
    last_state: str | None = None
    prev_state: str | None = None
    previous_wall: float | None = None
    previous_sim: float | None = None
    prev_frames: int | None = None
    last_frame_seq: int | None = None
    prev_last_frame_seq: int | None = None
    last_frame_sim: float | None = None
    observed_running_s = 0.
    largest_sample_gap = 0.
    max_tm_lag_s = 0.
    process_unavailable_running_samples = 0
    running_no_frame_samples = 0
    disk_below_reserve_samples = 0
    rss_first: int | None = None
    rss_last: int | None = None
    rss_peak: int | None = None
    rss_scope: str | None = None
    rss_scope_changed = False
    disk_min_free: int | None = None
    alert_types: set[str] = set()
    warning_samples = 0
    sim_progress = 0.
    stream_id = None
    try:
        with evidence_path.open("rb") as stream:
            for raw in stream:
                row = json.loads(raw)
                obs = row["observation"]
                wall = obs["wall_elapsed_s"]
                sim = obs["sim_elapsed_s"]
                state = obs["session_state"]
                sample_utc = _iso_utc(obs["sampled_at"])
                if (obs["session_id"] != chain["session_id"]
                        or obs.get("resume_supported") is not False
                        or obs.get("automatic_tc_replay_allowed") is not False
                        or type(sim) not in (int, float) or not math.isfinite(sim)
                        or sim < 0 or state not in {
                            "CREATED", "READY", "RUNNING", "PAUSED",
                            "STOPPING", *TERMINAL,
                        }):
                    raise ValueError("ENDURANCE_SAMPLE_SOURCE_INCONSISTENT")
                tm = obs["telemetry"]
                if stream_id is None:
                    stream_id = tm["stream_id"]
                if tm["stream_id"] != stream_id:
                    raise ValueError("ENDURANCE_STREAM_ID_CHANGED")
                seq = tm["last_sequence"]
                frames = tm["durable_frame_count"]
                if (type(frames) is not int or frames < 0
                        or type(seq) not in (int, type(None))
                        or (seq is not None and seq < 0)):
                    raise ValueError("ENDURANCE_TELEMETRY_COUNTER_INVALID")
                if previous_wall is not None:
                    gap = wall - previous_wall
                    largest_sample_gap = max(largest_sample_gap, gap)
                    if gap <= 0:
                        raise ValueError("ENDURANCE_DUPLICATE_OR_NEGATIVE_INTERVAL")
                    if state == "RUNNING" and prev_state == "RUNNING":
                        observed_running_s += gap
                    if previous_sim is not None and sim < previous_sim:
                        raise ValueError("ENDURANCE_SIM_TIME_REVERSED")
                    if frames < prev_frames:
                        raise ValueError("ENDURANCE_FRAME_COUNT_REVERSED")
                    if seq is not None and prev_last_frame_seq is not None and seq < prev_last_frame_seq:
                        raise ValueError("ENDURANCE_FRAME_SEQUENCE_REVERSED")
                    if (state == "RUNNING" and prev_state == "RUNNING"
                            and seq == prev_last_frame_seq and gap >= max_stale_s):
                        running_no_frame_samples += 1
                if state == "RUNNING" and tm["status"] not in ("FRESH",):
                    # First sample of a new native runtime may precede its
                    # initial physical telemetry recorder output.
                    if not (row["sequence"] == 0 and tm["status"] == "NO_TELEMETRY"):
                        warning_samples += 1
                        alert_types.add("RUNNING_TELEMETRY_NOT_FRESH")
                tm_lag = tm.get("lag_wall_s")
                if tm_lag is not None:
                    if not math.isfinite(tm_lag) or tm_lag < 0:
                        raise ValueError("ENDURANCE_TM_AGE_INVALID")
                    max_tm_lag_s = max(max_tm_lag_s, tm_lag)
                proc = obs["process"]
                if not proc["available"] and state == "RUNNING":
                    process_unavailable_running_samples += 1
                if proc["available"]:
                    rss = proc["resident_bytes"]
                    if type(rss) is not int or rss < 0:
                        raise ValueError("ENDURANCE_RSS_INVALID")
                    scope = proc["scope"]
                    if rss_scope is None:
                        rss_scope = scope
                    elif scope != rss_scope:
                        rss_scope_changed = True
                    if rss_first is None:
                        rss_first = rss
                    rss_last = rss
                    rss_peak = max(rss_peak, rss) if rss_peak is not None else rss
                disk = obs.get("disk")
                if disk is None or not disk["available"]:
                    alert_types.add("DISK_METRICS_MISSING")
                else:
                    free = disk["free_bytes"]
                    reserve = disk["reserved_bytes"]
                    if type(free) is not int or type(reserve) is not int:
                        raise ValueError("ENDURANCE_DISK_METRICS_INVALID")
                    disk_min_free = min(disk_min_free, free) if disk_min_free is not None else free
                    if free <= reserve:
                        disk_below_reserve_samples += 1
                last_wall = wall
                if first_wall is None:
                    first_wall = wall
                prev_state = state
                previous_wall = wall
                previous_sim = sim
                prev_frames = frames
                prev_last_frame_seq = seq
                last_frame_seq = seq
                last_frame_sim = tm["last_sim_time_s"]
                last_sample_utc = sample_utc
                last_state = state
                if row["sequence"] == 0:
                    first_sim = sim
                sim_progress = sim - first_sim
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return {**baseline, "verdict": "BLOCKED", "reason_codes": [
            "EVIDENCE_SOURCE_INCONSISTENT", type(exc).__name__],
            "samples": count}

    if running_no_frame_samples:
        findings.append("RUNNING_TELEMETRY_STALLED")
    if warning_samples:
        findings.append("RUNNING_TELEMETRY_NOT_FRESH")
    if process_unavailable_running_samples:
        findings.append("RUNNING_PROCESS_UNAVAILABLE")
    if disk_below_reserve_samples:
        findings.append("DISK_BELOW_RESERVED_THRESHOLD")
    if "DISK_METRICS_MISSING" in alert_types:
        findings.append("DISK_METRICS_MISSING")
    if rss_scope_changed:
        findings.append("PROCESS_SCOPE_CHANGED")
    elapsed = (last_wall - first_wall) if first_wall is not None and last_wall is not None else 0.
    rss_rate = (
        (rss_last - rss_first) / (1024 * 1024) * 3600 / elapsed
        if rss_first is not None and rss_last is not None and elapsed > 0 else None
    )
    if rss_rate is not None and rss_rate > rss_growth_warning_mib_h:
        findings.append("RSS_GROWTH_RATE_WARNING")
    age_s = (
        max(0., (datetime.now(timezone.utc) - last_sample_utc).total_seconds())
        if last_sample_utc else None
    )
    if not summary_path.is_file():
        # A live producer may still be appending; no valid summary means
        # incomplete, even when one sees 86400s elapsed in the JSONL.
        verdict = "INCOMPLETE"
        findings.append("SUMMARY_MISSING_OR_STILL_RECORDING")
        if age_s is not None and age_s > max_stale_s:
            findings.append("EXTERNAL_SAMPLER_STALE")
        summary = None
    else:
        try:
            attestation = verify_endurance_summary(summary_path)
            summary = attestation["summary"]
            if (summary["recording_status"] != "FINISHED"
                    or summary.get("qualified_24h_wallclock") is not False
                    or summary.get("native_resume_supported") is not False
                    or summary.get("automatic_tc_replay_allowed") is not False
                    or not math.isclose(
                        summary.get("observed_running_wall_s", -1),
                        observed_running_s, rel_tol=0, abs_tol=0.01,
                    )):
                raise ValueError("ENDURANCE_SUMMARY_STATE_MISMATCH")
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            return {**baseline, "verdict": "BLOCKED", "reason_codes": [
                "SUMMARY_INTEGRITY_OR_METRICS_INVALID", type(exc).__name__],
                "samples": count}
        # Running must be demonstrably continuous; event-level interruptions
        # cannot be excluded purely by wall-span aggregation.
        eligible = (
            observed_running_s >= 86400.
            and last_state == "COMPLETED"
            and not findings and not summary["alerts"]
            and summary.get("eligible_for_manual_24h_review") is True
            and largest_sample_gap <= 3 * summary["interval_target_s"]
        )
        verdict = "REVIEW_ELIGIBLE" if eligible else "NOT_QUALIFIED"
        if not eligible:
            findings.append("ENDURANCE_THRESHOLDS_NOT_MET")

    return {
        **baseline, "verdict": verdict, "reason_codes": sorted(set(findings)),
        "samples": count, "session_id": chain["session_id"],
        "first_wall_elapsed_s": first_wall,
        "last_wall_elapsed_s": last_wall,
        "observed_wall_span_s": elapsed,
        "observed_running_wall_s": observed_running_s,
        "simulation_progress_s": sim_progress,
        "process_scope": rss_scope,
        "rss_first_bytes": rss_first, "rss_last_bytes": rss_last,
        "rss_peak_bytes": rss_peak,
        "rss_growth_mib_per_hour": rss_rate,
        "rss_growth_warning_mib_per_hour": rss_growth_warning_mib_h,
        "max_telemetry_lag_s": max_tm_lag_s,
        "largest_sample_gap_s": largest_sample_gap,
        "min_disk_free_bytes": disk_min_free,
        "last_sample_age_s": age_s,
        "last_telemetry_sequence": last_frame_seq,
        "last_telemetry_sim_time_s": last_frame_sim,
        "summary_recording_status": summary["recording_status"] if summary else None,
        "summary_evidence_sha256": chain["final_entry_hash"],
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Read-only, fail-closed external Basilisk endurance audit",
    )
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--max-stale-s", type=float, default=30.)
    parser.add_argument("--rss-warning-mib-h", type=float, default=512.)
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args(argv)
    report = audit_endurance_workspace(
        args.workspace, max_stale_s=args.max_stale_s,
        rss_growth_warning_mib_h=args.rss_warning_mib_h,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if report["verdict"] == "BLOCKED":
        return 2
    if report["verdict"] == "INCOMPLETE" and not args.allow_incomplete:
        return 3
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
