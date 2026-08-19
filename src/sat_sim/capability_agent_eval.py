"""Evaluation harness for capability-aware LLM TaskSpec generation.

A2 evaluates the complete local trust pipeline around a real or mocked model:

    request text -> model draft -> validate/repair -> compile -> script export

A7-A extends the harness from a pure "all cases must generate scripts" smoke test
into a capability-Agent benchmark.  Expected fixtures may now declare planner
expectations, unsupported-domain boundary behavior, and stable TaskSpec parameter
checks.  This keeps the trusted local pipeline measurable as new capabilities are
added.
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic

import csv
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .capability_agent import DEFAULT_ALLOWED_CAPABILITIES, generate_capability_script_from_text
from .task_spec import load_mapping, write_json


@dataclass(frozen=True)
class CapabilityAgentEvalCaseResult:
    name: str
    ok: bool
    request_path: str
    expected_path: str | None
    output_dir: str
    generated_script: str | None
    generated_task: dict[str, Any]
    expected: dict[str, Any]
    checks: dict[str, bool]
    category: str = "uncategorized"
    errors: list[str] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CapabilityAgentEvalReport:
    ok: bool
    backend: str
    output_dir: str
    total: int
    passed: int
    failed: int
    pass_rate: float
    cases: tuple[CapabilityAgentEvalCaseResult, ...]
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "backend": self.backend,
            "output_dir": self.output_dir,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": self.pass_rate,
            "metrics": dict(self.metrics),
            "cases": [case.to_dict() for case in self.cases],
        }


def _load_expected(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        return load_mapping(path)
    except Exception:
        text = path.read_text(encoding="utf-8")
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def _get_nested(data: Mapping[str, Any], dotted: str) -> Any:
    cur: Any = data
    for part in dotted.split("."):
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(part)
    return cur


def _coerce_expected_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _expected_agent_ok(expected: Mapping[str, Any]) -> bool | None:
    if "expected_agent_ok" in expected:
        return bool(expected["expected_agent_ok"])
    agent = expected.get("agent")
    if isinstance(agent, Mapping) and "ok" in agent:
        return bool(agent["ok"])
    return None


def _values_equal(generated: Any, expected: Any) -> bool:
    """Compare scalar expected values with numeric tolerance when appropriate."""

    if isinstance(expected, (int, float)) and isinstance(generated, (int, float)):
        return abs(float(generated) - float(expected)) <= max(1e-9, abs(float(expected)) * 1e-9)
    return generated == expected


def _compare_expected(generated: Mapping[str, Any], expected: Mapping[str, Any]) -> tuple[dict[str, bool], list[str]]:
    checks: dict[str, bool] = {}
    errors: list[str] = []

    # Legacy stable semantic checks used by A2/A3/A4 eval fixtures.
    for path in ("capability_id", "task_type", "target.level", "target.name", "target.mode"):
        expected_value = _get_nested(expected, path)
        if expected_value is None:
            continue
        generated_value = _get_nested(generated, path)
        passed = _values_equal(generated_value, expected_value)
        checks[path] = passed
        if not passed:
            errors.append(f"{path}: expected {expected_value!r}, got {generated_value!r}")

    # A7-A parameter/field assertions.  These are intentionally dotted paths so
    # evals can assert that NL numbers actually bind to TaskSpec fields.
    expected_paths = _coerce_expected_mapping(expected.get("expect_paths") or expected.get("expected_paths"))
    for path, expected_value in expected_paths.items():
        generated_value = _get_nested(generated, str(path))
        passed = _values_equal(generated_value, expected_value)
        check_name = f"path:{path}"
        checks[check_name] = passed
        if not passed:
            errors.append(f"{path}: expected {expected_value!r}, got {generated_value!r}")

    return checks, errors


def _planner_from_result(result: Any, generated_task: Mapping[str, Any]) -> dict[str, Any]:
    metadata = generated_task.get("metadata")
    if isinstance(metadata, Mapping):
        planner = metadata.get("planner")
        if isinstance(planner, Mapping):
            return dict(planner)
    files = dict(getattr(result, "files", {}) or {})
    plan_path = files.get("capability_plan")
    if plan_path:
        try:
            data = json.loads(Path(plan_path).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def _compare_planner(planner: Mapping[str, Any], expected: Mapping[str, Any]) -> tuple[dict[str, bool], list[str]]:
    checks: dict[str, bool] = {}
    errors: list[str] = []
    spec = _coerce_expected_mapping(expected.get("planner") or expected.get("expected_planner"))
    if not spec:
        return checks, errors

    for key in ("supported", "selected_capability_id", "selected_task_type", "recommended_action"):
        if key not in spec:
            continue
        expected_value = spec[key]
        actual_value = planner.get(key)
        passed = _values_equal(actual_value, expected_value)
        check_name = f"planner.{key}"
        checks[check_name] = passed
        if not passed:
            errors.append(f"planner.{key}: expected {expected_value!r}, got {actual_value!r}")

    expected_domains = spec.get("unsupported_domains") or spec.get("unsupported_requirements")
    if expected_domains is not None:
        actual_items = planner.get("unsupported_requirements") or []
        actual_domains = []
        if isinstance(actual_items, list):
            for item in actual_items:
                if isinstance(item, Mapping) and item.get("domain") is not None:
                    actual_domains.append(str(item["domain"]))
                elif isinstance(item, str):
                    actual_domains.append(item)
        expected_set = {str(item) for item in expected_domains}
        actual_set = set(actual_domains)
        passed = expected_set.issubset(actual_set)
        checks["planner.unsupported_domains"] = passed
        if not passed:
            errors.append(f"planner.unsupported_domains: expected at least {sorted(expected_set)!r}, got {sorted(actual_set)!r}")

    return checks, errors


def _agent_failure_diagnostics(result: Any) -> dict[str, Any]:
    """Return compact diagnostics for a failed Agent pipeline."""

    diagnostics: dict[str, Any] = {
        "agent_ok": bool(getattr(result, "ok", False)),
        "files": dict(getattr(result, "files", {}) or {}),
    }
    validation = getattr(result, "validation", None)
    if validation is not None:
        try:
            diagnostics["validation"] = validation.to_dict()
        except Exception:
            diagnostics["validation"] = str(validation)
    steps = []
    for step in getattr(result, "steps", ()) or ():
        try:
            payload = step.to_dict()
        except Exception:
            payload = {"step": str(step)}
        steps.append(payload)
    diagnostics["steps"] = steps
    failed_steps = [s for s in steps if s.get("status") in {"failed", "error", "partial", "boundary"}]
    if failed_steps:
        diagnostics["failed_steps"] = failed_steps
    script = getattr(result, "script", None)
    if script is not None:
        try:
            diagnostics["script_export"] = script.to_dict()
        except Exception:
            diagnostics["script_export"] = str(script)
    return diagnostics


def _summarize_agent_failure(result: Any) -> str:
    """Return one human-readable failure string for case_results.csv/errors."""

    validation = getattr(result, "validation", None)
    if validation is not None:
        try:
            payload = validation.to_dict()
            errors = payload.get("errors") or []
            if errors:
                first = errors[0]
                return f"validation failed: {first.get('path')}: {first.get('message')}"
        except Exception as exc:
            record_runtime_diagnostic(
                code='EVALUATION_FAILURE_SUMMARY_FALLBACK',
                category=DiagnosticCategory.EVALUATION_FORMAT_FALLBACK,
                location='src/sat_sim/capability_agent_eval.py:_summarize_agent_failure:01',
                exception=exc,
                strict=False,
            )
    steps = []
    for step in getattr(result, "steps", ()) or ():
        try:
            step_dict = step.to_dict()
        except Exception:
            continue
        if step_dict.get("status") in {"failed", "error", "partial", "boundary"}:
            steps.append(step_dict)
    if steps:
        last = steps[-1]
        return f"{last.get('name')} {last.get('status')}: {last.get('message')}"
    return "pipeline did not complete successfully"


def _case_name(path: Path) -> str:
    return path.stem.replace(" ", "_")


def discover_eval_cases(evals_dir: str | Path) -> list[tuple[Path, Path | None]]:
    root = Path(evals_dir)
    request_dir = root / "requests"
    expected_dir = root / "expected"
    out: list[tuple[Path, Path | None]] = []
    for req in sorted(request_dir.glob("*.md")) + sorted(request_dir.glob("*.txt")):
        exp = expected_dir / f"{req.stem}.yaml"
        if not exp.exists():
            exp = expected_dir / f"{req.stem}.json"
        out.append((req, exp if exp.exists() else None))
    return out


def _rate(values: list[bool]) -> float | None:
    if not values:
        return None
    return sum(1 for v in values if v) / len(values)


def _compute_metrics(results: Sequence[CapabilityAgentEvalCaseResult]) -> dict[str, Any]:
    def values_for(names: set[str]) -> list[bool]:
        return [bool(value) for case in results for name, value in case.checks.items() if name in names]

    def values_with_prefix(prefix: str) -> list[bool]:
        return [bool(value) for case in results for name, value in case.checks.items() if name.startswith(prefix)]

    categories: dict[str, int] = {}
    for case in results:
        categories[case.category] = categories.get(case.category, 0) + 1

    expected_supported_cases = [case for case in results if _expected_agent_ok(case.expected) is not False]
    expected_unsupported_cases = [case for case in results if _expected_agent_ok(case.expected) is False]
    exported_values = [case.generated_script is not None for case in expected_supported_cases]
    script_run_values = values_for({"script_run_ok"})

    return {
        "capability_selection_accuracy": _rate(values_for({"capability_id"})),
        "task_type_accuracy": _rate(values_for({"task_type"})),
        "parameter_expectation_accuracy": _rate(values_with_prefix("path:")),
        "planner_route_accuracy": _rate(values_with_prefix("planner.")),
        "unsupported_handling_rate": _rate([case.ok for case in expected_unsupported_cases]),
        "script_export_rate": _rate(exported_values),
        "script_execution_pass_rate": _rate(script_run_values),
        "case_count_by_category": categories,
        "expected_supported_case_count": len(expected_supported_cases),
        "expected_unsupported_case_count": len(expected_unsupported_cases),
    }


def run_capability_agent_evals(
    evals_dir: str | Path,
    *,
    output_dir: str | Path = "reports/capability_agent_eval_a2",
    examples_dir: str | Path = "examples",
    backend: str = "template",
    model_command: str | None = None,
    model_name: str | None = None,
    model_base_url: str | None = None,
    model_api_key_env: str = "OPENAI_API_KEY",
    model_timeout_s: float = 60.0,
    model_temperature: float | None = None,
    model_max_output_tokens: int | None = None,
    model_structured_output: str = "text",
    allowed_capabilities: Sequence[str] = DEFAULT_ALLOWED_CAPABILITIES,
    run_scripts: bool = False,
    script_timeout_s: float = 120.0,
) -> CapabilityAgentEvalReport:
    """Run capability Agent eval cases and write a report."""

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    results: list[CapabilityAgentEvalCaseResult] = []
    for req_path, expected_path in discover_eval_cases(evals_dir):
        name = _case_name(req_path)
        case_output = output_root / name
        request_text = req_path.read_text(encoding="utf-8")
        expected = _load_expected(expected_path)
        expected_ok = _expected_agent_ok(expected)
        category = str(expected.get("category") or "legacy")
        errors: list[str] = []
        generated_script: str | None = None
        generated_task: dict[str, Any] = {}
        checks: dict[str, bool] = {}
        diagnostics: dict[str, Any] = {}
        ok = False
        try:
            result = generate_capability_script_from_text(
                request_text,
                output_dir=case_output,
                examples_dir=examples_dir,
                backend=backend,
                allowed_capabilities=tuple(allowed_capabilities),
                script_output=case_output / f"{name}.py",
                output_root=case_output / "dataset",
                model_command=model_command,
                model_name=model_name,
                model_base_url=model_base_url,
                model_api_key_env=model_api_key_env,
                model_timeout_s=model_timeout_s,
                model_temperature=model_temperature,
                model_max_output_tokens=model_max_output_tokens,
                model_structured_output=model_structured_output,
            )
            generated_task = dict(result.task_spec)
            generated_script = result.files.get("generated_script")
            diagnostics = _agent_failure_diagnostics(result)
            planner = _planner_from_result(result, generated_task)
            if planner:
                diagnostics["planner"] = planner

            if expected_ok is None:
                checks["pipeline_ok"] = bool(result.ok)
                if not result.ok:
                    errors.append(_summarize_agent_failure(result))
            else:
                checks["agent_ok_expected"] = bool(result.ok) == expected_ok
                if expected_ok:
                    checks["pipeline_ok"] = bool(result.ok)
                    if not result.ok:
                        errors.append(_summarize_agent_failure(result))
                else:
                    checks["unsupported_blocked_before_script"] = result.script is None and result.compiled is None
                    if result.ok:
                        errors.append("expected planner boundary failure, but Agent pipeline succeeded")

            expected_checks, expected_errors = _compare_expected(generated_task, expected)
            checks.update(expected_checks)
            errors.extend(expected_errors)
            planner_checks, planner_errors = _compare_planner(planner, expected)
            checks.update(planner_checks)
            errors.extend(planner_errors)

            if run_scripts and generated_script and result.ok:
                try:
                    proc = subprocess.run(
                        [sys.executable, generated_script],
                        cwd=Path.cwd(),
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=False,
                        timeout=script_timeout_s,
                    )
                    diagnostics.setdefault("script_run", {})
                    diagnostics["script_run"].update({
                        "returncode": proc.returncode,
                        "stdout_tail": proc.stdout[-1000:],
                        "stderr_tail": proc.stderr[-1000:],
                        "timeout_s": script_timeout_s,
                    })
                    checks["script_run_ok"] = proc.returncode == 0
                    if proc.returncode != 0:
                        errors.append(f"generated script exited with {proc.returncode}: {proc.stderr[-1000:]}")
                except subprocess.TimeoutExpired as exc:
                    checks["script_run_ok"] = False
                    diagnostics.setdefault("script_run", {})
                    diagnostics["script_run"].update({
                        "timeout": True,
                        "timeout_s": script_timeout_s,
                        "stdout_tail": (exc.stdout or "")[-1000:] if isinstance(exc.stdout, str) else "",
                        "stderr_tail": (exc.stderr or "")[-1000:] if isinstance(exc.stderr, str) else "",
                    })
                    errors.append(f"generated script timed out after {script_timeout_s}s")
            ok = all(checks.values()) if checks else bool(result.ok)
        except Exception as exc:
            errors.append(str(exc))
            checks["exception_free"] = False
            diagnostics = {"exception": repr(exc)}
        results.append(
            CapabilityAgentEvalCaseResult(
                name=name,
                ok=ok,
                request_path=str(req_path),
                expected_path=str(expected_path) if expected_path else None,
                output_dir=str(case_output),
                generated_script=generated_script,
                generated_task=generated_task,
                expected=expected,
                checks=checks,
                category=category,
                errors=errors,
                diagnostics=diagnostics,
            )
        )
    total = len(results)
    passed = sum(1 for case in results if case.ok)
    failed = total - passed
    metrics = _compute_metrics(results)
    report = CapabilityAgentEvalReport(
        ok=failed == 0,
        backend=backend,
        output_dir=str(output_root),
        total=total,
        passed=passed,
        failed=failed,
        pass_rate=(passed / total if total else 0.0),
        cases=tuple(results),
        metrics=metrics,
    )
    report_payload = report.to_dict()
    write_json(output_root / "capability_agent_eval_report.json", report_payload)
    write_json(output_root / "metrics.json", metrics)
    with (output_root / "case_results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["name", "category", "ok", "capability_id", "task_type", "errors"])
        writer.writeheader()
        for case in results:
            writer.writerow({
                "name": case.name,
                "category": case.category,
                "ok": case.ok,
                "capability_id": case.generated_task.get("capability_id"),
                "task_type": case.generated_task.get("task_type"),
                "errors": "; ".join(case.errors),
            })
    return report


__all__ = [
    "CapabilityAgentEvalCaseResult",
    "CapabilityAgentEvalReport",
    "discover_eval_cases",
    "run_capability_agent_evals",
]
