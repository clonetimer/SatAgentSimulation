"""Agent orchestration layer for TaskSpec generation.

The orchestrator keeps the LLM/framework boundary outside the simulation core:
Agents generate or modify a TaskSpec draft, while this module performs the
trusted steps: normalize, validate, repair, compile, optionally run, and write a
reproducible agent artifact bundle.

P4 deliberately provides a deterministic template backend so CI, local smoke
checks, and future UI backends can run without network access or a model SDK.
Production deployments can replace the backend with OpenAI Agents SDK,
LangGraph, AutoGen, or another framework by implementing ``AgentBackend``.
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic

import copy
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from .agent import agent_context_payload, repair_hints_from_issues
from .task_compiler import CompiledTask, compile_task_spec
from .task_runner import TaskRunResult
from .task_spec import TASK_SPEC_VERSION, spec_sha256, write_json
from .task_validator import ValidationResult, validate_task_spec

try:  # pragma: no cover - dependency availability is environment-specific
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # type: ignore


DEFAULT_GENERATED_DIR = "generated_tasks"
P4_GENERATOR_VERSION = "p4.agent_orchestrator.v0"


@dataclass(frozen=True)
class AgentGenerationRequest:
    """User request plus execution options for the generation pipeline."""

    request: str
    output_dir: str | Path = DEFAULT_GENERATED_DIR
    examples_dir: str | Path = "examples"
    backend: str = "template"
    auto_run: bool = False
    max_repair_attempts: int = 2
    output_root: str | Path | None = None
    task_id: str | None = None
    dry_run: bool = False
    extra_context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentStep:
    """One high-level step in the Agent orchestration trace."""

    name: str
    status: str
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AgentSessionResult:
    """Complete result of a TaskSpec generation session."""

    ok: bool
    request: AgentGenerationRequest
    task_spec: dict[str, Any]
    validation: ValidationResult
    compiled: CompiledTask | None = None
    run_result: TaskRunResult | None = None
    output_dir: Path | None = None
    files: dict[str, str] = field(default_factory=dict)
    steps: tuple[AgentStep, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "agent_facade_version": "v20.agent-facade.v1",
            "task_spec_version": self.task_spec.get("schema_version"),
            "request": {
                **asdict(self.request),
                "output_dir": str(self.request.output_dir),
                "examples_dir": str(self.request.examples_dir),
                "output_root": str(self.request.output_root) if self.request.output_root is not None else None,
            },
            "task_spec": self.task_spec,
            "validation": self.validation.to_dict(),
            "compiled": self.compiled.to_dict() if self.compiled else None,
            "run_result": self.run_result.to_dict() if self.run_result else None,
            "output_dir": str(self.output_dir) if self.output_dir else None,
            "files": dict(self.files),
            "steps": [step.to_dict() for step in self.steps],
        }


class AgentBackend(Protocol):
    """Protocol for pluggable LLM/framework backends."""

    name: str

    def draft_task_spec(self, *, request: AgentGenerationRequest, context: Mapping[str, Any]) -> dict[str, Any]:
        """Return an initial TaskSpec draft as a mapping."""


@dataclass(frozen=True)
class TemplateSelection:
    """Template chosen by a backend."""

    template_file: str
    reason: str


def _stable_short_hash(text: str, n: int = 10) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:n]


def _slugify(text: str, *, default: str = "generated_task", max_len: int = 72) -> str:
    # Keep ASCII task_id compatibility and make Chinese/Japanese requests stable
    # by appending a short hash when non-ASCII content is present.
    ascii_text = text.encode("ascii", "ignore").decode("ascii")
    ascii_text = re.sub(r"[^A-Za-z0-9_.-]+", "_", ascii_text).strip("_.-")
    if not ascii_text:
        ascii_text = default
    ascii_text = re.sub(r"_+", "_", ascii_text)[:max_len].strip("_.-") or default
    return ascii_text


def _safe_load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:  # pragma: no cover
        raise RuntimeError("PyYAML is required to load YAML templates")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"template {path} root must be an object")
    return data


def _safe_dump_yaml(data: Mapping[str, Any]) -> str:
    if yaml is None:  # pragma: no cover
        return json.dumps(data, indent=2, ensure_ascii=False)
    return yaml.safe_dump(dict(data), sort_keys=False, allow_unicode=True)


def _write_yaml(path: Path, data: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_safe_dump_yaml(data), encoding="utf-8")
    return path


def _examples_by_file(examples_dir: str | Path) -> dict[str, Path]:
    root = Path(examples_dir)
    out: dict[str, Path] = {}
    if not root.exists():
        return out
    for path in sorted(list(root.glob("*.yaml")) + list(root.glob("*.yml")) + list(root.glob("*.json"))):
        out[path.name] = path
        out[path.stem] = path
    return out


def _contains_any(text: str, words: Sequence[str]) -> bool:
    lower = text.lower()
    return any(word.lower() in lower for word in words)


def _extract_seconds(text: str, keywords: Sequence[str]) -> float | None:
    """Extract a seconds value near any keyword.

    Supports common Chinese and English forms such as "仿真 3600 秒", "duration
    1 h", "持续 30 min". The parser is conservative; templates provide defaults
    if no unambiguous value is found.
    """

    lower = text.lower()
    unit_map = {
        "s": 1.0,
        "sec": 1.0,
        "second": 1.0,
        "seconds": 1.0,
        "秒": 1.0,
        "m": 60.0,
        "min": 60.0,
        "minute": 60.0,
        "minutes": 60.0,
        "分钟": 60.0,
        "h": 3600.0,
        "hr": 3600.0,
        "hour": 3600.0,
        "hours": 3600.0,
        "小时": 3600.0,
    }
    keyword_pattern = "|".join(re.escape(k.lower()) for k in keywords)
    number_unit = r"([0-9]+(?:\.[0-9]+)?)\s*(秒|分钟|小时|seconds?|secs?|s|min(?:ute)?s?|m|hours?|hrs?|h)"
    clause_chars = r"[^\n。；;,，]"
    patterns = [
        rf"(?:{keyword_pattern}){clause_chars}{{0,20}}?{number_unit}",
        rf"{number_unit}[^\n。；;,]{{0,20}}(?:{keyword_pattern})",
    ]
    for pattern in patterns:
        m = re.search(pattern, lower, re.IGNORECASE)
        if m:
            # group order differs depending on which side number_unit appears.
            groups = [g for g in m.groups() if g]
            for i, group in enumerate(groups[:-1]):
                try:
                    value = float(group)
                    unit = str(groups[i + 1]).lower()
                    if unit in unit_map:
                        return value * unit_map[unit]
                except Exception:
                    continue
    return None


def _extract_first_float(text: str, keywords: Sequence[str], *, min_value: float | None = None, max_value: float | None = None) -> float | None:
    lower = text.lower()
    keyword_pattern = "|".join(re.escape(k.lower()) for k in keywords)
    patterns = [
        rf"(?:{keyword_pattern})[^0-9\-]{{0,20}}([0-9]+(?:\.[0-9]+)?)",
        rf"([0-9]+(?:\.[0-9]+)?)\s*[^\n。；;,]{{0,20}}(?:{keyword_pattern})",
    ]
    for pattern in patterns:
        m = re.search(pattern, lower, re.IGNORECASE)
        if m:
            value = float(m.group(1))
            if min_value is not None and value < min_value:
                continue
            if max_value is not None and value > max_value:
                continue
            return value
    return None


def _set_path(data: dict[str, Any], path: str, value: Any) -> None:
    from .campaign import set_by_path

    set_by_path(data, path, value)


class TemplateAgentBackend:
    """Deterministic request-to-template backend for local development and CI.

    It is not a replacement for an LLM.  Its purpose is to provide a stable
    baseline, test harness, and fallback path for common satellite simulation
    requests. A production Agent can use the same orchestrator with an LLM
    backend and still pass through the same validation/compile/run gates.
    """

    name = "template"

    def select_template(self, request: str, context: Mapping[str, Any]) -> TemplateSelection:
        text = request.lower()
        is_campaign = _contains_any(text, ["campaign", "批量", "扫描", "sweep", "grid", "随机", "random", "蒙特卡洛", "monte carlo"])
        is_fault = _contains_any(text, ["fault", "故障", "开路", "open", "失效", "failure", "异常"])
        is_degradation = _contains_any(text, ["degradation", "退化", "老化", "degrade", "loss", "衰减"])
        mentions_battery = _contains_any(text, ["battery", "电池", "soc"])
        mentions_rw = _contains_any(text, ["reaction wheel", "反作用轮", "rw", "动量轮"])
        mentions_adcs = _contains_any(text, ["adcs", "姿态", "detumble", "消旋", "控制"])
        mentions_orbit = _contains_any(text, ["orbit", "轨道", "environment", "环境", "leo", "太阳", "磁场"])
        mentions_whole = _contains_any(text, ["whole", "spacecraft", "整星", "全星", "卫星级"])

        if is_campaign:
            if mentions_battery or is_fault:
                return TemplateSelection("campaign_component_battery_fault_sweep.yaml", "campaign + battery/fault keywords")
            if mentions_orbit:
                if _contains_any(text, ["random", "随机", "lhs"]):
                    return TemplateSelection("campaign_orbit_environment_random.yaml", "campaign + orbit + random keywords")
                return TemplateSelection("campaign_orbit_environment_grid.yaml", "campaign + orbit keywords")
            return TemplateSelection("campaign_component_battery_fault_sweep.yaml", "generic campaign fallback")
        if mentions_whole:
            if is_fault or mentions_battery:
                return TemplateSelection("whole_spacecraft_battery_fault.yaml", "whole-spacecraft + fault/battery keywords")
            if is_degradation:
                return TemplateSelection("whole_spacecraft_degradation.yaml", "whole-spacecraft + degradation keywords")
            return TemplateSelection("whole_spacecraft_nominal.yaml", "whole-spacecraft nominal keywords")
        if mentions_orbit:
            return TemplateSelection("orbit_environment_nominal.yaml", "orbit/environment keywords")
        if mentions_adcs:
            if _contains_any(text, ["detumble", "消旋", "magnetic", "磁"]):
                return TemplateSelection("subsystem_adcs_magnetic_detumble_nominal.yaml", "ADCS magnetic detumble keywords")
            return TemplateSelection("subsystem_adcs_control_nominal.yaml", "ADCS control keywords")
        if mentions_rw or is_degradation:
            return TemplateSelection("component_reaction_wheel_degradation.yaml", "reaction-wheel/degradation keywords")
        if mentions_battery or is_fault:
            if is_fault or _contains_any(text, ["开路", "open_circuit", "open circuit"]):
                return TemplateSelection("component_battery_fault_open_circuit.yaml", "battery fault keywords")
            return TemplateSelection("component_battery_nominal.yaml", "battery nominal keywords")
        return TemplateSelection("component_battery_nominal.yaml", "default fallback")

    def draft_task_spec(self, *, request: AgentGenerationRequest, context: Mapping[str, Any]) -> dict[str, Any]:
        selection = self.select_template(request.request, context)
        examples = _examples_by_file(request.examples_dir)
        path = examples.get(selection.template_file) or examples.get(Path(selection.template_file).stem)
        if path is None:
            raise FileNotFoundError(f"example template not found: {selection.template_file}")
        spec = _safe_load_yaml(path)
        spec = copy.deepcopy(spec)
        _apply_request_overrides(spec, request, base_template=selection.template_file, selection_reason=selection.reason)
        return spec


def _apply_request_overrides(spec: dict[str, Any], request: AgentGenerationRequest, *, base_template: str, selection_reason: str) -> None:
    text = request.request
    req_hash = _stable_short_hash(text)
    base_id = str(spec.get("task_id") or Path(base_template).stem)
    task_id = request.task_id or f"gen_{_slugify(base_id, max_len=52)}_{req_hash}"
    spec["schema_version"] = TASK_SPEC_VERSION
    spec["task_id"] = task_id
    spec.setdefault("tags", [])
    if isinstance(spec["tags"], list) and "agent_generated" not in spec["tags"]:
        spec["tags"].append("agent_generated")
    spec["description"] = spec.get("description") or "Agent-generated satellite simulation TaskSpec."
    spec.setdefault("metadata", {})
    if isinstance(spec["metadata"], dict):
        spec["metadata"].setdefault("agent", {})
        spec["metadata"]["agent"].update(
            {
                "generated_by": P4_GENERATOR_VERSION,
                "backend": request.backend,
                "request_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "request_excerpt": text[:240],
                "base_template": base_template,
                "selection_reason": selection_reason,
            }
        )

    duration_s = _extract_seconds(text, ["duration", "simulate", "simulation", "仿真", "持续", "时长", "运行"])
    sample_s = _extract_seconds(text, ["sample", "sampling", "采样", "输出间隔", "步长"])
    onset_s = _extract_seconds(text, ["onset", "fault at", "故障在", "发生", "注入", "开始"])
    magnitude = _extract_first_float(text, ["magnitude", "severity", "幅值", "严重度", "强度"], min_value=0.0, max_value=1.0)
    initial_soc = _extract_first_float(text, ["initial soc", "soc", "初始电量", "荷电状态"], min_value=0.0, max_value=1.0)

    if duration_s is not None:
        _set_path(spec, "simulation.duration_s", duration_s)
        # Also update common nested campaign base specs so expanded cases follow
        # the user request.
        if spec.get("task_type") == "campaign":
            _set_path(spec, "campaign.base_spec.simulation.duration_s", duration_s)
    if sample_s is not None:
        _set_path(spec, "simulation.sample_s", sample_s)
        if spec.get("task_type") == "campaign":
            _set_path(spec, "campaign.base_spec.simulation.sample_s", sample_s)
    if onset_s is not None:
        try:
            _set_path(spec, "faults[0].onset_time_s", onset_s)
        except Exception as exc:
            record_runtime_diagnostic(
                code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                location='src/sat_sim/agent_orchestrator.py:_apply_request_overrides:01',
                exception=exc,
                strict=False,
            )
        if spec.get("task_type") == "campaign":
            try:
                _set_path(spec, "campaign.base_spec.faults[0].onset_time_s", onset_s)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/agent_orchestrator.py:_apply_request_overrides:05',
                    exception=exc,
                    strict=False,
                )
    if magnitude is not None:
        try:
            _set_path(spec, "faults[0].magnitude", magnitude)
        except Exception as exc:
            record_runtime_diagnostic(
                code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                location='src/sat_sim/agent_orchestrator.py:_apply_request_overrides:02',
                exception=exc,
                strict=False,
            )
        if spec.get("task_type") == "campaign":
            try:
                _set_path(spec, "campaign.base_spec.faults[0].magnitude", magnitude)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/agent_orchestrator.py:_apply_request_overrides:06',
                    exception=exc,
                    strict=False,
                )
    if initial_soc is not None:
        try:
            _set_path(spec, "spacecraft.eps.initial_soc", initial_soc)
        except Exception as exc:
            record_runtime_diagnostic(
                code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                location='src/sat_sim/agent_orchestrator.py:_apply_request_overrides:03',
                exception=exc,
                strict=False,
            )
        if spec.get("task_type") == "campaign":
            try:
                _set_path(spec, "campaign.base_spec.spacecraft.eps.initial_soc", initial_soc)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/agent_orchestrator.py:_apply_request_overrides:07',
                    exception=exc,
                    strict=False,
                )

    output_root = request.output_root or Path("datasets") / task_id
    spec.setdefault("outputs", {})
    if isinstance(spec["outputs"], dict):
        spec["outputs"]["output_root"] = str(output_root)
        spec["outputs"].setdefault("trace_format", "csv")
        spec["outputs"].setdefault("include_summary", True)
        spec["outputs"].setdefault("include_trace", True)
        spec["outputs"].setdefault("include_labels", True)
        spec["outputs"].setdefault("include_manifest", True)
    if spec.get("task_type") == "campaign":
        try:
            _set_path(spec, "campaign.base_spec.outputs.output_root", "datasets/_campaign_child")
        except Exception as exc:
            record_runtime_diagnostic(
                code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                location='src/sat_sim/agent_orchestrator.py:_apply_request_overrides:04',
                exception=exc,
                strict=False,
            )


def _repair_spec_once(spec: Mapping[str, Any], validation: ValidationResult) -> dict[str, Any]:
    """Conservative deterministic repairs for common Agent drafting errors."""

    out = copy.deepcopy(dict(spec))
    out.setdefault("schema_version", TASK_SPEC_VERSION)
    out.setdefault("task_id", f"gen_task_{_stable_short_hash(spec_sha256(out))}")
    out.setdefault("simulation", {"duration_s": 600.0, "sample_s": 10.0, "seed": 0, "backend": "python"})
    if isinstance(out.get("simulation"), dict):
        sim = out["simulation"]
        sim.setdefault("duration_s", 600.0)
        sim.setdefault("sample_s", 10.0)
        sim.setdefault("seed", 0)
        sim.setdefault("backend", "python")
        try:
            if float(sim.get("duration_s", 0)) <= 0:
                sim["duration_s"] = 600.0
            if float(sim.get("sample_s", 0)) <= 0:
                sim["sample_s"] = 10.0
            if float(sim["sample_s"]) > float(sim["duration_s"]):
                sim["sample_s"] = float(sim["duration_s"])
        except Exception:
            sim["duration_s"] = 600.0
            sim["sample_s"] = 10.0
    out.setdefault("outputs", {})
    if isinstance(out.get("outputs"), dict):
        outputs = out["outputs"]
        outputs.setdefault("output_root", str(Path("datasets") / str(out.get("task_id", "generated"))))
        outputs.setdefault("trace_format", "csv")
        outputs.setdefault("include_summary", True)
        outputs.setdefault("include_trace", True)
        outputs.setdefault("include_labels", True)
        outputs.setdefault("include_manifest", True)
        if outputs.get("trace_format") not in {"csv", "parquet", "jsonl"}:
            outputs["trace_format"] = "csv"

    task_type = out.get("task_type")
    target = out.setdefault("target", {}) if task_type in {"component", "subsystem"} else out.get("target", {})
    if isinstance(target, dict):
        if task_type in {"component", "subsystem"}:
            target["level"] = task_type
            target.setdefault("name", "battery" if task_type == "component" else "adcs_control")
        faults = out.get("faults") if isinstance(out.get("faults"), list) else []
        degradations = out.get("degradations") if isinstance(out.get("degradations"), dict) else {}
        if faults:
            target["mode"] = "fault"
        elif degradations and any(v not in ({}, [], None) for v in degradations.values()):
            target["mode"] = "degradation"
        else:
            target.setdefault("mode", "nominal")

    faults = out.get("faults")
    duration_s = None
    try:
        duration_s = float(out.get("simulation", {}).get("duration_s"))  # type: ignore[union-attr]
    except Exception as exc:
        record_runtime_diagnostic(
            code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
            category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
            location='src/sat_sim/agent_orchestrator.py:_repair_spec_once:01',
            exception=exc,
            strict=False,
        )
    if isinstance(faults, list):
        seen: set[str] = set()
        for i, fault in enumerate(faults):
            if not isinstance(fault, dict):
                continue
            fault.setdefault("fault_id", f"f_{i:03d}")
            if fault["fault_id"] in seen:
                fault["fault_id"] = f"{fault['fault_id']}_{i}"
            seen.add(str(fault["fault_id"]))
            fault.setdefault("target", "target_0")
            fault.setdefault("target_type", "battery")
            fault.setdefault("fault_type", "open_circuit")
            try:
                fault["onset_time_s"] = max(0.0, float(fault.get("onset_time_s", 0.0)))
            except Exception:
                fault["onset_time_s"] = 0.0
            if duration_s is not None and fault["onset_time_s"] > duration_s:
                fault["onset_time_s"] = max(0.0, duration_s * 0.5)
            try:
                dur = float(fault.get("duration_s", -1))
                fault["duration_s"] = dur if dur > 0 or dur == -1 else -1
            except Exception:
                fault["duration_s"] = -1
            try:
                mag = float(fault.get("magnitude", 1.0))
                fault["magnitude"] = min(1.0, max(0.0, mag))
            except Exception:
                fault["magnitude"] = 1.0

    return out


def _backend_from_name(name: str) -> AgentBackend:
    if name == "template":
        return TemplateAgentBackend()
    raise ValueError(
        f"unsupported backend {name!r}; P4 ships a deterministic 'template' backend. "
        "Use sat_sim.agent_frameworks to wrap OpenAI Agents SDK, LangGraph, or AutoGen in production."
    )


def run_agent_generation(request: AgentGenerationRequest) -> AgentSessionResult:
    """Compatibility wrapper around the V20 product Agent facade.

    The former P4 orchestration graph is no longer a second execution path.
    Existing imports remain valid, but all work is delegated to
    :mod:`sat_sim.agent_facade`.
    """

    from .agent_facade import AgentFacadeRequest, run_agent

    facade = run_agent(AgentFacadeRequest(
        request=request.request,
        output_dir=request.output_dir,
        examples_dir=request.examples_dir,
        backend=request.backend,
        task_id=request.task_id,
        output_root=request.output_root,
        max_repair_attempts=request.max_repair_attempts,
        auto_run=request.auto_run,
        dry_run=request.dry_run,
    ))
    steps = tuple(
        AgentStep(
            name=item.name,
            status=item.status,
            message=item.message,
            payload={**item.payload, "reason_codes": list(item.reason_codes)},
        )
        for item in facade.steps
    )
    return AgentSessionResult(
        ok=facade.ok,
        request=request,
        task_spec=facade.task_spec,
        validation=facade.validation,
        compiled=facade.compiled,
        run_result=facade.run_result,
        output_dir=facade.output_dir,
        files=facade.files,
        steps=steps,
    )


def generate_task_from_text(
    request_text: str,
    *,
    output_dir: str | Path = DEFAULT_GENERATED_DIR,
    examples_dir: str | Path = "examples",
    backend: str = "template",
    auto_run: bool = False,
    output_root: str | Path | None = None,
    task_id: str | None = None,
    max_repair_attempts: int = 2,
    dry_run: bool = False,
) -> AgentSessionResult:
    """Convenience wrapper for one-shot generation from natural language."""

    req = AgentGenerationRequest(
        request=request_text,
        output_dir=output_dir,
        examples_dir=examples_dir,
        backend=backend,
        auto_run=auto_run,
        output_root=output_root,
        task_id=task_id,
        max_repair_attempts=max_repair_attempts,
        dry_run=dry_run,
    )
    return run_agent_generation(req)


__all__ = [
    "AgentBackend",
    "AgentGenerationRequest",
    "AgentSessionResult",
    "AgentStep",
    "TemplateAgentBackend",
    "TemplateSelection",
    "generate_task_from_text",
    "run_agent_generation",
]
