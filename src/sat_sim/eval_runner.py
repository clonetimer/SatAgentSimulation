"""Offline evaluation harness for TaskSpec-generating Agents."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .agent_orchestrator import generate_task_from_text
from .task_spec import load_mapping, write_json


@dataclass(frozen=True)
class AgentEvalCaseResult:
    case_id: str
    request_path: Path
    ok: bool
    validation_ok: bool
    compile_ok: bool
    run_ok: bool | None
    expected_match: bool | None
    generated_task_path: Path | None
    output_dir: Path
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["request_path"] = str(self.request_path)
        payload["generated_task_path"] = str(self.generated_task_path) if self.generated_task_path else None
        payload["output_dir"] = str(self.output_dir)
        return payload


@dataclass(frozen=True)
class AgentEvalReport:
    ok: bool
    evals_dir: Path
    output_dir: Path
    case_count: int
    validation_pass_rate: float
    compile_pass_rate: float
    run_pass_rate: float | None
    expected_match_rate: float | None
    cases: tuple[AgentEvalCaseResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "evals_dir": str(self.evals_dir),
            "output_dir": str(self.output_dir),
            "case_count": self.case_count,
            "validation_pass_rate": self.validation_pass_rate,
            "compile_pass_rate": self.compile_pass_rate,
            "run_pass_rate": self.run_pass_rate,
            "expected_match_rate": self.expected_match_rate,
            "cases": [case.to_dict() for case in self.cases],
        }


def _expected_path(evals_dir: Path, stem: str) -> Path | None:
    root = evals_dir / "expected"
    for suffix in (".yaml", ".yml", ".json"):
        p = root / f"{stem}{suffix}"
        if p.exists():
            return p
    return None


def _semantic_value(data: Mapping[str, Any], path: str) -> Any:
    """Read a stable semantic field from legacy or CanonicalTaskSpec v1."""

    direct = _get_dotted(data, path)
    if direct is not None:
        return direct
    canonical_paths = {
        "task_type": "simulation.level",
        "capability_id": "model.capability_id",
        "target.level": "model.target.level",
        "target.name": "model.target.name",
        "target.mode": "model.target.mode",
    }
    mapped = canonical_paths.get(path)
    if mapped is not None:
        value = _get_dotted(data, mapped)
        if value is not None:
            return value
    if path == "target.level":
        return _get_dotted(data, "simulation.level")
    return None


def _compare_expected(generated: Mapping[str, Any], expected: Mapping[str, Any]) -> tuple[bool, dict[str, Any]]:
    """Compare stable semantic fields rather than exact YAML text."""

    checks: dict[str, Any] = {}
    for path in ("capability_id", "task_type", "target.level", "target.name", "target.mode"):
        g = _semantic_value(generated, path)
        e = _semantic_value(expected, path)
        if e is not None:
            checks[path] = {"expected": e, "generated": g, "ok": g == e}
    ok = all(item["ok"] for item in checks.values()) if checks else True
    return ok, checks


def _get_dotted(data: Mapping[str, Any], path: str) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return None
        cur = cur[part]
    return cur


def run_agent_evals(
    evals_dir: str | Path = "evals",
    *,
    output_dir: str | Path = "reports/agent_eval_p4",
    examples_dir: str | Path = "examples",
    backend: str = "template",
    run: bool = False,
) -> AgentEvalReport:
    """Run offline request fixtures through the Agent generation pipeline."""

    eval_root = Path(evals_dir)
    request_root = eval_root / "requests"
    out_root = Path(output_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    request_paths = sorted(list(request_root.glob("*.md")) + list(request_root.glob("*.txt")))
    cases: list[AgentEvalCaseResult] = []
    for path in request_paths:
        case_id = path.stem
        case_out = out_root / "cases" / case_id
        text = path.read_text(encoding="utf-8").strip()
        try:
            result = generate_task_from_text(text, output_dir=case_out, examples_dir=examples_dir, backend=backend, auto_run=run, dry_run=not run)
            generated_path = case_out / "task_spec.yaml"
            if not generated_path.exists():
                generated_path = case_out / "generated_task.yaml"
            expected_match: bool | None = None
            details: dict[str, Any] = {"steps": [s.to_dict() for s in result.steps]}
            exp_path = _expected_path(eval_root, case_id)
            if exp_path is not None:
                expected = load_mapping(exp_path)
                expected_match, checks = _compare_expected(result.task_spec, expected)
                details["expected_checks"] = checks
            compile_ok = result.compiled is not None
            run_ok = result.run_result is not None if run else None
            ok = result.validation.ok and compile_ok and (run_ok is not False) and (expected_match is not False)
            cases.append(
                AgentEvalCaseResult(
                    case_id=case_id,
                    request_path=path,
                    ok=ok,
                    validation_ok=result.validation.ok,
                    compile_ok=compile_ok,
                    run_ok=run_ok,
                    expected_match=expected_match,
                    generated_task_path=generated_path if generated_path.exists() else None,
                    output_dir=case_out,
                    details=details,
                )
            )
        except Exception as exc:
            cases.append(
                AgentEvalCaseResult(
                    case_id=case_id,
                    request_path=path,
                    ok=False,
                    validation_ok=False,
                    compile_ok=False,
                    run_ok=False if run else None,
                    expected_match=None,
                    generated_task_path=None,
                    output_dir=case_out,
                    details={"error": str(exc)},
                )
            )
    def rate(values: list[bool]) -> float:
        return sum(1 for v in values if v) / len(values) if values else 0.0

    run_values = [c.run_ok for c in cases if c.run_ok is not None]
    expected_values = [c.expected_match for c in cases if c.expected_match is not None]
    report = AgentEvalReport(
        ok=all(c.ok for c in cases) if cases else False,
        evals_dir=eval_root,
        output_dir=out_root,
        case_count=len(cases),
        validation_pass_rate=rate([c.validation_ok for c in cases]),
        compile_pass_rate=rate([c.compile_ok for c in cases]),
        run_pass_rate=rate([bool(v) for v in run_values]) if run_values else None,
        expected_match_rate=rate([bool(v) for v in expected_values]) if expected_values else None,
        cases=tuple(cases),
    )
    write_json(out_root / "agent_eval_report.json", report.to_dict())
    return report


__all__ = ["AgentEvalCaseResult", "AgentEvalReport", "run_agent_evals"]
