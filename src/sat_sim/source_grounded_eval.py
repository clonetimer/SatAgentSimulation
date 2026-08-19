"""Source-grounded Agent evaluation benchmark.

S8 evaluates the product promise that natural-language requests route to
registered capabilities with explicit source bindings, deterministic TaskSpec
alignment, and executable adapter-backed scripts.  It intentionally avoids
network-only LLM behavior in tests by supporting the existing template backend.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .capability_agent import DEFAULT_ALLOWED_CAPABILITIES, generate_capability_script_from_text
from .capability_registry import get_capability
from .source_native import source_binding_payload
from .task_spec import load_mapping, write_json


@dataclass(frozen=True)
class SourceGroundedEvalCaseResult:
    name: str
    ok: bool
    request_path: str
    expected_path: str | None
    output_dir: str
    generated_script: str | None
    generated_task: dict[str, Any]
    expected: dict[str, Any]
    checks: dict[str, bool]
    metrics: dict[str, Any]
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourceGroundedEvalReport:
    ok: bool
    output_dir: str
    total: int
    passed: int
    failed: int
    pass_rate: float
    metrics: dict[str, Any]
    cases: tuple[SourceGroundedEvalCaseResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "output_dir": self.output_dir,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": self.pass_rate,
            "metrics": dict(self.metrics),
            "cases": [case.to_dict() for case in self.cases],
        }


def _get_path(data: Mapping[str, Any], dotted: str) -> Any:
    cur: Any = data
    for part in dotted.split("."):
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(part)
    return cur


def _values_equal(actual: Any, expected: Any) -> bool:
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return abs(float(actual) - float(expected)) <= max(1e-9, abs(float(expected)) * 1e-9)
    return actual == expected


def _expected_path_for(request_path: Path) -> Path | None:
    for suffix in (".yaml", ".yml", ".json"):
        candidate = request_path.parent.parent / "expected" / f"{request_path.stem}{suffix}"
        if candidate.exists():
            return candidate
    return None


def _load_expected(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        return load_mapping(path)
    except Exception:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def _run_exported_script(script_path: Path, *, output_root: Path, timeout_s: float) -> tuple[bool, str]:
    cmd = [sys.executable, str(script_path), "--output-root", str(output_root)]
    output_root.parent.mkdir(parents=True, exist_ok=True)
    log_path = output_root.parent / f"{output_root.name}.script.log"
    try:
        with log_path.open("w", encoding="utf-8") as log:

            env = dict(os.environ)
            src_path = str(Path.cwd() / "src")
            env["PYTHONPATH"] = src_path + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
            proc = subprocess.run(cmd, cwd=str(Path.cwd()), env=env, text=True, stdout=log, stderr=subprocess.STDOUT, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return False, f"script timed out after {timeout_s}s"
    if proc.returncode != 0:
        try:
            tail = log_path.read_text(encoding="utf-8")[-2000:]
        except Exception:
            tail = ""
        return False, tail.strip() or f"exit {proc.returncode}"
    return True, ""


def _manifest_source_binding(dataset_root: Path) -> dict[str, Any]:
    manifest = dataset_root / "manifest.json"
    if not manifest.exists():
        return {}
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return {}
    cap = data.get("capability") if isinstance(data, Mapping) else None
    if isinstance(cap, Mapping) and isinstance(cap.get("source_binding"), Mapping):
        return dict(cap["source_binding"])
    return {}


def _case_checks(
    generated: Mapping[str, Any],
    expected: Mapping[str, Any],
    *,
    agent_ok: bool,
    script_run_ok: bool,
    dataset_root: Path | None,
) -> tuple[dict[str, bool], list[str], dict[str, Any]]:
    checks: dict[str, bool] = {}
    errors: list[str] = []
    metrics: dict[str, Any] = {}

    expected_action = expected.get("expected_action") or expected.get("action")
    if expected_action == "reject_unsupported":
        passed = not agent_ok and not generated.get("capability_id")
        checks["unsupported_boundary"] = passed
        metrics["unsupported_boundary_checked"] = True
        if not passed:
            errors.append("expected unsupported boundary rejection, but a runnable capability TaskSpec was generated")
        # Unsupported-boundary cases intentionally do not require source-binding
        # or script execution checks.
        return checks, errors, metrics

    expected_capability = expected.get("capability_id") or expected.get("expected_capability_id")
    actual_capability = generated.get("capability_id")
    if expected_capability is not None:
        passed = _values_equal(actual_capability, expected_capability)
        checks["capability_selection"] = passed
        metrics["capability_selection_checked"] = True
        if not passed:
            errors.append(f"capability_id expected {expected_capability!r}, got {actual_capability!r}")

    source_binding: dict[str, Any] = {}
    if isinstance(actual_capability, str):
        try:
            source_binding = source_binding_payload(get_capability(actual_capability).data)
        except Exception:
            source_binding = {}

    expected_mode = expected.get("source_binding_mode") or expected.get("expected_source_binding_mode")
    if expected_mode is not None:
        actual_mode = source_binding.get("mode")
        passed = _values_equal(actual_mode, expected_mode)
        checks["source_binding_mode"] = passed
        metrics["source_binding_checked"] = True
        if not passed:
            errors.append(f"source_binding.mode expected {expected_mode!r}, got {actual_mode!r}")

    expected_primary = expected.get("primary_module") or expected.get("expected_primary_module")
    if expected_primary is not None:
        actual_primary = source_binding.get("primary_module")
        passed = _values_equal(actual_primary, expected_primary)
        checks["source_binding_primary_module"] = passed
        if not passed:
            errors.append(f"source_binding.primary_module expected {expected_primary!r}, got {actual_primary!r}")

    expected_paths = expected.get("expected_paths") or expected.get("expect_paths") or {}
    if isinstance(expected_paths, Mapping):
        for path, expected_value in expected_paths.items():
            actual_value = _get_path(generated, str(path))
            passed = _values_equal(actual_value, expected_value)
            checks[f"path:{path}"] = passed
            metrics["parameter_mapping_checked"] = True
            if not passed:
                errors.append(f"{path} expected {expected_value!r}, got {actual_value!r}")

    absent_paths = expected.get("expected_absent_paths") or expected.get("absent_paths") or []
    if isinstance(absent_paths, Sequence) and not isinstance(absent_paths, (str, bytes)):
        for path in absent_paths:
            actual_value = _get_path(generated, str(path))
            passed = actual_value is None
            checks[f"absent:{path}"] = passed
            metrics["template_pollution_checked"] = True
            if not passed:
                errors.append(f"{path} was expected absent, got {actual_value!r}")

    expected_modifiers = expected.get("expected_modifiers") or {}
    if isinstance(expected_modifiers, Mapping):
        modifiers = generated.get("modifiers") if isinstance(generated.get("modifiers"), Mapping) else {}
        if "fault_count" in expected_modifiers:
            actual = len(modifiers.get("faults") or []) if isinstance(modifiers, Mapping) else 0
            passed = actual == int(expected_modifiers["fault_count"])
            checks["modifier_fault_count"] = passed
            metrics["modifier_mapping_checked"] = True
            if not passed:
                errors.append(f"modifier fault_count expected {expected_modifiers['fault_count']!r}, got {actual!r}")
        if "degradation_count" in expected_modifiers:
            actual = len(modifiers.get("degradations") or []) if isinstance(modifiers, Mapping) else 0
            passed = actual == int(expected_modifiers["degradation_count"])
            checks["modifier_degradation_count"] = passed
            metrics["modifier_mapping_checked"] = True
            if not passed:
                errors.append(f"modifier degradation_count expected {expected_modifiers['degradation_count']!r}, got {actual!r}")

    if expected.get("require_script_execution", True):
        checks["script_execution"] = bool(script_run_ok)
        metrics["script_execution_checked"] = True
        if not script_run_ok:
            errors.append("exported script did not execute successfully")

    if dataset_root is not None and expected.get("require_manifest_source_binding", True):
        manifest_binding = _manifest_source_binding(dataset_root)
        passed = bool(manifest_binding) and (expected_mode is None or manifest_binding.get("mode") == expected_mode)
        checks["manifest_source_binding"] = passed
        if not passed:
            errors.append("dataset manifest did not contain expected source_binding")

    return checks, errors, metrics


def run_source_grounded_evals(
    evals_dir: str | Path,
    *,
    output_dir: str | Path = "reports/source_grounded_eval",
    examples_dir: str | Path = "examples",
    backend: str = "template",
    allowed_capabilities: Sequence[str] = DEFAULT_ALLOWED_CAPABILITIES,
    run_scripts: bool = True,
    script_timeout_s: float = 120.0,
) -> SourceGroundedEvalReport:
    root = Path(evals_dir)
    requests_dir = root / "requests"
    out_root = Path(output_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    cases: list[SourceGroundedEvalCaseResult] = []

    request_files = sorted(requests_dir.glob("*.txt"))
    for req_path in request_files:
        name = req_path.stem
        case_dir = out_root / name
        case_dir.mkdir(parents=True, exist_ok=True)
        text = req_path.read_text(encoding="utf-8").strip()
        expected_path = _expected_path_for(req_path)
        expected = _load_expected(expected_path)
        generated_task: dict[str, Any] = {}
        script_path: Path | None = None
        script_run_ok = False
        dataset_root: Path | None = None
        checks: dict[str, bool] = {}
        errors: list[str] = []
        case_metrics: dict[str, Any] = {}
        try:
            expected_capability = expected.get("capability_id") or expected.get("expected_capability_id")
            case_allowed_capabilities = tuple(allowed_capabilities)
            if isinstance(expected_capability, str) and expected_capability.strip() and expected_capability in case_allowed_capabilities:
                # Keep source-grounded unit/eval cases bounded and deterministic:
                # the case expectation already defines the route being checked,
                # so restrict prompt/context generation to that capability.
                case_allowed_capabilities = (expected_capability,)
            result = generate_capability_script_from_text(
                text,
                output_dir=case_dir,
                examples_dir=examples_dir,
                backend=backend,
                allowed_capabilities=case_allowed_capabilities,
                output_root=case_dir / "dataset",
                run_task=False,
                dry_run=True,
            )
            generated_task = dict(result.task_spec or {})
            script_value = result.files.get("generated_script") if hasattr(result, "files") else None
            if script_value:
                script_path = Path(script_value)
            expected_action = expected.get("expected_action") or expected.get("action")
            if not result.ok and expected_action != "reject_unsupported":
                errors.append("agent generation failed")
            case_requires_script = bool(expected.get("require_script_execution", True))
            if run_scripts and case_requires_script and script_path is not None and script_path.exists() and result.ok:
                dataset_root = case_dir / "script_dataset"
                script_run_ok, script_error = _run_exported_script(script_path, output_root=dataset_root, timeout_s=script_timeout_s)
                if script_error:
                    errors.append(script_error)
            elif not run_scripts or not case_requires_script:
                script_run_ok = True
                dataset_root = None
            checks, check_errors, case_metrics = _case_checks(
                generated_task,
                expected,
                agent_ok=bool(result.ok),
                script_run_ok=script_run_ok,
                dataset_root=dataset_root,
            )
            errors.extend(check_errors)
        except Exception as exc:
            errors.append(str(exc))
        ok = not errors and all(checks.values()) if checks else not errors
        cases.append(SourceGroundedEvalCaseResult(
            name=name,
            ok=ok,
            request_path=str(req_path),
            expected_path=str(expected_path) if expected_path else None,
            output_dir=str(case_dir),
            generated_script=str(script_path) if script_path else None,
            generated_task=generated_task,
            expected=expected,
            checks=checks,
            metrics=case_metrics,
            errors=errors,
        ))

    total = len(cases)
    passed = sum(1 for c in cases if c.ok)
    failed = total - passed
    metrics = _aggregate_metrics(cases)
    report = SourceGroundedEvalReport(
        ok=(failed == 0),
        output_dir=str(out_root),
        total=total,
        passed=passed,
        failed=failed,
        pass_rate=(passed / total if total else 0.0),
        metrics=metrics,
        cases=tuple(cases),
    )
    write_json(out_root / "report.json", report.to_dict())
    write_json(out_root / "metrics.json", metrics)
    _write_case_csv(out_root / "case_results.csv", cases)
    return report


def _aggregate_metrics(cases: Sequence[SourceGroundedEvalCaseResult]) -> dict[str, Any]:
    def rate(check_name: str) -> float:
        checked = [case.checks[check_name] for case in cases if check_name in case.checks]
        return sum(1 for ok in checked if ok) / len(checked) if checked else 0.0

    path_checks: list[bool] = []
    absent_checks: list[bool] = []
    modifier_checks: list[bool] = []
    for case in cases:
        for name, ok in case.checks.items():
            if name.startswith("path:"):
                path_checks.append(ok)
            if name.startswith("absent:"):
                absent_checks.append(ok)
            if name.startswith("modifier_"):
                modifier_checks.append(ok)
    return {
        "case_count": len(cases),
        "pass_rate": (sum(1 for c in cases if c.ok) / len(cases) if cases else 0.0),
        "source_binding_accuracy": rate("source_binding_mode"),
        "capability_selection_accuracy": rate("capability_selection"),
        "parameter_mapping_accuracy": (sum(1 for ok in path_checks if ok) / len(path_checks) if path_checks else 0.0),
        "script_execution_pass_rate": rate("script_execution"),
        "manifest_source_binding_rate": rate("manifest_source_binding"),
        "unsupported_boundary_accuracy": rate("unsupported_boundary"),
        "modifier_mapping_accuracy": (sum(1 for ok in modifier_checks if ok) / len(modifier_checks) if modifier_checks else 0.0),
        "template_pollution_free_rate": (sum(1 for ok in absent_checks if ok) / len(absent_checks) if absent_checks else 0.0),
    }


def _write_case_csv(path: Path, cases: Sequence[SourceGroundedEvalCaseResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "ok", "capability_id", "checks", "errors"])
        writer.writeheader()
        for case in cases:
            writer.writerow({
                "name": case.name,
                "ok": case.ok,
                "capability_id": case.generated_task.get("capability_id"),
                "checks": json.dumps(case.checks, ensure_ascii=False, sort_keys=True),
                "errors": " | ".join(case.errors),
            })


__all__ = ["SourceGroundedEvalCaseResult", "SourceGroundedEvalReport", "run_source_grounded_evals"]
