"""Read-only original-run review; no simulations, tests or product imports.

Run from a checkout containing the original local evidence. Writes only small
exact-byte attachments and acceptance.json beside this script. Large traces
remain local. Original automatic qualification and raw records are not edited.
"""
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
LOCAL = ROOT / "reports/local_requirements_completion"
RUN = LOCAL / "r46-native-endurance-24h-v1"
SESSION = RUN / "api/interactive_sessions/native-endurance"
BUNDLE = SESSION / "run_bundle"

def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def load(path):
    return json.loads(path.read_bytes())

def canonical_hash(row):
    return hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()

def chain(path):
    previous = "0" * 64
    with path.open("rb") as stream:
        for index, raw in enumerate(stream):
            row = json.loads(raw)
            assert row["sequence"] == index and row["previous_hash"] == previous
            assert canonical_hash({k:v for k,v in row.items() if k!="entry_hash"}) == row["entry_hash"]
            previous = row["entry_hash"]
            yield row

def main():
    progress = load(RUN / "progress.json")
    summary = load(SESSION / "continuous_endurance_summary.json")
    prior = load(LOCAL / "endurance-terminal-snapshot-fix-v1/original24-independent-evidence-review.json")
    pace = load(RUN / "pace-history-audit/report.json")
    manifest_path = BUNDLE / "bundle_manifest.json"
    manifest = load(manifest_path)
    checks = {}
    def check(name, value):
        checks[name] = bool(value)
        assert value, name
    check("original_negative_unchanged", progress["status"] == "NOT_QUALIFIED"
          and summary["qualified_24h_wallclock"] is False
          and summary["eligible_for_manual_24h_review"] is False)
    check("summary_digest", canonical_hash({k:v for k,v in summary.items() if k!="summary_sha256"}) == summary["summary_sha256"])
    payloads = []
    for name, expected in manifest["files"].items():
        path = (BUNDLE / name).resolve()
        check("safe_payload:" + name, path.is_relative_to(BUNDLE.resolve()))
        actual = {"path":name, "bytes":path.stat().st_size, "sha256":digest(path)}
        check("payload:" + name, actual["bytes"] == expected["size_bytes"] and actual["sha256"] == expected["sha256"])
        payloads.append(actual)
    check("manifest_bound_to_prior_review", digest(manifest_path) ==
          "29fd6f3a30b48afac9e473cf83332a5ad856377cdb85a011f3c2dbd8f1f25f5b" and payloads == prior["sealed_payloads"])
    check("mission_sealed_succeeded", manifest["overall_status"] == "PASS"
          and manifest["execution_status"] == "SUCCEEDED" and manifest["validation_result"] == "PASS"
          and progress["session"]["archive_status"] == "SEALED")
    previous = None
    running = 0.
    count = 0
    max_gap = 0.
    tail = deque(maxlen=3)
    for row in chain(SESSION / "continuous_endurance_evidence.jsonl"):
        observation = row["observation"]
        if previous is not None:
            gap = observation["wall_elapsed_s"] - previous["wall_elapsed_s"]
            assert gap >= 0
            max_gap = max(max_gap, gap)
            if previous["session_state"] == observation["session_state"] == "RUNNING":
                running += gap
        previous = observation
        tail.append(row)
        count += 1
    check("periodic_chain_summary_binding", count == summary["samples"] and tail[-1]["entry_hash"] == summary["final_evidence_hash"])
    check("running_over24h_recomputed", running >= 86400 and abs(running-summary["observed_running_wall_s"]) < 1e-6)
    check("no_alert_pause_recorder_error", summary["alerts"] == {} and summary["observed_pause_after_run"] is False
          and summary["recording_status"] == "FINISHED" and summary["reason_code"] is None)
    check("sampling_gap_budget", max_gap <= 3*summary["interval_target_s"])
    state_count = 0
    for terminal in chain(BUNDLE / "interactive/state_events.jsonl"):
        state_count += 1
    check("source_terminal_completed", terminal["state"] == "COMPLETED" and terminal == prior["source_terminal_event"])
    last_at = datetime.fromisoformat(tail[-1]["observation"]["sampled_at"])
    terminal_at = datetime.fromisoformat(terminal["wall_time"])
    finalized_at = datetime.fromisoformat(summary["completed_at"])
    delay = (terminal_at-last_at).total_seconds()
    check("terminal_race_original_evidence", tail[-1]["observation"]["session_state"] == "STOPPING"
          and 0 < delay <= summary["interval_target_s"] and terminal_at <= finalized_at
          and terminal["sim_time_s"] == tail[-1]["observation"]["sim_elapsed_s"])
    observations = 0
    for row in chain(RUN / "pace_history.jsonl"):
        observations += len(row.get("observations", []))
    check("pace_complete_hash_binding", row["kind"] == "finish"
          and observations == row["terminal_total"] == pace["persisted_samples"] == 172861
          and row["entry_hash"] == pace["final_entry_hash"] and digest(RUN/"pace_history.jsonl") == pace["ledger_sha256"]
          and row["terminal_max_abs_drift_s"] == pace["captured_peak_abs_drift_s"])
    rolling = RUN / "pace-history-audit/rolling_30min_every_5s.jsonl"
    rolling_count = 0
    with rolling.open("rb") as stream:
        for raw in stream:
            assert json.loads(raw)["status"] == "PASS"
            rolling_count += 1
    check("declared_window_budget_pass", pace["status"] == "PACE_WINDOW_GATE_PASS" and pace["coverage"] == "COMPLETE"
          and pace["missing_samples"] == 0 and len(pace["disjoint_complete_windows"]) == 48
          and all(w["status"] == "PASS" for w in pace["disjoint_complete_windows"])
          and rolling_count == pace["rolling_window_count"] == 16927
          and pace["rolling_failed_windows"] == 0 and pace["empty_windows"] == 0
          and digest(rolling) == pace["rolling_windows_sha256"])
    check("frozen_run_inputs_unchanged", all(progress[k] is True for k in (
        "environment_assets_unchanged", "pace_helper_unchanged", "performance_budget_unchanged",
        "runtime_source_unchanged", "application_source_unchanged", "runner_source_unchanged")))
    full = LOCAL / "endurance-terminal-snapshot-full-v1"
    suite = ET.parse(full/"junit.xml").getroot().find("testsuite")
    failed = [f'{c.attrib["classname"]}::{c.attrib["name"]}' for c in suite.findall("testcase") if c.find("failure") is not None]
    check("latest_full_failure_not_hidden", suite.attrib["failures"] == "3" and len(failed) == 3
          and load(full/"pytest-returned.json")["exit_code"] == 1)
    files = {"original-summary.json": SESSION/"continuous_endurance_summary.json",
        "original-bundle-manifest.json": manifest_path, "original-pace-audit.json": RUN/"pace-history-audit/report.json",
        "prior-independent-review.json": LOCAL/"endurance-terminal-snapshot-fix-v1/original24-independent-evidence-review.json",
        "latest-full-junit.xml": full/"junit.xml", "latest-full-returned.json": full/"pytest-returned.json",
        "awake-maintenance-incident.json": LOCAL/"r46-native-endurance-24h-v1-launch/awake-maintenance-incident-v1.json",
        "awake-final-state.json": LOCAL/"r46-native-endurance-24h-v1-awake-recovery-v2/state.json"}
    attachments = []
    for name, source in files.items():
        target = OUT/name
        shutil.copyfile(source, target)
        assert digest(target) == digest(source)
        attachments.append({"path":name, "sha256":digest(target), "bytes":target.stat().st_size})
    raw_paths = [RUN/"progress.json", SESSION/"continuous_endurance_evidence.jsonl",
        SESSION/"continuous_endurance_summary.json", BUNDLE/"interactive/state_events.jsonl",
        RUN/"pace_history.jsonl", rolling, manifest_path]
    result = {"schema_version":"manual-endurance-review.v1", "reviewed_at_utc":datetime.now(timezone.utc).isoformat(),
        "acceptance_method":"User selected original evidence review; no new24h run",
        "conclusion":"ACCEPTED_WITH_DISCLOSED_LIMITATIONS_ORIGINAL_EVIDENCE_REVIEW",
        "scope":"Original R46 native continuity, declared window budgets, terminal completion and sealed payload integrity only",
        "original_automatic_result":"NOT_QUALIFIED", "automatic_result_overridden":False,
        "all_requirements_completed":False, "product_release_approved_by_this_review":False,
        "checks":checks, "running_wall_s":running, "periodic_samples":count, "state_events":state_count,
        "terminal_delay_after_last_sample_s":delay, "original_tail":list(tail), "original_terminal_event":terminal,
        "pace_samples":observations, "fixed_windows":48, "rolling_windows":rolling_count,
        "worst_fixed_p95_ms":max(w["p95_abs_drift_ms"] for w in pace["disjoint_complete_windows"]),
        "p95_budget_ms":pace["metadata"]["p95_budget_ms"], "max_abs_drift_s":pace["captured_peak_abs_drift_s"],
        "hard_realtime_claim":False, "timing_status":progress["session"]["timing_status"],
        "physical_coverage":"PARTIAL", "physical_warning_count":131731,
        "physical_warning_counts":{"soc_bounds":31597,"thermal_bounds":100134},
        "latest_full_regression":{"passed":2504,"failed":3,"skipped":5,"warnings":2,"failed_cases":failed},
        "sealed_payloads":payloads,"attachments":attachments,
        "local_original_bindings":[{"path":p.relative_to(ROOT).as_posix(),"sha256":digest(p),"bytes":p.stat().st_size} for p in raw_paths],
        "limitations":["Original sampler eligibility remains false; this is a separate review conclusion.",
            "Not flight or hardware qualification, complete physical coverage, or hard real-time proof.",
            "Awake helper required recovery; no unattended fault-free infrastructure claim.",
            "Terminal recorder fix is local, uncommitted and uninstalled; latest full regression has3 failures.",
            "Large original traces remain local; digests prove integrity binding, not authentication."]}
    (OUT/"acceptance.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"conclusion":result["conclusion"],"checks":len(checks),"all_checks_pass":all(checks.values()),
        "original_auto":progress["status"],"running_wall_s":running,"payloads_verified":len(payloads),"latest_full_failures":len(failed)}))

if __name__ == "__main__":
    main()
