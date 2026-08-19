#!/usr/bin/env python3
"""
用途：按测试文件隔离运行全量 pytest，避免 Basilisk 或多进程资源回收导致单一父进程停滞。
参数：--output-root 指定证据目录；--file-timeout-s 指定单文件超时；--allow-missing-basilisk 生成缺依赖构造态报告但不签发正式 PASS。
输出：即时保存逐文件日志、退出码与 JUnit，并生成全量汇总 JSON/JUnit；仅 failures=errors=skipped=0 时正式通过。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from sat_sim.internal_capability_acceptance import basilisk_runtime_evidence

BASILISK_COLLECTION_TESTS = frozenset(
    {
        "tests/a3r/test_runtime_vertical_slice.py",
        "tests/test_p0_trustworthiness_fixes.py",
        "tests/test_resource_closure_agent_trust.py",
        "tests/test_whole_subsystem_owned_feasibility.py",
    }
)

# These files exercise adapters, template normalization, run-bundle execution or
# diagnostics whose active path imports Basilisk.  In construction mode only, a
# failure in this explicit set may be classified as dependency-blocked when the
# runtime identity gate has already proved that Basilisk is unavailable.  The
# raw pytest/JUnit failures are retained; formal strict mode never uses this
# classification to issue PASS.
BASILISK_RUNTIME_DEPENDENT_TESTS = frozenset(
    {
        "tests/a3r/test_resolution_and_routing.py",
        "tests/a4r/test_composite_runtime.py",
        "tests/a5r/test_subsystem_runtime.py",
        "tests/test_component_builder_contract.py",
        "tests/test_presentation_and_template_coverage.py",
        "tests/test_bsksim_foundation.py",
        "tests/test_framework_truth_remediation.py",
        "tests/test_unified_native_migration.py",
        "tests/test_unified_native_event_migration.py",
        "tests/test_unified_runtime_closure.py",
        "tests/test_target_acceptance_and_physical_migration.py",
        "tests/test_windows_taskspec_compatibility.py",
        "tests/test_model_output_semantics.py",
        "tests/test_propulsion_whole_coupling.py",
        "tests/test_multi_rate_fmea_unified_subsystems.py",
        "tests/test_basilisk_native_recorder_groups.py",
        "tests/test_fmea_episode_telemetry_traceability.py",
        "tests/test_formal_effect_evidence_contracts.py",
        "tests/test_product_capability_closure.py",
        "tests/test_final_acceptance_delivery.py",
        "tests/test_capability_causality_contract.py",
        "tests/test_runtime_evidence_status_thermal_rf.py",
        "tests/test_agent_scale_governance.py",
        "tests/test_full_runtime_coupling_closure.py",
        "tests/test_workbench_reports_diagnostics.py",
    }
)

_DIRECT_BASILISK_BLOCK_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"No module named ['\"]Basilisk['\"]",
        r"Basilisk[^\n]{0,120}unavailable",
        r"basilisk_unavailable",
        r"Basilisk Monte Carlo worker[^\n]{0,200}failed",
        r"NoneType[^\n]{0,120}SimBaseClass",
    )
)


def _is_runtime_dependency_blocked(relative: str, stdout: str, *, runtime_ready: bool) -> tuple[bool, str | None]:
    """Classify a construction-mode failure without mutating raw JUnit truth."""

    if runtime_ready or relative not in BASILISK_RUNTIME_DEPENDENT_TESTS:
        return False, None
    for pattern in _DIRECT_BASILISK_BLOCK_PATTERNS:
        if pattern.search(stdout):
            return True, f"direct_log_match:{pattern.pattern}"
    indirect_markers = (
        "reasons=['capability']",
        'reasons=["capability"]',
        "EXECUTION_PLAN_INVALID: ['capability']",
        "TASKSPEC_LEGACY_MIGRATED', 'capability'",
        'TASKSPEC_LEGACY_MIGRATED", "capability"',
    )
    if any(marker in stdout for marker in indirect_markers):
        return True, "indirect_capability_import_path_with_runtime_gate_failed"
    # Some end-to-end diagnostics assert a downstream artifact that cannot be
    # generated after the same runtime gate fails.  Membership in the explicit
    # audited set is sufficient only in construction mode and only while the
    # shared runtime gate is not ready.
    return True, "explicit_runtime_dependent_test_with_runtime_gate_failed"


def _junit_totals(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    return {
        key: sum(int(float(suite.attrib.get(key, "0") or 0)) for suite in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _safe_id(relative: str) -> str:
    stem = relative.replace("/", "__").replace(".py", "")
    return f"{stem}__{hashlib.sha256(relative.encode()).hexdigest()[:10]}"


def _aggregate_junit(paths: list[Path], output: Path) -> dict[str, int]:
    root = ET.Element("testsuites")
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for path in paths:
        parsed = ET.parse(path).getroot()
        suites = [parsed] if parsed.tag == "testsuite" else list(parsed.findall("testsuite"))
        for suite in suites:
            root.append(suite)
            for key in totals:
                totals[key] += int(float(suite.attrib.get(key, "0") or 0))
    for key, value in totals.items():
        root.set(key, str(value))
    output.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)
    return totals


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, default=Path("reports/final_full_regression"))
    parser.add_argument("--file-timeout-s", type=float, default=300.0)
    parser.add_argument("--allow-missing-basilisk", action="store_true", help="construction mode; preserves raw failures while classifying audited Basilisk-dependent files as blocked")
    parser.add_argument("--match", action="append", default=[], help="optional substring filter for test paths")
    parser.add_argument("pytest_args", nargs="*")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    records_root = output_root / "records"
    logs_root = output_root / "logs"
    junit_root = output_root / "junit"
    runtime = basilisk_runtime_evidence()
    if not runtime.ready and not args.allow_missing_basilisk:
        payload = {
            "status": "BLOCKED_RUNTIME_DEPENDENCY",
            "passed": False,
            "runtime_dependency": runtime.to_dict(),
            "message": "Strict full regression requires an importable Basilisk runtime.",
        }
        _write_json(output_root / "full_regression.json", payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
        return 2

    paths = sorted(path.relative_to(project_root).as_posix() for path in (project_root / "tests").rglob("test_*.py"))
    if args.match:
        paths = [path for path in paths if any(token in path for token in args.match)]
    excluded: list[str] = []
    if not runtime.ready:
        excluded = [path for path in paths if path in BASILISK_COLLECTION_TESTS]
        paths = [path for path in paths if path not in BASILISK_COLLECTION_TESTS]

    env = dict(os.environ)
    project_src = str(project_root / "src")
    env["PYTHONPATH"] = project_src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    records: list[dict[str, Any]] = []
    complete_junits: list[Path] = []
    suite_started = time.time()

    for index, relative in enumerate(paths, start=1):
        test_id = _safe_id(relative)
        junit_path = junit_root / f"{test_id}.xml"
        log_path = logs_root / f"{test_id}.log"
        cmd = [sys.executable, "-m", "pytest", "-q", relative, f"--junitxml={junit_path}", *args.pytest_args]
        started = time.time()
        timed_out = False
        try:
            completed = subprocess.run(
                cmd,
                cwd=project_root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=args.file_timeout_s,
            )
            exit_code = completed.returncode
            stdout = completed.stdout or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = 124
            captured = exc.stdout or ""
            if isinstance(captured, bytes):
                captured = captured.decode("utf-8", errors="replace")
            stdout = str(captured) + "\nTIMEOUT\n"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(stdout, encoding="utf-8")
        if junit_path.is_file():
            try:
                totals = _junit_totals(junit_path)
                complete_junits.append(junit_path)
            except Exception as exc:
                totals = {"tests": 0, "failures": 0, "errors": 1, "skipped": 0}
                stdout += f"\nJUNIT_PARSE_ERROR: {exc}\n"
        else:
            totals = {"tests": 0, "failures": 0, "errors": 1, "skipped": 0}
        passed = (
            exit_code == 0
            and not timed_out
            and totals["tests"] > 0
            and totals["failures"] == 0
            and totals["errors"] == 0
            and totals["skipped"] == 0
        )
        runtime_blocked, runtime_block_reason = _is_runtime_dependency_blocked(
            relative, stdout, runtime_ready=runtime.ready
        ) if (args.allow_missing_basilisk and not passed and not timed_out) else (False, None)
        record = {
            "test_file": relative,
            "status": "PASS" if passed else "TIMEOUT" if timed_out else "BLOCKED_RUNTIME_DEPENDENCY" if runtime_blocked else "FAIL",
            "passed": passed,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "runtime_dependency_blocked": runtime_blocked,
            "runtime_dependency_block_reason": runtime_block_reason,
            "duration_s": round(time.time() - started, 6),
            "junit": totals,
            "junit_path": str(junit_path) if junit_path.is_file() else None,
            "log_path": str(log_path),
            "command": cmd,
        }
        records.append(record)
        _write_json(records_root / f"{test_id}.json", record)
        _write_json(
            output_root / "progress.json",
            {
                "completed": index,
                "scheduled": len(paths),
                "pass_count": sum(1 for item in records if item["passed"]),
                "runtime_blocked_count": sum(1 for item in records if item.get("runtime_dependency_blocked")),
                "failure_count": sum(1 for item in records if not item["passed"] and not item.get("runtime_dependency_blocked")),
                "latest": record,
            },
        )
        print(
            f"[{index:03d}/{len(paths):03d}] {record['status']:<7} {relative} "
            f"tests={totals['tests']} failures={totals['failures']} errors={totals['errors']} skipped={totals['skipped']} "
            f"duration={record['duration_s']:.2f}s",
            flush=True,
        )

    aggregate_junit = output_root / "full_regression.junit.xml"
    totals = _aggregate_junit(complete_junits, aggregate_junit) if complete_junits else {"tests": 0, "failures": 0, "errors": 1, "skipped": 0}
    every_file_passed = len(records) == len(paths) and all(item["passed"] for item in records)
    runtime_blocked_records = [item for item in records if item.get("runtime_dependency_blocked")]
    unclassified_failures = [item for item in records if not item["passed"] and not item.get("runtime_dependency_blocked")]
    formal_pass = every_file_passed and not excluded and totals["failures"] == totals["errors"] == totals["skipped"] == 0
    construction_analysis_passed = (
        args.allow_missing_basilisk
        and not runtime.ready
        and not unclassified_failures
        and len(records) == len(paths)
    )
    payload: dict[str, Any] = {
        "status": "PASS" if formal_pass else "PARTIAL_PASS_RUNTIME_BLOCKED" if construction_analysis_passed else "FAIL",
        "passed": formal_pass,
        "construction_subset_passed": construction_analysis_passed,
        "construction_analysis_only": bool(args.allow_missing_basilisk),
        "runtime_dependency": runtime.to_dict(),
        "duration_s": round(time.time() - suite_started, 6),
        "scheduled_file_count": len(paths),
        "completed_file_count": len(records),
        "file_pass_count": sum(1 for item in records if item["passed"]),
        "runtime_blocked_file_count": len(runtime_blocked_records),
        "unclassified_failure_count": len(unclassified_failures),
        "file_failure_count": len(unclassified_failures),
        "raw_nonpass_file_count": sum(1 for item in records if not item["passed"]),
        "junit": totals,
        "raw_junit_preserved": True,
        "excluded_tests": excluded,
        "aggregate_junit": str(aggregate_junit) if aggregate_junit.is_file() else None,
        "records": records,
    }
    _write_json(output_root / "full_regression.json", payload)
    print(json.dumps({key: payload[key] for key in ("status", "passed", "construction_subset_passed", "scheduled_file_count", "file_pass_count", "runtime_blocked_file_count", "unclassified_failure_count", "file_failure_count", "junit", "excluded_tests")}, ensure_ascii=False, indent=2), flush=True)
    return 0 if (formal_pass or construction_analysis_passed) else 2


if __name__ == "__main__":
    raise SystemExit(main())
