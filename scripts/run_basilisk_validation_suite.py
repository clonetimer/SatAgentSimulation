#!/usr/bin/env python3
"""
用途：执行 Basilisk 模型与场景验证套件。
参数：--profile、--strict-exit。
输出：生成验证报告并按策略返回退出码。
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports"
DOC_DIR = ROOT / "docs"
SUMMARY_PATH = REPORT_DIR / "bsk_validation_closure_summary.json"
UNSUPPORTED_REPORT_PATH = REPORT_DIR / "unsupported_fault_backlog.json"
DOC_PATH = DOC_DIR / "BSK_VALIDATION_CLOSURE_REPORT.md"
UNSUPPORTED_DOC_PATH = DOC_DIR / "UNSUPPORTED_FAULT_BACKLOG.md"
DOC_CLOSURE_PATH = DOC_DIR / "DOC_CLOSURE_1_REPORT.md"
P0_HARDENING_REPORT_PATH = REPORT_DIR / "p0_validation_hardening_1_summary.json"
P0_HARDENING_DOC_PATH = DOC_DIR / "P0_VALIDATION_HARDENING_1_REPORT.md"

# Legacy statuses that mean the command ran and found known partials.  They are
# execution-success states, not formal acceptance states.
PARTIAL_STATUSES = {
    "PASS_WITH_PARTIAL_QOI",
    "PASS_WITH_PARTIAL_INTEGRATION",
    "PASS_WITH_PARTIAL_WHOLE_SPACECRAFT",
}
PASS_STATUSES = {"PASS"}
FAIL_STATUSES = {"FAIL", "ERROR", "TIMEOUT", "ENV_MISSING_BASILISK"}


def _cmd(batch: str, category: str, command: list[str], timeout_s: int, report: str | None = None, fast: bool = False) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "batch": batch,
        "category": category,
        "command": command,
        "timeout_s": timeout_s,
        "fast": fast,
    }
    if report:
        spec["report_path"] = REPORT_DIR / report
    return spec


ALL_VALIDATION_COMMANDS: list[dict[str, Any]] = [
    _cmd("PYTHON-COMPILEALL", "static_compile", [sys.executable, "-m", "compileall", "-q", "src", "scripts", "tests"], 120, fast=True),
    _cmd("UNIT-TESTS", "unit_tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"], 120, fast=True),
    _cmd("ADCS-RW-FRICTION-NATIVE-1", "subsystem_native_mapping", [sys.executable, "scripts/check_adcs_rw_friction_native.py"], 180, "adcs_rw_friction_native_summary.json", fast=True),
    _cmd("BSK-P0-ADCS-FINITE-1", "p0_hardening", [sys.executable, "scripts/check_p0_adcs_finite.py"], 180, "p0_adcs_finite_1_summary.json", fast=True),
    _cmd("BSK-P0-REVIEW-REPRO-1", "p0_hardening", [sys.executable, "scripts/check_p0_review_repro.py"], 120, "p0_review_repro_1_summary.json", fast=True),
    _cmd("BSK-P0-RUNTIME-FAULT-EVENT-FAST", "p0_hardening", [sys.executable, "scripts/check_p0_runtime_fault_event_fast.py"], 120, "p0_runtime_fault_event_fast_summary.json", fast=True),
    _cmd("BSK-P0-RUNTIME-FAULT-EVENT-1", "p0_hardening", [sys.executable, "scripts/check_p0_runtime_fault_event.py"], 480, "p0_runtime_fault_event_1_summary.json", fast=False),
    _cmd("WHOLESC-STRUCTURE-FIX-2C", "whole_spacecraft_structure", [sys.executable, "scripts/check_whole_spacecraft_structure_2c.py"], 180, "wholesc_structure_fix_2c_summary.json", fast=True),
    _cmd("BSK-BACKLOG-REDUCTION-3", "whole_spacecraft_backlog_reduction", [sys.executable, "scripts/check_whole_spacecraft_backlog_reduction_3.py"], 180, "wholesc_backlog_reduction_3_summary.json", fast=False),
    _cmd("SUBSYS-BACKLOG-RECLASSIFY-1", "historical_backlog", [sys.executable, "scripts/check_subsys_backlog_reclassify.py"], 120, "subsys_direct_injection_backlog_reclassified.json", fast=True),
    _cmd("NATIVE-CAPABILITY-AUDIT-2", "native_capability_audit", [sys.executable, "scripts/check_native_capability_audit_v2.py"], 180, "native_capability_audit_v2_summary.json", fast=True),
    _cmd("LAYER-SCHEMA-CONTRACT-1", "architecture_contract", [sys.executable, "scripts/check_layer_schema_contract.py"], 180, "layer_schema_contract_1_summary.json", fast=True),
    _cmd("WHOLESC-SUBSYSTEM-OWNED-FEASIBILITY-1", "architecture_contract", [sys.executable, "scripts/check_whole_subsystem_owned_feasibility.py"], 180, "wholesc_subsystem_owned_feasibility_1.json", fast=True),
    _cmd("ENV-DATA-ASSET-1+ADCS-SENSOR-NATIVE-1", "environment_and_adcs_sensor_native", [sys.executable, "scripts/check_env_data_asset_adcs_sensor_native.py"], 240, "env_data_asset_adcs_sensor_native_1_summary.json", fast=True),
    _cmd("ADCS-ACTUATOR-NATIVE-2", "adcs_actuator_native", [sys.executable, "scripts/check_adcs_actuator_native_2.py"], 180, "adcs_actuator_native_2_summary.json", fast=True),
    _cmd("COMMDATA-RF-NATIVE-1", "commdata_rf_native", [sys.executable, "scripts/check_commdata_rf_native_1.py"], 180, "commdata_rf_native_1_summary.json", fast=True),
    _cmd("COMMDATA-ODH-EPS-NATIVE-1", "commdata_odh_eps_native", [sys.executable, "scripts/check_commdata_odh_eps_native_1.py"], 240, "commdata_odh_eps_native_1_summary.json", fast=True),
    _cmd("PROPULSION-NATIVE-MAPPING-1", "propulsion_native_mapping", [sys.executable, "scripts/check_propulsion_native_mapping_1.py"], 240, "propulsion_native_mapping_1_summary.json", fast=True),
    _cmd("WHOLESC-NATIVE-CLOSURE-1", "whole_spacecraft_native_closure", [sys.executable, "scripts/check_wholesc_native_closure_1.py"], 240, "wholesc_native_closure_1_summary.json", fast=True),
    _cmd("VALIDATION-DOC-CLOSURE-2", "validation_doc_closure", [sys.executable, "scripts/check_validation_doc_closure_2.py"], 120, "validation_doc_closure_2_summary.json", fast=True),
    _cmd("SIM-TRUTH-AND-GATE-1", "simulation_truth_and_gates", [sys.executable, "scripts/check_sim_truth_and_gate_1.py"], 180, "sim_truth_and_gate_1_summary.json", fast=True),
    _cmd("RUNTIME-INJECTION-CLOSURE-2", "runtime_injection_closure", [sys.executable, "scripts/check_runtime_injection_closure_2.py"], 240, "runtime_injection_closure_2_summary.json", fast=True),
    _cmd("WHOLE-MISSION-COUPLING-2", "whole_mission_coupling", [sys.executable, "scripts/check_whole_mission_coupling_2.py"], 240, "whole_mission_coupling_2_summary.json", fast=True),
    _cmd("PARAMETER-PROVENANCE-CALIBRATION-1", "parameter_provenance_calibration", [sys.executable, "scripts/check_parameter_provenance_calibration_1.py"], 180, "parameter_provenance_calibration_1_summary.json", fast=True),
    _cmd("UQ-MISSION-VALIDATION-1", "uq_mission_validation", [sys.executable, "scripts/check_uq_mission_validation_1.py"], 300, "uq_mission_validation_1_summary.json", fast=True),
    _cmd("PROJECT-STRUCTURE-NORMALIZATION-1", "project_structure", [sys.executable, "scripts/check_project_structure_normalization_1.py"], 120, "project_structure_normalization_1_summary.json", fast=True),
    _cmd("CALIBRATION-EVIDENCE-SURROGATE-1", "calibration_evidence_surrogate", [sys.executable, "scripts/check_calibration_evidence_surrogate_1.py"], 240, "calibration_evidence_surrogate_1_summary.json", fast=True),
    _cmd("WHOLESC-BSK-QOI-1", "whole_spacecraft_qoi", [sys.executable, "scripts/check_whole_spacecraft_basilisk_qoi.py"], 240, "wholesc_bsk_qoi_1_profiles.json", fast=False),
    # Full profile commands below.
    _cmd("SUBSYS-BSK-BUILDER-1", "subsystem_structure", [sys.executable, "scripts/check_subsystem_basilisk_builders.py"], 300, "subsys_bsk_builder_1_closure_capture.json"),
    _cmd("SUBSYS-BSK-RUNNER-1", "subsystem_structure", [sys.executable, "scripts/check_subsystem_basilisk_runners.py"], 300, "subsys_bsk_runner_1_smoke.json"),
    _cmd("SUBSYS-BSK-FAULTDEG-1", "subsystem_fault_degradation", [sys.executable, "scripts/check_subsystem_fault_degradation_coverage.py"], 300, "subsys_bsk_faultdeg_1_coverage.json"),
    _cmd("SUBSYS-BSK-EVENT-1", "subsystem_runtime_injection", [sys.executable, "scripts/check_subsystem_basilisk_runtime_events.py"], 300, "subsys_bsk_event_1_runtime_smoke.json"),
    _cmd("SUBSYS-BSK-EVENT-2", "subsystem_runtime_injection", [sys.executable, "scripts/check_subsystem_basilisk_runtime_targets.py"], 300, "subsys_bsk_event_3_direct_injection.json"),
    _cmd("SUBSYS-BSK-EVENT-3", "subsystem_runtime_injection", [sys.executable, "scripts/check_subsystem_basilisk_direct_injection.py"], 300, "subsys_bsk_event_3_direct_injection.json"),
    _cmd("SUBSYS-BSK-QOI-1", "subsystem_qoi", [sys.executable, "scripts/check_subsystem_basilisk_qoi_matrix.py"], 360, "subsys_bsk_qoi_1_summary.json"),
    _cmd("SUBSYS-BSK-QOI-DIRECTION", "subsystem_qoi", [sys.executable, "scripts/check_subsystem_basilisk_qoi_direction.py"], 360, "subsys_bsk_event_3_qoi_direction.json"),
    _cmd("SUBSYS-BSK-QOI-2", "subsystem_qoi", [sys.executable, "scripts/check_subsystem_basilisk_qoi_profiles.py"], 480, "subsys_bsk_qoi_2_profiles.json"),
    _cmd("INTEG-BSK-QOI-1", "integration_qoi", [sys.executable, "scripts/check_integration_basilisk_qoi_pairwise.py"], 360, "integ_bsk_qoi_1_pairwise.json"),
    _cmd("INTEG-BSK-QOI-2", "integration_qoi", [sys.executable, "scripts/check_integration_basilisk_qoi_dynamic.py"], 360, "integ_bsk_qoi_2_dynamic.json"),
    _cmd("WHOLESC-STRUCTURE-FIX-1", "whole_spacecraft_structure", [sys.executable, "scripts/check_whole_spacecraft_structure.py"], 240, "wholesc_structure_fix_summary.json"),
    _cmd("WHOLESC-STRUCTURE-FIX-2A", "whole_spacecraft_structure", [sys.executable, "scripts/check_whole_spacecraft_structure_2a.py"], 240, "wholesc_structure_fix_2a_summary.json"),
    _cmd("WHOLESC-STRUCTURE-FIX-2B", "whole_spacecraft_structure", [sys.executable, "scripts/check_whole_spacecraft_structure_2b.py"], 240, "wholesc_structure_fix_2b_summary.json"),
    _cmd("BSK-BACKLOG-REDUCTION-1", "whole_spacecraft_backlog_reduction", [sys.executable, "scripts/check_whole_spacecraft_backlog_reduction_1.py"], 240, "wholesc_backlog_reduction_1_summary.json"),
    _cmd("BSK-BACKLOG-REDUCTION-2", "whole_spacecraft_backlog_reduction", [sys.executable, "scripts/check_whole_spacecraft_backlog_reduction_2.py"], 240, "wholesc_backlog_reduction_2_summary.json"),
]


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _last_json_object(text: str) -> Any | None:
    text = text.strip()
    if not text:
        return None
    for idx in range(len(text) - 1, -1, -1):
        if text[idx] == "{":
            try:
                return json.loads(text[idx:])
            except json.JSONDecodeError:
                continue
    return None


def _relative(path: Any) -> str:
    if not path:
        return ""
    p = Path(str(path))
    try:
        return str(p.relative_to(ROOT))
    except Exception:
        return str(p)


def _summary_from_payload(batch: str, payload: Any, returncode: int) -> dict[str, Any]:
    if isinstance(payload, dict):
        if isinstance(payload.get("summary"), dict):
            summary = dict(payload["summary"])
            summary.setdefault("batch", batch)
            return summary
        if "status" in payload:
            summary = dict(payload)
            summary.setdefault("batch", batch)
            return summary
        if batch == "SUBSYS-BSK-FAULTDEG-1":
            subsystems = [k for k in payload if k != "legacy_fault_campaign"]
            return {"batch": batch, "status": "PASS", "subsystem_count": len(subsystems), "subsystems": subsystems}
        if batch == "SUBSYS-BSK-RUNNER-1":
            nominal_failures = [name for name, row in payload.items() if isinstance(row, dict) and row.get("nominal_status") != "PASS"]
            return {"batch": batch, "status": "PASS" if not nominal_failures else "FAIL", "nominal_failures": nominal_failures}
        if batch == "SUBSYS-BSK-BUILDER-1":
            runtime_results = payload.get("runtime_results", {}) if isinstance(payload, dict) else {}
            runtime_failures = {k: v for k, v in runtime_results.items() if v != "PASS"}
            return {"batch": batch, "status": "PASS" if not runtime_failures else "FAIL", "runtime_results": runtime_results}
    return {"batch": batch, "status": "PASS" if returncode == 0 else "FAIL"}


def _partial_count(summary: dict[str, Any]) -> int:
    count = 0
    for key, value in summary.items():
        if key.startswith("partial") and key.endswith("count") or key.startswith("partial_") and key.endswith("_count"):
            try:
                count += int(value)
            except Exception:
                pass
    # Some reports expose dicts rather than counts.
    for key, value in summary.items():
        if key.startswith("partial_by_") and isinstance(value, dict):
            for item in value.values():
                try:
                    count += int(item)
                except Exception:
                    pass
    return count


def validation_result_from_summary(summary: dict[str, Any], returncode: int = 0) -> str:
    """Classify a command's validation result.

    This function is intentionally exported for lightweight unit tests.
    """
    status = str(summary.get("status", ""))
    if returncode != 0 or status in FAIL_STATUSES or status.startswith("FAIL"):
        return "FAIL"
    if int(summary.get("runtime_fault_fallback_count") or 0) > 0:
        return "FAIL"
    if int(summary.get("runtime_fault_event_count") or 0) > 0:
        expected = int(summary.get("runtime_fault_event_count") or 0)
        triggered = int(summary.get("runtime_fault_triggered_count") or 0)
        targets = int(summary.get("runtime_fault_mutation_target_count") or 0)
        if triggered < expected or targets <= 0:
            return "FAIL"
    recovery_expected = int(summary.get("runtime_fault_recovery_expected_count") or 0)
    if recovery_expected > 0:
        recovery_registered = int(summary.get("runtime_fault_recovery_registered_count") or 0)
        recovery_triggered = int(summary.get("runtime_fault_recovery_triggered_count") or 0)
        recovery_failures = int(summary.get("runtime_fault_recovery_failure_count") or 0)
        if (
            recovery_registered < recovery_expected
            or recovery_triggered < recovery_expected
            or recovery_failures > 0
        ):
            return "FAIL"
    if status in PARTIAL_STATUSES or _partial_count(summary) > 0:
        return "PARTIAL"
    if status in PASS_STATUSES or status == "":
        return "PASS"
    return "UNKNOWN"


def _run_command(spec: dict[str, Any]) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONPATH"] = "src" + os.pathsep + "." + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("OPENBLAS_NUM_THREADS", "1")
    env.setdefault("OMP_NUM_THREADS", "1")
    env.setdefault("MKL_NUM_THREADS", "1")
    started = datetime.now(timezone.utc)
    timeout_s = int(spec.get("timeout_s", 300))
    proc: subprocess.Popen[str] | None = None
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    safe_batch = str(spec["batch"]).replace("/", "_").replace(" ", "_")
    stdout_path = REPORT_DIR / f".{safe_batch}.stdout.log"
    stderr_path = REPORT_DIR / f".{safe_batch}.stderr.log"

    def _tail(path: Path, limit: int = 2000) -> str:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            return text[-limit:]
        except Exception:
            return ""

    try:
        with stdout_path.open("w", encoding="utf-8") as stdout_f, stderr_path.open("w", encoding="utf-8") as stderr_f:
            proc = subprocess.Popen(
                spec["command"], cwd=ROOT, env=env, text=True, stdout=stdout_f, stderr=stderr_f
            )
            try:
                returncode = int(proc.wait(timeout=timeout_s) or 0)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=10)
                except Exception:
                    pass
                duration_s = (datetime.now(timezone.utc) - started).total_seconds()
                return {
                    "batch": spec["batch"],
                    "category": spec["category"],
                    "command": " ".join(spec["command"]),
                    "returncode": None,
                    "duration_s": duration_s,
                    "execution_status": "TIMEOUT",
                    "validation_result": "FAIL",
                    "result_source": "timeout",
                    "summary": {"batch": spec["batch"], "status": "TIMEOUT"},
                    "error": f"TimeoutExpired after {timeout_s}s",
                    "stdout_tail": _tail(stdout_path),
                    "stderr_tail": _tail(stderr_path),
                }

        duration_s = (datetime.now(timezone.utc) - started).total_seconds()
        stdout = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.exists() else ""
        stderr = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
        payload = None
        summary_source = "stdout_json"
        report_path = spec.get("report_path")
        if report_path and Path(report_path).exists():
            payload = _load_json(Path(report_path))
            summary_source = "executed_report_file"
        if payload is None:
            payload = _last_json_object(stdout) or _last_json_object(stderr)
        summary = _summary_from_payload(spec["batch"], payload, returncode)
        if spec["batch"] == "PYTHON-COMPILEALL" and returncode == 0:
            summary = {"batch": spec["batch"], "status": "PASS"}
        validation_result = validation_result_from_summary(summary, returncode)
        execution_status = "EXECUTED" if returncode == 0 else "ERROR"
        return {
            "batch": spec["batch"],
            "category": spec["category"],
            "command": " ".join(spec["command"]),
            "returncode": returncode,
            "duration_s": duration_s,
            "execution_status": execution_status,
            "validation_result": validation_result,
            "result_source": "executed_command",
            "summary_source": summary_source,
            "summary": _jsonable(summary),
            "report_path": _relative(spec.get("report_path", "")),
            "stdout_tail": stdout[-2000:],
            "stderr_tail": stderr[-2000:],
        }
    except Exception as exc:  # noqa: BLE001
        if proc is not None and proc.poll() is None:
            proc.kill()
        return {
            "batch": spec["batch"],
            "category": spec["category"],
            "command": " ".join(spec["command"]),
            "returncode": None,
            "duration_s": (datetime.now(timezone.utc) - started).total_seconds(),
            "execution_status": "ERROR",
            "validation_result": "FAIL",
            "result_source": "executed_command_error",
            "summary": {"batch": spec["batch"], "status": "ERROR"},
            "error": f"{type(exc).__name__}: {exc}",
        }

def _environment_summary() -> dict[str, Any]:
    basilisk: dict[str, Any]
    try:
        import Basilisk  # type: ignore
        basilisk = {"import_ok": True, "version": getattr(Basilisk, "__version__", "unknown"), "file": getattr(Basilisk, "__file__", None)}
    except Exception as exc:  # noqa: BLE001
        basilisk = {"import_ok": False, "error": f"{type(exc).__name__}: {exc}"}
    packages: dict[str, str | None] = {}
    try:
        from importlib.metadata import version
        for name in ("bsk", "colorama", "sgp4", "numpy"):
            try:
                packages[name] = version(name)
            except Exception:
                packages[name] = None
    except Exception:
        pass
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "basilisk": basilisk,
        "packages": packages,
        "dependency_files": {
            "requirements_txt": (ROOT / "requirements.txt").exists(),
            "pyproject_toml": (ROOT / "pyproject.toml").exists(),
        },
    }


def _load_report_if_exists(name: str) -> Any:
    path = REPORT_DIR / name
    return _load_json(path) if path.exists() else None


def _report_summary(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict) and isinstance(payload.get("summary"), dict):
        return payload["summary"]
    if isinstance(payload, dict):
        return payload
    return {}


def _build_coverage_matrix(command_results: list[dict[str, Any]]) -> dict[str, Any]:
    by_batch = {row["batch"]: row for row in command_results}
    subsys_qoi = _report_summary(_load_report_if_exists("subsys_bsk_qoi_2_profiles.json") or {})
    integ1 = _report_summary(_load_report_if_exists("integ_bsk_qoi_1_pairwise.json") or {})
    integ2 = _report_summary(_load_report_if_exists("integ_bsk_qoi_2_dynamic.json") or {})
    whole_qoi = _report_summary(_load_report_if_exists("wholesc_bsk_qoi_1_profiles.json") or {})
    whole_2c = _load_report_if_exists("wholesc_structure_fix_2c_summary.json") or {}
    backlog1 = _report_summary(_load_report_if_exists("wholesc_backlog_reduction_1_summary.json") or {})
    backlog2 = _report_summary(_load_report_if_exists("wholesc_backlog_reduction_2_summary.json") or {})
    direct = _load_report_if_exists("subsys_bsk_event_3_direct_injection.json") or {}
    subsystems = ("adcs", "comm_data", "eps", "payload", "propulsion", "thermal")
    subsystem_rows: dict[str, dict[str, Any]] = {}
    for subsystem in subsystems:
        subsystem_rows[subsystem] = {
            "builder": "validated" if by_batch.get("SUBSYS-BSK-BUILDER-1", {}).get("validation_result") == "PASS" else "not_run_or_not_validated",
            "runner_modes": "validated" if by_batch.get("SUBSYS-BSK-RUNNER-1", {}).get("validation_result") == "PASS" else "not_run_or_not_validated",
            "fault_degradation_catalog": "validated" if by_batch.get("SUBSYS-BSK-FAULTDEG-1", {}).get("validation_result") == "PASS" else "not_run_or_not_validated",
            "direct_runtime_injection": "validated_with_unsupported_filtered" if direct.get("status") == "PASS" else "not_run_or_not_validated",
            "qoi_profile_checks": (subsys_qoi.get("checks_by_subsystem") or {}).get(subsystem),
            "qoi_failures": (subsys_qoi.get("failures_by_subsystem") or {}).get(subsystem),
        }
    matrices = (whole_2c.get("matrices") or {}) if isinstance(whole_2c, dict) else {}
    return {
        "subsystem_coverage": subsystem_rows,
        "integration_coverage": {
            "pairwise": {"status": integ1.get("status"), "checks_by_pair": integ1.get("checks_by_pair"), "partial_by_pair": integ1.get("partial_by_pair")},
            "dynamic": {"status": integ2.get("status"), "checks_by_pair": integ2.get("checks_by_pair"), "partial_by_pair": integ2.get("partial_by_pair")},
        },
        "whole_spacecraft_coverage": {
            "structure_2c": by_batch.get("WHOLESC-STRUCTURE-FIX-2C", {}).get("summary") or _report_summary(whole_2c),
            "backlog_reduction_1": backlog1,
            "backlog_reduction_2": backlog2,
            "qoi": {"status": whole_qoi.get("status"), "checks_by_scenario": whole_qoi.get("checks_by_scenario"), "partial_by_scenario": whole_qoi.get("partial_by_scenario")},
            "coupling_matrix": matrices.get("coupling_matrix", {}),
            "fault_injection_matrix": matrices.get("fault_injection_matrix", {}),
            "degradation_injection_matrix": matrices.get("degradation_injection_matrix", {}),
        },
    }


def _collect_unsupported_backlog() -> list[dict[str, Any]]:
    """Collect open capability/remediation items.

    The legacy function name is retained for report compatibility.  The
    authoritative subsystem source is NATIVE-CAPABILITY-AUDIT-2; the old
    32-row direct-injection reclassification is historical input only.
    """
    backlog: list[dict[str, Any]] = []
    native = _load_report_if_exists("native_capability_blocking_backlog_v2.json") or {}
    for row in native.get("active", []):
        backlog.append({
            "scope": "native_capability_remediation",
            "status": "open_native_mapping_or_chain",
            "priority": row.get("priority", "P1"),
            "backlog_id": row.get("backlog_id"),
            "component": ",".join(row.get("components", [])),
            "reason": row.get("rationale", ""),
            "recommended_backlog_item": "; ".join(row.get("acceptance", [])),
            "depends_on": row.get("depends_on", []),
        })
    if not native.get("active"):
        # Compatibility fallback for older packages.
        reclassified = _load_report_if_exists("subsys_direct_injection_backlog_reclassified.json") or {}
        for row in reclassified.get("items", []):
            if row.get("acceptance_action") not in {"must_fix", "must_fix_next_batch"}:
                continue
            backlog.append({
                **row,
                "scope": "legacy_subsystem_capability_mapping",
                "status": "legacy_metric_replaced",
                "priority": row.get("priority", "P1"),
                "reason": row.get("capability_evidence", row.get("reason", "")),
                "recommended_backlog_item": "Regenerate NATIVE-CAPABILITY-AUDIT-2 and use its grouped remediation backlog.",
            })
    whole_2c = _load_report_if_exists("wholesc_structure_fix_2c_summary.json") or {}
    matrices = whole_2c.get("matrices") or {}
    for subsystem, row in (matrices.get("fault_injection_matrix") or {}).items():
        status = row.get("fault_injection_status")
        if status in {"metadata_only", "partial_runtime_supported", "unsupported"}:
            backlog.append({
                "scope": "whole_spacecraft_fault_injection",
                "subsystem": subsystem,
                "status": status,
                "priority": "P1" if status == "partial_runtime_supported" else "P2",
                "reason": "Whole-spacecraft fault support is not yet a complete runtime mutation path for this subsystem.",
                "evidence": row.get("evidence", []),
                "recommended_backlog_item": "Expose stable subsystem-level runtime fault hooks and route them through whole_spacecraft.faults.",
            })
    for subsystem, row in (matrices.get("degradation_injection_matrix") or {}).items():
        status = row.get("degradation_injection_status")
        if status in {"metadata_only", "partial_runtime_supported", "unsupported"}:
            backlog.append({
                "scope": "whole_spacecraft_degradation_injection",
                "subsystem": subsystem,
                "status": status,
                "priority": "P2" if status == "metadata_only" else "P1",
                "reason": "Whole-spacecraft degradation support is not yet mapped to build/runtime objects for this subsystem.",
                "evidence": row.get("evidence", []),
                "recommended_backlog_item": "Expose subsystem degradation factories or runtime parameter hooks and consume them in whole_spacecraft.degradation.",
            })
    for name, row in (matrices.get("coupling_matrix") or {}).items():
        status = row.get("coupling_status")
        if status in {"not_coupled_no_eclipse_message", "disabled_by_schema"}:
            backlog.append({
                "scope": "whole_spacecraft_normal_coupling",
                "link": name,
                "status": status,
                "priority": "P2" if status == "not_coupled_no_eclipse_message" else "P3",
                "reason": "Normal coupling is not active in the default graph for this link.",
                "evidence": row.get("evidence", []),
                "recommended_backlog_item": "Enable or implement the corresponding message/object bridge in WholeSpacecraftCouplingConfig and builder.",
            })
    return backlog


def _write_unsupported_reports(backlog: list[dict[str, Any]]) -> dict[str, Any]:
    by_scope = Counter(row.get("scope", "unknown") for row in backlog)
    by_priority = Counter(row.get("priority", "unknown") for row in backlog)
    payload = {
        "summary": {
            "status": "OPEN_BACKLOG" if backlog else "PASS",
            "metric_name": "capability_and_injection_backlog",
            "legacy_unsupported_metric": "deprecated",
            "backlog_count": len(backlog),
            "by_scope": dict(sorted(by_scope.items())),
            "by_priority": dict(sorted(by_priority.items())),
        },
        "unsupported_backlog": backlog,
    }
    UNSUPPORTED_REPORT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# Capability Mapping / Fault / Degradation / Coupling Backlog",
        "",
        "The filename is retained for compatibility, but the old aggregate `unsupported` metric is deprecated. The authoritative source is the field-level native capability audit; only open native mapping, chain, ownership and integration actions are blocking.",
        "",
        "## Summary",
        "",
        f"- status: `{payload['summary']['status']}`",
        f"- backlog_count: `{len(backlog)}`",
        f"- by_scope: `{dict(sorted(by_scope.items()))}`",
        f"- by_priority: `{dict(sorted(by_priority.items()))}`",
        "",
        "## Backlog items",
        "",
        "| Scope | Target | Status | Priority | Reason | Recommended item |",
        "|---|---|---:|---:|---|---|",
    ]
    for row in backlog:
        if row.get("scope") == "subsystem_capability_mapping" and row.get("item"):
            target = f"{row.get('subsystem', 'unknown')}:{row['item']}"
        else:
            target = row.get("subsystem") or row.get("link") or row.get("component") or "unknown"
        reason = str(row.get("reason", "")).replace("|", "/")
        rec = str(row.get("recommended_backlog_item", "")).replace("|", "/")
        lines.append(f"| `{row.get('scope')}` | `{target}` | `{row.get('status')}` | `{row.get('priority', '')}` | {reason[:180]} | {rec[:180]} |")
    UNSUPPORTED_DOC_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


def _write_main_doc(report: dict[str, Any], unsupported_payload: dict[str, Any]) -> None:
    s = report["summary"]
    lines = [
        "# BSK Validation Closure Report",
        "",
        "## Summary",
        "",
        f"- profile: `{s['profile']}`",
        f"- status: `{s['status']}`",
        f"- execution_status: `{s['execution_status']}`",
        f"- formal_acceptance_status: `{s['formal_acceptance_status']}`",
        f"- command_count: `{s['command_count']}`",
        f"- command_execution_fail_count: `{s['command_execution_fail_count']}`",
        f"- validation_fail_count: `{s['validation_fail_count']}`",
        f"- partial_command_count: `{s['partial_command_count']}`",
        f"- unsupported_backlog_count: `{s['unsupported_backlog_count']}`",
        "",
        "> `formal_acceptance_status` is intentionally not PASS while partial checks or open backlog remain. This report is an executed validation/audit report, not a formal acceptance certificate.",
        "",
        "## Environment",
        "",
        f"- Python: `{report['environment']['python'].split()[0]}`",
        f"- Platform: `{report['environment']['platform']}`",
        f"- Basilisk import: `{report['environment']['basilisk'].get('import_ok')}`",
        f"- Basilisk version: `{report['environment']['basilisk'].get('version')}`",
        f"- Packages: `{report['environment'].get('packages')}`",
        "",
        "## Command results",
        "",
        "| Batch | Category | Execution | Validation | Source | Duration s | Report |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for row in report["command_results"]:
        lines.append(f"| `{row['batch']}` | `{row['category']}` | `{row['execution_status']}` | `{row['validation_result']}` | `{row.get('result_source')}` | `{row.get('duration_s', 0):.1f}` | `{row.get('report_path', '')}` |")
    lines.extend([
        "",
        "## Fast/full usage",
        "",
        "```bash",
        "PYTHONPATH=src python scripts/run_basilisk_validation_suite.py --profile fast",
        "PYTHONPATH=src python scripts/run_basilisk_validation_suite.py --profile full",
        "PYTHONPATH=src python scripts/run_basilisk_validation_suite.py --profile fast --strict-exit",
        "```",
        "",
        "The default fast profile is designed for quick local复审. The full profile is exhaustive and may exceed constrained execution windows.",
        "",
        "## Open capability / injection backlog",
        "",
        f"See `docs/UNSUPPORTED_FAULT_BACKLOG.md` (legacy filename, capability backlog content). Current open count: `{unsupported_payload['summary']['backlog_count']}`.",
    ])
    DOC_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_doc_closure(report: dict[str, Any]) -> None:
    docs = sorted(p.name for p in DOC_DIR.glob("*.md"))
    required = [
        "P0_ADCS_FINITE_1_REPORT.md",
        "P0_RUNTIME_FAULT_EVENT_1_REPORT.md",
        "P0_VALIDATION_HARDENING_1_REPORT.md",
        "BSK_VALIDATION_CLOSURE_REPORT.md",
        "UNSUPPORTED_FAULT_BACKLOG.md",
    ]
    missing = [name for name in required if name not in docs]
    lines = [
        "# DOC Closure 1 Report",
        "",
        f"- status: `{'PASS' if not missing else 'FAIL'}`",
        f"- documented_files: `{len(docs)}`",
        f"- missing_required_docs: `{missing}`",
        "",
        "## Required P0 closure docs",
        "",
    ]
    for name in required:
        lines.append(f"- [{'x' if name in docs else ' '}] `{name}`")
    DOC_CLOSURE_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_p0_hardening_doc(report: dict[str, Any]) -> None:
    s = report["summary"]
    lines = [
        "# P0 Validation Hardening 1 Report",
        "",
        "## Summary",
        "",
        f"- batch: `{s['batch']}`",
        f"- profile: `{s['profile']}`",
        f"- status: `{s['status']}`",
        f"- execution_status: `{s['execution_status']}`",
        f"- formal_acceptance_status: `{s['formal_acceptance_status']}`",
        f"- command_execution_fail_count: `{s['command_execution_fail_count']}`",
        f"- validation_fail_count: `{s['validation_fail_count']}`",
        f"- partial_command_count: `{s['partial_command_count']}`",
        f"- unsupported_backlog_count: `{s['unsupported_backlog_count']}`",
        "",
        "## Hardened semantics",
        "",
        "- NaN/Inf in critical telemetry is a hard failure in whole-spacecraft summaries.",
        "- Runtime fault events must register, trigger, avoid fallback, and mutate live targets.",
        "- `PASS_WITH_PARTIAL_*` is classified as `PARTIAL`, not formal PASS.",
        "- Open unsupported backlog prevents formal acceptance PASS.",
        "- Each command row records execution source and report source.",
        "",
        "## Dependency/test additions",
        "",
        "- Added `requirements.txt`.",
        "- Added `pyproject.toml`.",
        "- Added `tests/` with unittest-based hardening guards.",
    ]
    P0_HARDENING_DOC_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _select_commands(profile: str) -> list[dict[str, Any]]:
    if profile == "fast":
        return [spec for spec in ALL_VALIDATION_COMMANDS if spec.get("fast")]
    if profile == "full":
        return list(ALL_VALIDATION_COMMANDS)
    raise ValueError(f"unknown profile: {profile}")


def run_suite(profile: str = "fast") -> dict[str, Any]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    DOC_DIR.mkdir(parents=True, exist_ok=True)
    specs = _select_commands(profile)
    command_results: list[dict[str, Any]] = []
    started = time.monotonic()
    for spec in specs:
        print(f"[bsk-validation] running {spec['batch']}", file=sys.stderr, flush=True)
        result = _run_command(spec)
        print(f"[bsk-validation] completed {spec['batch']} execution={result.get('execution_status')} validation={result.get('validation_result')}", file=sys.stderr, flush=True)
        command_results.append(result)
    coverage_matrix = _build_coverage_matrix(command_results)
    backlog = _collect_unsupported_backlog()
    unsupported_payload = _write_unsupported_reports(backlog)

    execution_fail_count = sum(1 for row in command_results if row["execution_status"] not in {"EXECUTED"})
    validation_fail_count = sum(1 for row in command_results if row["validation_result"] in {"FAIL", "UNKNOWN"})
    partial_command_count = sum(1 for row in command_results if row["validation_result"] == "PARTIAL")
    timeout_count = sum(1 for row in command_results if row["execution_status"] == "TIMEOUT")
    fallback_count = 0
    for row in command_results:
        summary = row.get("summary") or {}
        fallback_count += int(summary.get("runtime_fault_fallback_count") or 0)

    execution_status = "PASS" if execution_fail_count == 0 else "FAIL"
    if validation_fail_count == 0 and partial_command_count == 0 and len(backlog) == 0:
        formal_acceptance_status = "PASS"
        status = "PASS"
    elif execution_fail_count == 0 and validation_fail_count == 0:
        formal_acceptance_status = "FAIL_LIMITED_REMEDIATION_REQUIRED"
        status = "EXECUTED_WITH_KNOWN_PARTIAL_OR_BACKLOG"
    else:
        formal_acceptance_status = "FAIL"
        status = "FAIL"

    report = {
        "summary": {
            "batch": "BSK-P0-VALIDATION-HARDENING-1",
            "profile": profile,
            "status": status,
            "execution_status": execution_status,
            "formal_acceptance_status": formal_acceptance_status,
            "command_count": len(command_results),
            "command_execution_fail_count": execution_fail_count,
            "validation_fail_count": validation_fail_count,
            "partial_command_count": partial_command_count,
            "timeout_count": timeout_count,
            "runtime_fault_fallback_count": fallback_count,
            "unsupported_backlog_count": len(backlog),
            "duration_s": time.monotonic() - started,
        },
        "environment": _environment_summary(),
        "command_results": command_results,
        "status_counts": {
            "execution": dict(Counter(row["execution_status"] for row in command_results)),
            "validation": dict(Counter(row["validation_result"] for row in command_results)),
            "by_category_validation": {k: dict(v) for k, v in _by_category_validation(command_results).items()},
        },
        "coverage_matrix": coverage_matrix,
        "unsupported_backlog_summary": unsupported_payload["summary"],
        "notes": (
            "This suite separates execution success from formal acceptance. Partial checks and open backlog are not reported as formal PASS.",
        ),
    }
    SUMMARY_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    P0_HARDENING_REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    _write_main_doc(report, unsupported_payload)
    _write_p0_hardening_doc(report)
    _write_doc_closure(report)
    return report


def _by_category_validation(command_results: Iterable[dict[str, Any]]) -> dict[str, Counter]:
    out: dict[str, Counter] = defaultdict(Counter)
    for row in command_results:
        out[row["category"]][row["validation_result"]] += 1
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("fast", "full"), default="fast", help="Validation profile. fast is intended to complete in constrained local review windows.")
    parser.add_argument("--strict-exit", action="store_true", help="Exit nonzero unless formal_acceptance_status is PASS. Without this flag, the script exits nonzero only for execution/validation failures.")
    args = parser.parse_args(argv)
    report = run_suite(args.profile)
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False, sort_keys=True))
    summary = report["summary"]
    if args.strict_exit:
        return 0 if summary["formal_acceptance_status"] == "PASS" else 1
    return 0 if summary["execution_status"] == "PASS" and summary["validation_fail_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
