"""Capability-aware Agent MVP for simulation script generation.

This module is the thin Agent/product layer on top of the capability-centric
simulation tooling introduced in P5/P6.  It does not ask an LLM to freely write
Python.  Instead, it prepares a capability-constrained prompt/context, accepts a
TaskSpec draft from either a deterministic local backend or an external LLM, and
then performs the trusted steps locally:

    natural language request
      -> capability-constrained TaskSpec draft
      -> validate / repair
      -> compile
      -> capability-python script export
      -> optional run

The module is intentionally dependency-light: no online model SDK is required to
run tests.  Production systems can call ``build_capability_agent_prompt`` and
feed the prompt to their chosen LLM, then pass the returned YAML/JSON draft back
through ``run_capability_script_generation(..., draft_spec_path=...)``.
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

from .capability_registry import (
    CapabilityContract,
    active_capability_ids,
    explicit_capability_ids_in_text,
    capability_summary_payload,
    get_capability,
    get_replacement_capability_id,
    legacy_compatibility_capability_ids,
    resolve_capability_id_for_product,
)
from .model_governance import ordered_product_capability_ids
from .llm_backends import LLMCallConfig, LLMCallResult, make_text_llm_backend
from .nl_outputs import resolve_output_intents
from .script_exporter import ScriptExportResult, export_runner_script
from .task_compiler import CompiledTask, compile_task_spec
from .task_models import CANONICAL_TASK_SPEC_VERSION
from .task_runner import TaskRunResult
from .unified_execution import execute_compiled_task
from .task_spec import TASK_SPEC_VERSION, TaskSpecError, load_task_spec, write_json
from .taskspec_alignment import align_task_spec_to_source
from .task_validator import ValidationIssue, ValidationResult, validate_task_spec

try:  # pragma: no cover - environment-specific dependency availability
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # type: ignore


A1_AGENT_VERSION = "a1.capability_agent_mvp.v0"
A2_AGENT_VERSION = "a2.real_llm_integration.v0"
A3_AGENT_VERSION = "a3.deepseek_integration.v0"
A4_AGENT_VERSION = "a4.structured_deepseek_output.v0"
DEFAULT_SCRIPT_NAME = "generated_simulation.py"
DEFAULT_TASK_YAML = "generated_task.yaml"
# Capability exposure is derived from lifecycle metadata in the registry.
# This removes the previous second, hand-maintained capability catalog from the
# Agent implementation while retaining explicit Route-B research profiles.
LEGACY_COMPATIBILITY_ALLOWED_CAPABILITIES = legacy_compatibility_capability_ids()
ACTIVE_DEFAULT_CAPABILITIES = ordered_product_capability_ids()

ROUTE_B_MODEL_LIBRARY_CAPABILITIES = (
    "orbit_environment.orbit_fidelity.v1",
    "subsystem.adcs_fidelity.v1",
    "whole_spacecraft.orbit_adcs_fidelity.v1",
    "whole_spacecraft.power_thermal_orbit_coupled.v1",
    "whole_spacecraft.comm_payload_mission_coupled.v1",
    "whole_spacecraft.maneuver_orbit_attitude.v1",
)

# Product-default Agent paths use only active capabilities.  Deprecated/internal
# capability files remain loadable through the explicit legacy compatibility
# profile for historical tests and reproducibility.
DEFAULT_ALLOWED_CAPABILITIES = ACTIVE_DEFAULT_CAPABILITIES

# LLM exposure profiles are lifecycle-aware.  ``default`` and
# ``real_llm_eval`` expose the active product catalog; ``legacy_compatibility``
# and ``full`` intentionally expose deprecated/internal capabilities for old
# regression tests and reproducibility only.
REAL_LLM_EVAL_ALLOWED_CAPABILITIES = ACTIVE_DEFAULT_CAPABILITIES
ROUTE_B_STRICT_ALLOWED_CAPABILITIES = ROUTE_B_MODEL_LIBRARY_CAPABILITIES

LLM_EXPOSURE_PROFILES: dict[str, tuple[str, ...]] = {
    "default": DEFAULT_ALLOWED_CAPABILITIES,
    "active": ACTIVE_DEFAULT_CAPABILITIES,
    "full": LEGACY_COMPATIBILITY_ALLOWED_CAPABILITIES,
    "legacy": LEGACY_COMPATIBILITY_ALLOWED_CAPABILITIES,
    "legacy_compatibility": LEGACY_COMPATIBILITY_ALLOWED_CAPABILITIES,
    "real_llm_eval": REAL_LLM_EVAL_ALLOWED_CAPABILITIES,
    "route_b_strict": ROUTE_B_STRICT_ALLOWED_CAPABILITIES,
}


def allowed_capabilities_for_profile(profile: str | None = None) -> tuple[str, ...]:
    """Return the capability exposure set for a named LLM/eval profile."""

    name = (profile or "default").strip().lower().replace("-", "_")
    if name not in LLM_EXPOSURE_PROFILES:
        valid = ", ".join(sorted(LLM_EXPOSURE_PROFILES))
        raise ValueError(f"unknown capability exposure profile {profile!r}; valid profiles: {valid}")
    return tuple(LLM_EXPOSURE_PROFILES[name])




def _selection_replacement_for_allowed(selection: CapabilityTemplateSelection, allowed: Sequence[str]) -> CapabilityTemplateSelection:
    """Replace deprecated/internal template selections on product paths."""

    if selection.capability_id in set(allowed):
        return selection
    replacement = get_replacement_capability_id(selection.capability_id)
    replacement_templates = {
        "orbit_environment.medium_fidelity.v1": "orbit_environment_medium_fidelity_nominal.yaml",
        "orbit_environment.orbit_fidelity.v1": "orbit_environment_orbit_fidelity_nominal.yaml",
        "subsystem.adcs_closed_loop.basic.v1": "subsystem_adcs_closed_loop_basic_nominal.yaml",
        "subsystem.adcs_fidelity.v1": "subsystem_adcs_fidelity_nominal.yaml",
        "whole_spacecraft.composite_digital_twin.v1": "whole_spacecraft_composite_digital_twin_nominal.yaml",
        "whole_spacecraft.power_thermal_orbit_coupled.v1": "whole_spacecraft_power_thermal_orbit_coupled_nominal.yaml",
        "whole_spacecraft.comm_payload_mission_coupled.v1": "whole_spacecraft_comm_payload_mission_coupled_nominal.yaml",
        "whole_spacecraft.maneuver_orbit_attitude.v1": "whole_spacecraft_maneuver_orbit_attitude_nominal.yaml",
        "whole_spacecraft.orbit_adcs_fidelity.v1": "whole_spacecraft_orbit_adcs_fidelity_nominal.yaml",
        "component.link_budget.v1": "component_link_budget_capability_nominal.yaml",
        "subsystem.comm.basic_ground_pass.v1": "subsystem_comm_basic_ground_pass_capability_nominal.yaml",
    }
    if replacement and replacement in set(allowed) and replacement in replacement_templates:
        return CapabilityTemplateSelection(
            replacement_templates[replacement],
            replacement,
            f"{selection.reason}; migrated from deprecated/internal {selection.capability_id} to active replacement {replacement}",
        )
    return selection


def _apply_capability_migration_metadata(out: dict[str, Any], migration: Mapping[str, Any] | None) -> None:
    if not migration:
        return
    out.setdefault("metadata", {})
    if isinstance(out["metadata"], dict):
        out["metadata"].setdefault("migration", {})
        out["metadata"]["migration"].update(dict(migration))
        out["metadata"].setdefault("agent", {})
        if isinstance(out["metadata"].get("agent"), dict):
            out["metadata"]["agent"]["capability_migration_applied"] = True

def _agent_version_for_backend(backend: str) -> str:
    if backend == "deepseek":
        return A3_AGENT_VERSION
    if backend in {"openai", "command"}:
        return A2_AGENT_VERSION
    return A1_AGENT_VERSION


@dataclass(frozen=True)
class CapabilityAgentRequest:
    """Request for generating a capability-backed simulation script."""

    request: str
    output_dir: str | Path = "generated_scripts"
    examples_dir: str | Path = "examples"
    backend: str = "template"
    draft_spec_path: str | Path | None = None
    allowed_capabilities: tuple[str, ...] = DEFAULT_ALLOWED_CAPABILITIES
    task_id: str | None = None
    output_root: str | Path | None = None
    script_output: str | Path | None = None
    max_repair_attempts: int = 2
    run_task: bool = False
    dry_run: bool = False
    # A2 real-model integration options.  ``command`` is the recommended
    # framework-neutral seam: the prompt is sent on stdin and stdout must contain
    # a TaskSpec draft.  ``openai`` is an optional Responses-API HTTPS backend; ``deepseek`` is an optional DeepSeek Chat Completions backend.
    model_command: str | None = None
    model_name: str | None = None
    model_base_url: str | None = None
    model_api_key_env: str = "OPENAI_API_KEY"
    model_timeout_s: float = 60.0
    model_temperature: float | None = None
    model_seed: int | None = None
    model_max_output_tokens: int | None = None
    model_structured_output: str = "json_object"
    advisory_context: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class CapabilityAgentStep:
    """One audited step in the generation trace."""

    name: str
    status: str
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CapabilityScriptGenerationResult:
    """Result bundle for one natural-language-to-script generation session."""

    ok: bool
    request: CapabilityAgentRequest
    task_spec: dict[str, Any]
    validation: ValidationResult
    compiled: CompiledTask | None = None
    script: ScriptExportResult | None = None
    run_result: TaskRunResult | None = None
    output_dir: Path | None = None
    files: dict[str, str] = field(default_factory=dict)
    steps: tuple[CapabilityAgentStep, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        req = asdict(self.request)
        req["output_dir"] = str(self.request.output_dir)
        req["examples_dir"] = str(self.request.examples_dir)
        req["draft_spec_path"] = str(self.request.draft_spec_path) if self.request.draft_spec_path is not None else None
        req["output_root"] = str(self.request.output_root) if self.request.output_root is not None else None
        req["script_output"] = str(self.request.script_output) if self.request.script_output is not None else None
        req["allowed_capabilities"] = list(self.request.allowed_capabilities)
        # Do not expose secrets; only expose backend configuration and env var names.
        req["model_api_key"] = None
        if req.get("backend") == "deepseek" and req.get("model_api_key_env") == "OPENAI_API_KEY":
            req["model_api_key_env"] = "DEEPSEEK_API_KEY"
        return {
            "ok": self.ok,
            "agent_version": _agent_version_for_backend(str(self.request.backend)),
            "request": req,
            "task_spec": self.task_spec,
            "validation": self.validation.to_dict(),
            "compiled": self.compiled.to_dict() if self.compiled else None,
            "script": self.script.to_dict() if self.script else None,
            "run_result": self.run_result.to_dict() if self.run_result else None,
            "output_dir": str(self.output_dir) if self.output_dir else None,
            "files": dict(self.files),
            "steps": [step.to_dict() for step in self.steps],
        }


class CapabilityAgentBackend(Protocol):
    """Protocol for pluggable TaskSpec draft backends.

    A real LLM backend should implement this protocol.  It should return a
    TaskSpec mapping only; validation, repair, compilation, script export, and
    execution remain trusted local steps.
    """

    name: str

    def draft_task_spec(self, *, request: CapabilityAgentRequest, context: Mapping[str, Any]) -> dict[str, Any]:
        """Return a TaskSpec draft."""


@dataclass(frozen=True)
class CapabilityTemplateSelection:
    template_file: str
    capability_id: str
    reason: str


def _safe_load_yaml_text(text: str) -> dict[str, Any]:
    if yaml is None:  # pragma: no cover
        raise TaskSpecError("PyYAML is required for capability Agent YAML parsing")
    try:
        data = yaml.safe_load(text)
    except Exception as exc:
        hint = ""
        if re.search(r"\[[^\]]+\]\s*\*\s*\d+", text):
            hint = " Model output appears to contain a Python list-repetition expression such as '[1.0] * 30'; TaskSpec drafts must be literal JSON/YAML data, not executable expressions."
        raise TaskSpecError(f"failed to parse TaskSpec draft as YAML/JSON-compatible data: {exc}.{hint}") from exc
    if not isinstance(data, dict):
        raise TaskSpecError("TaskSpec draft root must be a mapping/object")
    return data


def _safe_dump_yaml(data: Mapping[str, Any]) -> str:
    if yaml is None:  # pragma: no cover
        return json.dumps(data, indent=2, ensure_ascii=False)
    return yaml.safe_dump(dict(data), sort_keys=False, allow_unicode=True)


def _write_yaml(path: Path, data: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_safe_dump_yaml(data), encoding="utf-8")
    return path


def _slugify(text: str, *, default: str = "generated_sim", max_len: int = 72) -> str:
    ascii_text = text.encode("ascii", "ignore").decode("ascii")
    ascii_text = re.sub(r"[^A-Za-z0-9_.-]+", "_", ascii_text).strip("_.-")
    ascii_text = re.sub(r"_+", "_", ascii_text)
    if not ascii_text:
        ascii_text = default
    return ascii_text[:max_len].strip("_.-") or default


def _short_hash(text: str, n: int = 10) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:n]


def _contains_any(text: str, words: Sequence[str]) -> bool:
    lower = text.lower()
    return any(word.lower() in lower for word in words)


def requested_health_mode(text: str) -> str:
    """Infer the health mode used to scope approved experience retrieval."""

    lower = text.lower()
    # Nominal requests often state the absence of injected effects. Remove those
    # clauses before looking for positive fault or degradation intent.
    probe = re.sub(
        r"(?:without|no)\s+(?:injected\s+)?(?:faults?|degradations?)"
        r"(?:\s*(?:or|and|/)\s*(?:faults?|degradations?))?",
        " ",
        lower,
    )
    probe = re.sub(
        r"不(?:注入|包含|启用)?[^，。；;\n]{0,16}(?:故障|退化)"
        r"(?:\s*(?:或|和|/)\s*(?:故障|退化))?",
        " ",
        probe,
    )
    fault = _contains_any(
        probe,
        [
            "mode=fault",
            "mode: fault",
            "fault",
            "故障",
            "开路",
            "open circuit",
            "failure",
            "失效",
            "异常",
            "中断",
            "outage",
            "jam",
            "卡滞",
        ],
    )
    degradation = _contains_any(
        probe,
        [
            "mode=degradation",
            "mode: degradation",
            "degradation",
            "退化",
            "老化",
            "衰减",
            "degrade",
            "capacity loss",
            "efficiency loss",
            "性能损失",
            "磨损",
        ],
    )
    if fault and degradation:
        return "mixed"
    if fault:
        return "fault"
    if degradation:
        return "degradation"
    return "nominal"


def _extract_seconds(text: str, keywords: Sequence[str]) -> float | None:
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
    # Keep reverse patterns within the same clause.  Without the Chinese comma
    # delimiter, a phrase such as "仿真1800秒，每60秒采样" could incorrectly bind
    # the 1800-second duration to the sample keyword.
    clause_chars = r"[^\n。；;,，]"
    reverse_clause_chars = r"[^0-9\n。；;,，]"
    patterns = [
        rf"(?:{keyword_pattern}){clause_chars}{{0,30}}?{number_unit}",
        rf"(?:每|every)\s*{number_unit}{reverse_clause_chars}{{0,30}}(?:{keyword_pattern})",
        rf"{number_unit}{reverse_clause_chars}{{0,30}}(?:{keyword_pattern})",
    ]
    for pattern in patterns:
        m = re.search(pattern, lower, re.IGNORECASE)
        if not m:
            continue
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
        rf"(?:{keyword_pattern})[^0-9\-]{{0,30}}([0-9]+(?:\.[0-9]+)?)",
        rf"([0-9]+(?:\.[0-9]+)?)\s*[^\n。；;,，]{{0,30}}(?:{keyword_pattern})",
    ]
    for pattern in patterns:
        for m in re.finditer(pattern, lower, re.IGNORECASE):
            value = float(m.group(1))
            if min_value is not None and value < min_value:
                continue
            if max_value is not None and value > max_value:
                continue
            return value
    return None




def _extract_distance_m(text: str, keywords: Sequence[str]) -> float | None:
    """Extract a distance in meters near keywords such as altitude/高度.

    This avoids binding duration text across Chinese commas, e.g.
    "仿真 2400 秒，每 60 秒采样，轨道高度 500 km" must return 500000 m,
    not 2400000 m.
    """

    lower = text.lower()
    keyword_pattern = "|".join(re.escape(k.lower()) for k in keywords)
    unit_map = {
        "m": 1.0, "meter": 1.0, "meters": 1.0, "米": 1.0,
        "km": 1000.0, "kilometer": 1000.0, "kilometers": 1000.0, "公里": 1000.0, "千米": 1000.0,
    }
    clause_chars = r"[^\n。；;,，]"
    number_unit = r"([0-9]+(?:\.[0-9]+)?)\s*(km|kilometers?|公里|千米|m|meters?|米)?"
    patterns = [
        rf"(?:{keyword_pattern}){clause_chars}{{0,30}}?{number_unit}",
        rf"{number_unit}{clause_chars}{{0,30}}(?:{keyword_pattern})",
    ]
    for pattern in patterns:
        m = re.search(pattern, lower, re.IGNORECASE)
        if not m:
            continue
        value = float(m.group(1))
        unit = (m.group(2) or "").lower()
        if unit:
            return value * unit_map.get(unit, 1.0)
        # LEO altitude values below 10,000 are almost always kilometers in user text.
        return value * 1000.0 if value < 10000.0 else value
    return None


def _extract_bits_value(text: str, keywords: Sequence[str]) -> float | None:
    """Extract a bit quantity near storage/queue capacity keywords."""

    lower = text.lower()
    keyword_pattern = "|".join(re.escape(k.lower()) for k in keywords)
    clause_chars = r"[^\n。；;,，]"
    unit_map = {
        "bit": 1.0, "bits": 1.0, "b": 1.0,
        "kbit": 1e3, "kb": 8e3,
        "mbit": 1e6, "mb": 8e6,
        "gbit": 1e9, "gb": 8e9,
    }
    number_unit = r"([0-9]+(?:\.[0-9]+)?)\s*(gbit|mbit|kbit|bits?|gb|mb|kb|b)?"
    patterns = [
        rf"(?:{keyword_pattern}){clause_chars}{{0,30}}?{number_unit}",
        rf"{number_unit}{clause_chars}{{0,30}}(?:{keyword_pattern})",
    ]
    for pattern in patterns:
        m = re.search(pattern, lower, re.IGNORECASE)
        if not m:
            continue
        value = float(m.group(1))
        unit = (m.group(2) or "").lower()
        if unit:
            return value * unit_map.get(unit, 1.0)
        return value
    return None




def _extract_mass_kg(text: str, keywords: Sequence[str]) -> float | None:
    """Extract a mass in kg near fuel/propellant keywords."""

    lower = text.lower()
    keyword_pattern = "|".join(re.escape(k.lower()) for k in keywords)
    clause_chars = r"[^\n。；;,，]"
    unit_map = {
        "kg": 1.0, "kgs": 1.0, "kilogram": 1.0, "kilograms": 1.0, "千克": 1.0, "公斤": 1.0,
        "g": 0.001, "gram": 0.001, "grams": 0.001, "克": 0.001,
    }
    number_unit = r"([0-9]+(?:\.[0-9]+)?)\s*(kg|kgs|kilograms?|千克|公斤|g|grams?|克)?"
    patterns = [
        rf"(?:{keyword_pattern}){clause_chars}{{0,30}}?{number_unit}",
        rf"{number_unit}{clause_chars}{{0,30}}(?:{keyword_pattern})",
    ]
    for pattern in patterns:
        m = re.search(pattern, lower, re.IGNORECASE)
        if not m:
            continue
        value = float(m.group(1))
        unit = (m.group(2) or "kg").lower()
        return value * unit_map.get(unit, 1.0)
    return None


def _extract_ratio_value(text: str, keywords: Sequence[str], *, default_if_percent: bool = True) -> float | None:
    lower = text.lower()
    keyword_pattern = "|".join(re.escape(k.lower()) for k in keywords)
    clause_chars = r"[^\n。；;,，]"
    patterns = [
        rf"(?:{keyword_pattern}){clause_chars}{{0,30}}?([0-9]+(?:\.[0-9]+)?)\s*(%|percent|百分比)?",
        rf"([0-9]+(?:\.[0-9]+)?)\s*(%|percent|百分比)?{clause_chars}{{0,30}}(?:{keyword_pattern})",
    ]
    for pattern in patterns:
        m = re.search(pattern, lower, re.IGNORECASE)
        if not m:
            continue
        value = float(m.group(1))
        unit = (m.group(2) or "").lower()
        if unit or (default_if_percent and value > 1.0):
            value /= 100.0
        return max(0.0, min(1.0, value))
    return None

def _set_path(data: dict[str, Any], path: str, value: Any) -> None:
    from .campaign import set_by_path

    set_by_path(data, path, value)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _examples_by_file(examples_dir: str | Path) -> dict[str, Path]:
    root = Path(examples_dir)
    out: dict[str, Path] = {}
    if not root.exists():
        return out
    for path in sorted(list(root.glob("*.yaml")) + list(root.glob("*.yml")) + list(root.glob("*.json"))):
        out[path.name] = path
        out[path.stem] = path
    return out


def _load_template(template_file: str, examples_dir: str | Path) -> dict[str, Any]:
    examples = _examples_by_file(examples_dir)
    path = examples.get(template_file) or examples.get(Path(template_file).stem)
    if path is None:
        raise FileNotFoundError(f"capability example template not found: {template_file}")
    return load_task_spec(path).data


def _default_parameter_value(payload: Mapping[str, Any]) -> Any:
    if "default" in payload:
        return copy.deepcopy(payload["default"])
    if "min" in payload:
        return payload["min"]
    kind = str(payload.get("type") or "")
    if "array" in kind:
        return []
    if "integer" in kind:
        return 1
    if "number" in kind:
        return 1.0
    if "boolean" in kind or kind == "bool":
        return False
    return ""


def _synthesize_template(capability_id: str, template_file: str) -> dict[str, Any]:
    """Build a deterministic minimal legacy draft when an example file is absent.

    The capability registry, not the Agent's memory, supplies target ownership,
    parameter defaults and output fields. This keeps installed packages usable
    even when optional example collections are not copied beside the source tree.
    """

    contract = get_capability(capability_id)
    target_level = contract.target_level
    # Historical orbit contracts use ``target.level=integrated`` while the
    # public TaskSpec task type is ``orbit_environment``. Preserve both
    # representations so installed-package template synthesis satisfies the
    # existing adapter contract without leaking the historical label into the
    # canonical simulation level.
    task_level = (
        "orbit_environment"
        if target_level == "integrated" and contract.capability_id.startswith("orbit_environment.")
        else target_level
    )
    target_name = contract.target_name
    parameters: dict[str, Any] = {}
    for name, payload in (contract.data.get("parameters") or {}).items():
        if isinstance(payload, Mapping) and (payload.get("required") or "default" in payload):
            value = _default_parameter_value(payload)
            if not payload.get("required") and isinstance(value, (list, dict)) and not value:
                continue
            parameters[str(name)] = value
    trace_fields = []
    outputs = contract.data.get("outputs") if isinstance(contract.data.get("outputs"), Mapping) else {}
    for item in outputs.get("trace", []) if isinstance(outputs, Mapping) else []:
        if isinstance(item, Mapping) and isinstance(item.get("name"), str):
            trace_fields.append(str(item["name"]))

    duration_s = 300.0
    if "burn_start_s" in parameters:
        try:
            burn_start_s = float(parameters.get("burn_start_s", 0.0) or 0.0)
            burn_duration_s = float(parameters.get("burn_duration_s", 0.0) or 0.0)
            duration_s = max(duration_s, burn_start_s + max(0.0, burn_duration_s) + 10.0)
        except (TypeError, ValueError) as exc:
            record_runtime_diagnostic(
                code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                location='src/sat_sim/capability_agent.py:_synthesize_template:01',
                exception=exc,
                strict=False,
            )

    name_lower = template_file.lower()
    mode = "fault" if "fault" in name_lower else "degradation" if "degradation" in name_lower else "nominal"
    spec: dict[str, Any] = {
        "schema_version": TASK_SPEC_VERSION,
        "task_id": Path(template_file).stem[:120],
        "task_type": task_level,
        "description": f"Registry-synthesized template for {capability_id}",
        "capability_id": capability_id,
        "simulation": {
            "duration_s": duration_s,
            "sample_s": 10.0,
            "backend": "selective_unified_basilisk_assembly",
        },
        "target": {"level": target_level, "name": target_name, "mode": mode},
        "parameters": parameters,
        "faults": [],
        "modifiers": {"faults": [], "degradations": [], "constraints": []},
        "outputs": {
            "output_root": "runs",
            "trace_format": "csv",
            "include_summary": True,
            "include_trace": True,
            "include_labels": True,
            "include_manifest": True,
            "record_fields": trace_fields,
        },
        "metadata": {
            "template_source": "capability_registry_synthesized",
            "template_file_requested": template_file,
            "reason_code": "TEMPLATE_SYNTHESIZED",
        },
    }
    if task_level == "whole_spacecraft":
        spec["spacecraft"] = {"mission": {"template": "registry_synthesized"}}
    if task_level == "orbit_environment":
        spec["orbit_environment"] = {"step_s": 1.0}
    modes = contract.data.get("modes") if isinstance(contract.data.get("modes"), Mapping) else {}
    if mode == "fault":
        payload = modes.get("fault") if isinstance(modes.get("fault"), Mapping) else {}
        types = payload.get("fault_types") if isinstance(payload, Mapping) else []
        fault_type = str(types[0]) if isinstance(types, list) and types else "signal_loss"
        spec["faults"] = [{
            "fault_id": "fault_001",
            "target": target_name,
            "target_type": target_name if target_name in {"battery", "solar_panel", "reaction_wheel", "thruster", "heater", "radiator"} else "unknown",
            "fault_type": fault_type,
            "onset_time_s": 0.0,
            "duration_s": -1.0,
            "magnitude": 1.0,
            "parameters": {},
        }]
    elif mode == "degradation":
        payload = modes.get("degradation") if isinstance(modes.get("degradation"), Mapping) else {}
        types = payload.get("degradation_types") if isinstance(payload, Mapping) else []
        degradation_type = str(types[0]) if isinstance(types, list) and types else "efficiency_scale"
        spec["modifiers"]["degradations"] = [{
            "degradation_id": "degradation_001",
            "target": target_name,
            "degradation_type": degradation_type,
            "onset_time_s": 0.0,
            "duration_s": -1.0,
            "scale": 0.9,
            "parameters": {},
        }]
        degradation_value = 10.0 if degradation_type.endswith("_pct") else 0.1
        legacy_paths = {
            "battery": ("eps", "battery"),
            "solar_panel": ("eps", "solar_panel"),
            "reaction_wheel": ("adcs", "reaction_wheel"),
        }
        path = legacy_paths.get(target_name)
        if path is not None:
            group, component = path
            spec["degradations"] = {group: {component: {degradation_type: degradation_value}}}
    return spec


def _normalize_event_windows(spec: dict[str, Any]) -> None:
    """Keep generated event onsets inside the final simulation window.

    Templates are selected before natural-language duration overrides are
    applied.  A template's default event time can therefore become equal to or
    greater than the requested duration.  Clamp only this structural conflict;
    explicit semantic time interpretation remains a V23 concern.
    """

    def normalize_one(payload: dict[str, Any]) -> None:
        simulation = payload.get("simulation") if isinstance(payload.get("simulation"), Mapping) else {}
        try:
            duration_s = float(simulation.get("duration_s"))
        except Exception:
            return
        if duration_s <= 0:
            return
        collections: list[Any] = [payload.get("faults")]
        modifiers = payload.get("modifiers") if isinstance(payload.get("modifiers"), Mapping) else {}
        collections.extend([modifiers.get("faults"), modifiers.get("degradations"), modifiers.get("constraints")])
        for collection in collections:
            if not isinstance(collection, list):
                continue
            for event in collection:
                if not isinstance(event, dict):
                    continue
                try:
                    onset = float(event.get("onset_time_s", 0.0) or 0.0)
                except Exception:
                    onset = 0.0
                if onset >= duration_s:
                    event["onset_time_s"] = max(0.0, duration_s * 0.5)
        campaign = payload.get("campaign") if isinstance(payload.get("campaign"), Mapping) else {}
        base_spec = campaign.get("base_spec") if isinstance(campaign.get("base_spec"), dict) else None
        if base_spec is not None:
            normalize_one(base_spec)

    normalize_one(spec)


def _summarize_contract(contract: CapabilityContract, *, allowed_dependency_capabilities: set[str] | None = None) -> dict[str, Any]:
    data = contract.data
    modes = data.get("modes") if isinstance(data.get("modes"), Mapping) else {}
    parameters = data.get("parameters") if isinstance(data.get("parameters"), Mapping) else {}
    outputs = data.get("outputs") if isinstance(data.get("outputs"), Mapping) else {}
    interface = contract.interface_summary
    summarized_params: dict[str, Any] = {}
    for name, payload in parameters.items():
        if not isinstance(payload, Mapping):
            continue
        summarized_params[name] = {
            key: payload[key]
            for key in ("type", "unit", "required", "min", "max", "default", "description")
            if key in payload
        }
    dependencies = list(interface["dependencies"] or [])
    if allowed_dependency_capabilities is not None:
        # Prompt exposure profiles should not leak hidden capability IDs through
        # dependency metadata.  Runtime contracts remain unchanged; this is only
        # the LLM-facing summary.
        dependencies = [
            item for item in dependencies
            if not (isinstance(item, Mapping) and item.get("capability_id") and str(item.get("capability_id")) not in allowed_dependency_capabilities)
        ]
    return {
        "capability_id": contract.capability_id,
        "level": contract.target_level,
        "target": contract.target_name,
        "domain": data.get("domain"),
        "trust_level": contract.trust_level,
        "summary": data.get("summary"),
        "modes": modes,
        "parameters": summarized_params,
        "outputs": outputs,
        "dependencies": dependencies,
        "consumes": interface["consumes"],
        "produces": interface["produces"],
        "time_grid": interface["time_grid"],
        "trace_fields": interface["trace_fields"],
        "operator": contract.operator_contract.model_dump(mode="json"),
    }


def _compact_contract_summary(contract: CapabilityContract) -> dict[str, Any]:
    """Small LLM-facing contract for local models with 8K context windows."""
    data = contract.data
    params: dict[str, Any] = {}
    raw_parameters = data.get("parameters") if isinstance(data.get("parameters"), Mapping) else {}
    for name, payload in raw_parameters.items():
        if not isinstance(payload, Mapping):
            continue
        row = {key: payload[key] for key in ("type", "unit", "required", "min", "max", "default", "enum") if key in payload}
        description = str(payload.get("description") or "").strip()
        if description:
            row["description"] = description[:120]
        params[str(name)] = row
    modes = data.get("modes") if isinstance(data.get("modes"), Mapping) else {}
    effects: dict[str, list[str]] = {"faults": [], "degradations": [], "constraints": []}
    for mode_name, destination, key in (("fault", "faults", "fault_types"), ("degradation", "degradations", "degradation_types"), ("constraint", "constraints", "constraint_types")):
        payload = modes.get(mode_name) if isinstance(modes.get(mode_name), Mapping) else {}
        values = payload.get(key) if isinstance(payload, Mapping) else []
        if isinstance(values, list):
            effects[destination] = [str(item) for item in values[:24]]
    return {
        "capability_id": contract.capability_id,
        "task_type": contract.target_level,
        "target": {"level": contract.target_level, "name": contract.target_name},
        "parameters": params,
        "effects": effects,
        "required_simulation_fields": ["duration_s", "sample_s"],
        "subsystem_rule": (f"simulation.subsystem must be {contract.target_name}" if contract.target_level == "subsystem" else None),
    }


def _scrub_capability_summary_for_prompt(item: Mapping[str, Any], *, allowed_capabilities: set[str]) -> dict[str, Any]:
    """Return an LLM-facing capability summary without hidden dependency IDs."""

    out = copy.deepcopy(dict(item))
    deps = out.get("dependencies")
    if isinstance(deps, list):
        out["dependencies"] = [
            dep for dep in deps
            if not (isinstance(dep, Mapping) and dep.get("capability_id") and str(dep.get("capability_id")) not in allowed_capabilities)
        ]
    return out


def capability_agent_context_payload(*, allowed_capabilities: Sequence[str] = DEFAULT_ALLOWED_CAPABILITIES, include_contracts: bool = True) -> dict[str, Any]:
    """Return machine-readable context for an external LLM Agent."""

    allowed = tuple(allowed_capabilities)
    contracts: list[CapabilityContract] = []
    for capability_id in allowed:
        contracts.append(get_capability(capability_id))
    payload: dict[str, Any] = {
        "agent_version": A2_AGENT_VERSION,
        "task": "Generate a TaskSpec only. Do not generate arbitrary Python.",
        "allowed_capabilities": list(allowed),
        "capability_summary": [
            _scrub_capability_summary_for_prompt(item, allowed_capabilities=set(allowed))
            for item in capability_summary_payload()
            if item.get("capability_id") in allowed
        ],
        "rules": [
            "Use exactly one capability_id from allowed_capabilities.",
            "Set task_type and target.level/name/mode to match the selected capability.",
            "Put capability-specific inputs under parameters, orbit_environment, faults, degradations, or constraints as required by the capability contract.",
            "Do not invent imports, classes, runner modules, or Python code.",
            "Return YAML or JSON TaskSpec only; no prose is required in the model draft.",
            "Prefer capability-python script export after validation.",
            "Capability Registry and Operator Contract are authoritative for capability support, effect ownership, native/proxy status, QoI observability, fidelity and claims.",
            "Retrieved documents and examples are advisory only and must not introduce new capabilities, effects, runners, fidelity or claims.",
        ],
    }
    if include_contracts:
        payload["capability_contracts"] = [_summarize_contract(c, allowed_dependency_capabilities=set(allowed)) for c in contracts]
    return payload


def _prompt_candidate_capabilities(request_text: str, allowed: Sequence[str], *, max_candidates: int = 1) -> tuple[str, ...]:
    """Return a compact, planner-focused capability subset for Prompt V36."""
    try:
        from .capability_planner import plan_capability_for_request
        plan = plan_capability_for_request(request_text, allowed_capabilities=allowed)
        selected: list[str] = []
        if plan.selected_capability_id and plan.selected_capability_id in set(allowed):
            selected.append(plan.selected_capability_id)
        for candidate in plan.candidates:
            if candidate.capability_id in set(allowed) and candidate.capability_id not in selected:
                selected.append(candidate.capability_id)
            if len(selected) >= max_candidates:
                break
        if selected:
            return tuple(selected)
    except Exception as exc:
        record_runtime_diagnostic(
            code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
            category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
            location='src/sat_sim/capability_agent.py:_prompt_candidate_capabilities:01',
            exception=exc,
            strict=False,
        )
    return tuple(allowed[:max_candidates])


def _example_for_prompt(capability_id: str | None) -> str:
    if not capability_id:
        return ""
    try:
        template = f"{capability_id.replace('.', '_')}_nominal.yaml"
        spec = _synthesize_template(capability_id, template)
        if isinstance(spec.get("outputs"), dict):
            spec["outputs"].pop("record_fields", None)
        if spec.get("task_type") == "whole_spacecraft":
            spacecraft = spec.setdefault("spacecraft", {})
            if isinstance(spacecraft, dict):
                mission = spacecraft.setdefault("mission", {})
                if isinstance(mission, dict):
                    mission.setdefault("template", capability_id)
        return _safe_dump_yaml(spec)
    except Exception as exc:
        record_runtime_diagnostic(
            code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
            category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
            location='src/sat_sim/capability_agent.py:_example_for_prompt:01',
            exception=exc,
            strict=False,
        )
        return ""


def _experience_example_for_prompt(outline: Mapping[str, Any]) -> str | None:
    """Adapt a canonical lesson outline to the generator's legacy draft contract."""

    model = outline.get("model")
    simulation = outline.get("simulation")
    parameters = outline.get("parameters")
    events = outline.get("events")
    outputs = outline.get("outputs")
    if not all(
        isinstance(item, Mapping)
        for item in (model, simulation, parameters, events, outputs)
    ):
        return None
    capability_id = model.get("capability_id")
    target = model.get("target")
    values = parameters.get("values")
    if (
        not isinstance(capability_id, str)
        or not capability_id
        or not isinstance(target, Mapping)
        or not isinstance(values, Mapping)
    ):
        return None
    # Event conversion needs effect-specific legacy fields. For those lessons,
    # retain the registry-generated example until a lossless adapter is available.
    if any(events.get(category) for category in ("faults", "degradations", "constraints")):
        return None
    level = str(target.get("level") or "component")
    task_type = level if level in {
        "component",
        "subsystem",
        "orbit_environment",
        "whole_spacecraft",
    } else "component"
    sim = {
        key: simulation[key]
        for key in ("duration_s", "sample_s", "backend")
        if key in simulation
    }
    sim.setdefault("backend", "selective_unified_basilisk_assembly")
    payload: dict[str, Any] = {
        "schema_version": TASK_SPEC_VERSION,
        "task_id": "approved_experience_reference",
        "task_type": task_type,
        "capability_id": capability_id,
        "simulation": sim,
        "target": {
            key: target[key] for key in ("level", "name", "mode") if key in target
        },
        "parameters": dict(values),
        "faults": [],
        "modifiers": {"faults": [], "degradations": [], "constraints": []},
        "outputs": {
            "output_root": "runs",
            "trace_format": "csv",
            "include_summary": True,
            "include_trace": True,
            "include_labels": True,
            "include_manifest": True,
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def build_capability_agent_prompt(
    request_text: str,
    *,
    allowed_capabilities: Sequence[str] = DEFAULT_ALLOWED_CAPABILITIES,
    compact: bool = True,
    advisory_context: Sequence[Mapping[str, Any]] = (),
) -> str:
    """Build a strict prompt that can be sent to an external LLM.

    V36 defaults to a compact, planner-scoped prompt.  Full context remains
    available through ``compact=False`` for audits, but product generation should
    avoid dumping the complete 26-capability contract set into every model call.
    """
    allowed = tuple(allowed_capabilities)
    allowed_set = set(allowed)
    prompt_capabilities = _prompt_candidate_capabilities(request_text, allowed) if compact else allowed
    if compact:
        contracts = [get_capability(capability_id) for capability_id in prompt_capabilities]
        context = {
            "prompt_scope": "local_compact",
            "allowed_capabilities": list(prompt_capabilities),
            "selected_capability": _compact_contract_summary(contracts[0]) if contracts else None,
            "rules": [
                "Generate only the selected capability.",
                "Use canonical TaskSpec fields only.",
                "For subsystem level set simulation.subsystem to the declared target name.",
                "Do not invent events, parameters, outputs, code, imports, fidelity, or claims.",
            ],
        }
    else:
        context = capability_agent_context_payload(allowed_capabilities=prompt_capabilities, include_contracts=True)
        context["allowed_capabilities_authoritative"] = list(allowed)
        context["prompt_scope"] = "full"
    context_json = json.dumps(context, separators=(",", ":"), ensure_ascii=False) if compact else json.dumps(context, indent=2, ensure_ascii=False)
    selected_capability = prompt_capabilities[0] if prompt_capabilities else None
    canonical_example = _example_for_prompt(selected_capability)
    for item in advisory_context:
        advisory = item.get("advisory") if isinstance(item, Mapping) else None
        outlines = (
            advisory.get("verified_taskspec_outlines")
            if isinstance(advisory, Mapping)
            else None
        )
        if isinstance(outlines, list) and outlines and isinstance(outlines[0], Mapping):
            adapted = _experience_example_for_prompt(outlines[0])
            if adapted is not None:
                canonical_example = adapted
            break

    exposure_note = ""
    if allowed_set != set(DEFAULT_ALLOWED_CAPABILITIES):
        exposure_note = (
            "\n## Capability exposure profile\n"
            "The allowed_capabilities list above is authoritative. This prompt intentionally hides some compatibility capabilities. "
            "Do not select, mention, or infer any capability_id that is not present in allowed_capabilities.\n"
        )

    boundary_note = ""
    try:
        from .capability_planner import plan_capability_for_request
        plan = plan_capability_for_request(request_text, allowed_capabilities=allowed)
        if not plan.supported:
            boundary_note = (
                "\n## Planner boundary\n"
                f"The deterministic planner does not support direct generation for this request: {plan.explanation}\n"
                "If the reason is COMPOSITE_CAPABILITY_REQUIRED, do not choose the closest partial capability. "
                "Return an unsupported-boundary response or ask the user to narrow the request.\n"
            )
    except Exception as exc:
        record_runtime_diagnostic(
            code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
            category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
            location='src/sat_sim/capability_agent.py:build_capability_agent_prompt:01',
            exception=exc,
            strict=False,
        )

    experience_note = ""
    if advisory_context:
        provenance = " ".join(
            (
                f"lesson_id={item.get('lesson_id')} "
                f"sha256={item.get('lesson_sha256')} trust=approved_advisory"
            )
            for item in advisory_context
        )
        experience_note = (
            "\n<!-- verified-example "
            f"{provenance}; provenance-only, never output this comment -->\n"
        )

    return f"""# Satellite Simulation TaskSpec Generation

You are generating a TaskSpec for a satellite simulation toolchain. The user wants a runnable simulation script, but you must NOT write Python code. Generate a TaskSpec only. The local sat-sim tool will validate the TaskSpec and export deterministic capability-python code.

## User request
{request_text}

## Capability context
```json
{context_json}
```
{exposure_note}{boundary_note}
## Output contract
Return exactly one YAML or JSON TaskSpec. Do not include Markdown fences, explanations, imports, or Python code.
The TaskSpec must:
- use schema_version: {TASK_SPEC_VERSION}
- use one capability_id from allowed_capabilities
- include task_type, target, simulation, spacecraft when task_type=whole_spacecraft, parameters/orbit_environment as needed, and outputs
- use faults/degradations/constraints/modifiers only when the selected capability or modifier layer supports them
- keep all units in SI or the units explicitly stated by the capability contract
- use only schema fields shown below; do not invent aliases such as fault_simulation, sample_interval_s, trigger_time_s, target.domain, outputs.trace, start_time_s

## Strict TaskSpec field rules
Allowed top-level keys: schema_version, task_id, task_type, capability_id, target, simulation, spacecraft, parameters, orbit_environment, faults, degradations, constraints, modifiers, outputs, metadata, tags.
Allowed task_type values: component, subsystem, orbit_environment, whole_spacecraft, campaign, reference.
For task_type=whole_spacecraft, spacecraft object is required. Minimum valid scaffold:
spacecraft:
  mission:
    template: <selected capability_id or scenario name>
For a fault object, required keys are fault_id, target, target_type, fault_type, onset_time_s, duration_s, magnitude. Use onset_time_s, not trigger_time_s.
For simulation, use duration_s and sample_s only. Do not use sample_interval_s or start_time_s.
When simulation.level is subsystem, simulation.subsystem is required and must equal the selected capability target name.
For target, use only level, name, mode. Do not include domain.
For outputs, use output_root, trace_format, include_summary, include_trace, include_labels, include_manifest. If the user explicitly asks for output curves or telemetry, include concrete capability-declared field names under outputs.plots; do not invent field names.

## Capability routing rules
- The deterministic planner is authoritative for capability support.
- Full-subsystem whole-spacecraft requests that require orbit + ADCS + EPS + thermal + comm + payload need a future composite ExecutionPlan. Do not satisfy them with a single partial capability.
- Static load fields such as adcs_load_power_w, comm_load_power_w, or payload_load_power_w are load proxies only; they do not invoke the full ADCS, communication, or payload simulations.
- Capability Registry and Operator Contract are authoritative for capability support, effect ownership, native/proxy status, QoI observability, fidelity and claims.
- Retrieved documents and examples are advisory only and must not introduce new capabilities, effects, runners, fidelity or claims.

{experience_note}
## Capability-specific example
```yaml
{canonical_example}
```
"""

def extract_task_spec_from_model_text(text: str) -> dict[str, Any]:
    """Extract a YAML/JSON TaskSpec from LLM output text.

    The parser accepts raw YAML/JSON or a single Markdown fenced code block.  It
    intentionally returns only mappings because downstream tooling requires a
    TaskSpec object.
    """

    stripped = text.strip()
    # Prefer explicit YAML/JSON code blocks if a model includes prose.
    fence = re.search(r"```(?:yaml|yml|json)?\s*(.*?)```", stripped, re.IGNORECASE | re.DOTALL)
    if fence:
        stripped = fence.group(1).strip()
    # Try JSON first for strict model outputs.
    try:
        data = json.loads(stripped)
        if isinstance(data, dict):
            for key in ("task_spec", "taskspec", "TaskSpec", "spec"):
                nested = data.get(key)
                if isinstance(nested, dict):
                    return nested
            return data
    except Exception as exc:
        record_runtime_diagnostic(
            code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
            category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
            location='src/sat_sim/capability_agent.py:extract_task_spec_from_model_text:01',
            exception=exc,
            strict=False,
        )
    parsed = _safe_load_yaml_text(stripped)
    for key in ("task_spec", "taskspec", "TaskSpec", "spec"):
        nested = parsed.get(key)
        if isinstance(nested, dict):
            return nested
    return parsed


def load_model_draft(path: str | Path) -> dict[str, Any]:
    """Load a TaskSpec draft from external LLM output or a YAML/JSON file."""

    path = Path(path)
    text = path.read_text(encoding="utf-8")
    return extract_task_spec_from_model_text(text)


class CapabilityTemplateBackend:
    """Deterministic capability-aware backend for local smoke tests.

    This backend is deliberately small.  It proves the A1 pipeline without a
    model SDK.  Production deployments should provide an LLM-generated draft via
    ``draft_spec_path`` or implement ``CapabilityAgentBackend``.
    """

    name = "template"

    def select_template(self, request: str, context: Mapping[str, Any]) -> CapabilityTemplateSelection:
        allowed = context.get("allowed_capabilities") if isinstance(context.get("allowed_capabilities"), list) else None
        explicit_ids = explicit_capability_ids_in_text(request, allowed_capabilities=allowed)
        if len(explicit_ids) == 1:
            capability_id = explicit_ids[0]
            return CapabilityTemplateSelection(
                f"{capability_id.replace('.', '_')}_nominal.yaml",
                capability_id,
                "exact capability_id requested",
            )
        if len(explicit_ids) > 1:
            raise TaskSpecError("request contains multiple exact capability IDs; select exactly one")
        text = request.lower()
        is_fault = _contains_any(text, ["fault", "故障", "开路", "open", "failure", "失效", "异常", "中断", "关机", "关闭", "断电", "outage", "jam", "卡滞", "遮挡"])
        is_degradation = _contains_any(text, ["degradation", "退化", "老化", "衰减", "degrade", "loss", "损失", "wear", "磨损"])
        mentions_eps = _contains_any(text, ["eps", "电源", "供电", "电源系统", "能量", "pdu", "负载", "load", "甩载", "eclipse", "日食", "shadow", "阴影"])
        mentions_solar = _contains_any(text, ["solar panel", "solar", "太阳阵列", "太阳帆板", "太阳能板", "太阳翼", "帆板", "遮挡"])
        mentions_battery = _contains_any(text, ["battery", "电池", "soc", "荷电", "容量"])
        mentions_rw = _contains_any(text, ["reaction wheel", "rw", "反作用轮", "动量轮", "飞轮"])
        mentions_adcs = _contains_any(text, ["adcs", "姿控", "姿态控制", "指向", "指向控制", "pointing", "attitude", "姿态误差", "指向误差"])
        mentions_adcs_closed_loop = _contains_any(text, ["closed-loop", "closed loop", "闭环", "闭环姿控", "闭环姿态控制", "detumble", "消旋", "nadir", "对地指向", "sun pointing", "太阳指向", "quaternion", "四元数", "gyro proxy", "简单陀螺", "陀螺代理"])
        mentions_thermal = _contains_any(text, ["thermal", "热控", "热轨", "温度", "散热", "散热器", "radiator", "heater", "加热器", "电池温度", "热平衡"])
        mentions_comm = _contains_any(text, ["comm", "communication", "通信", "下行", "downlink", "data backlog", "数据积压", "地面站通信", "ground pass"])
        mentions_comm_mission = _contains_any(text, ["通信", "地面站通信", "ground pass", "data backlog", "数据积压", "backlog", "地面站", "可见性", "ground station"])
        mentions_data_queue = _contains_any(text, ["data queue", "queue bits", "数据队列", "数据缓存", "队列", "缓存积压", "dropped bits", "丢弃数据"])
        mentions_onboard_storage = _contains_any(text, ["onboard storage", "stored bits", "data storage", "星上存储", "载荷存储", "存储容量", "存储溢出", "overflow bits"])
        mentions_ground_station = _contains_any(text, ["ground station", "地面站", "ground access", "过境", "仰角", "可见性"])
        mentions_antenna = _contains_any(text, ["antenna", "天线", "boresight", "离轴", "指向损失", "antenna gain"])
        mentions_transmitter = _contains_any(text, ["transmitter", "发射机", "tx power", "发射功率", "功放", "effective rate"])
        mentions_link_budget = _contains_any(text, ["link budget", "link-budget", "链路预算", "ebn0", "ber", "链路裕度"])
        mentions_power_sink = _contains_any(text, ["power sink", "用电负载", "功耗负载", "simple load"])
        mentions_heater = _contains_any(text, ["heater", "加热器", "恒温器", "heater power"])
        mentions_radiator = _contains_any(text, ["radiator", "散热器", "heat rejection", "热排散"])
        mentions_thermal_node = _contains_any(text, ["thermal node", "热节点", "温度节点", "thermal profile"])
        mentions_payload_sensor = _contains_any(text, ["payload sensor", "载荷传感器", "观测质量", "cloud fraction", "有效观测"])
        mentions_payload = _contains_any(text, ["payload instrument", "payload", "载荷", "成像载荷", "payload power"])
        mentions_propulsion = _contains_any(text, [
            "propulsion", "propellant", "fuel tank", "fuel pressure", "fuel remaining",
            "thruster", "burn", "ignition", "推进", "推进剂", "燃料箱", "燃料压力",
            "燃料消耗", "推进剂消耗", "推进器", "推力器", "点火", "喷气", "剩余燃料",
        ])
        mentions_orbit = _contains_any(text, ["orbit", "leo", "轨道", "环境", "磁场", "地面站", "ground station"])
        mentions_whole = _contains_any(text, ["whole spacecraft", "spacecraft", "整星", "全星", "星上", "power orbit", "power-orbit", "电源轨道", "轨道电源"])
        mentions_coupled = _contains_any(text, ["coupled", "coupling", "耦合", "联合", "联动", "任务级", "mission coupling", "mission-coupled", "route-b", "route b"])
        mentions_power_thermal_orbit_coupled = mentions_coupled and (mentions_thermal and mentions_eps and mentions_orbit)
        mentions_comm_payload_coupled = mentions_coupled and (mentions_comm and ("payload" in text or "载荷" in text or "storage" in text or "存储" in text or "data" in text or "数据" in text))
        mentions_maneuver_orbit_attitude = mentions_propulsion and (mentions_orbit or mentions_whole or _contains_any(text, ["orbit perturbation", "轨道扰动"])) and (_contains_any(text, ["maneuver", "delta-v", "delta v", "dv", "burn", "finite burn", "机动", "变轨", "轨控", "点火", "喷气", "速度增量", "轨道姿态耦合", "推进轨道姿态", "姿态扰动", "proxy", "代理"]) or mentions_coupled or mentions_whole)
        mentions_orbit_adcs_integration = (mentions_orbit and mentions_adcs and (mentions_coupled or _contains_any(text, ["orbit adcs", "orbit-adcs", "轨道姿控", "轨道姿态", "lvlh", "坐标系一致", "frame consistency", "nadir pointing from orbit", "sun pointing from orbit", "对地指向轨道", "太阳指向轨道"]))) and not mentions_maneuver_orbit_attitude
        mentions_medium_orbit = _contains_any(text, [
            "medium fidelity", "medium-fidelity", "medium fidelity orbit", "中等保真", "中保真",
            "j2", "j2摄动", "j2 摄动", "secular", "半长轴", "偏心率", "eccentricity",
            "analytic sun", "太阳向量", "日食几何", "eclipse geometry", "umbra", "penumbra", "本影", "半影",
        ])
        wants_campaign = _contains_any(text, ["campaign", "批量", "扫描", "sweep", "grid", "蒙特卡洛", "monte carlo"])
        mentions_source_native = _contains_any(text, ["source-native", "source native", "源码", "原生", "原 src", "原src", "source"] )
        mentions_unified_runtime = _contains_any(text, ["统一运行图", "统一 basilisk", "basilisk unified", "unified native", "同一 process", "同一task", "同一 task", "消息耦合", "原生耦合"])
        mentions_composite_full = (
            _contains_any(text, ["全分系统", "全分系统整星", "所有分系统", "六大分系统", "六分系统", "六系统", "完整六分系统", "完整整星", "完整全星", "全系统整星", "整星全链路", "整星数字样机", "full spacecraft", "all subsystems", "six subsystems", "end-to-end spacecraft"])
            and (mentions_whole or sum(bool(item) for item in (mentions_orbit, mentions_adcs, mentions_eps, mentions_thermal, mentions_comm, mentions_payload)) >= 3)
        ) or all((mentions_orbit, mentions_adcs, mentions_eps, mentions_thermal, mentions_comm, mentions_payload))

        if mentions_unified_runtime and mentions_whole and not mentions_propulsion:
            return CapabilityTemplateSelection("whole_spacecraft_unified_native_nominal.yaml", "whole_spacecraft.unified_native.v1", "recommended whole-spacecraft unified Basilisk runtime keywords")
        if mentions_unified_runtime and mentions_adcs and not mentions_whole:
            return CapabilityTemplateSelection("subsystem_adcs_unified_native_nominal.yaml", "subsystem.adcs_unified_native.v1", "recommended ADCS unified Basilisk runtime keywords")

        # Campaign templates are included for script-generation of datasets, but
        # A1's primary product is a single capability-python script.  Prefer
        # single TaskSpecs unless the user explicitly asks for a sweep.
        if wants_campaign:
            if mentions_composite_full:
                if is_fault and is_degradation:
                    return CapabilityTemplateSelection("whole_spacecraft_composite_digital_twin_mixed.yaml", "whole_spacecraft.composite_digital_twin.v1", "campaign request uses V37-B composite mixed template fallback")
                if is_fault:
                    return CapabilityTemplateSelection("whole_spacecraft_composite_digital_twin_fault.yaml", "whole_spacecraft.composite_digital_twin.v1", "campaign request uses V37-B composite runtime-fault template fallback")
                if is_degradation:
                    return CapabilityTemplateSelection("whole_spacecraft_composite_digital_twin_degradation.yaml", "whole_spacecraft.composite_digital_twin.v1", "campaign request uses V37-B composite degradation template fallback")
                return CapabilityTemplateSelection("whole_spacecraft_composite_digital_twin_nominal.yaml", "whole_spacecraft.composite_digital_twin.v1", "campaign request uses V37-B composite nominal template fallback")
            if mentions_data_queue:
                return CapabilityTemplateSelection("campaign_component_data_storage_capability_sweep.yaml", "component.data_queue.v1", "campaign + data-queue keywords")
            if mentions_onboard_storage:
                return CapabilityTemplateSelection("campaign_component_onboard_storage_capability_sweep.yaml", "component.onboard_storage.v1", "campaign + onboard-storage keywords")
            if mentions_power_thermal_orbit_coupled:
                return CapabilityTemplateSelection("whole_spacecraft_power_thermal_orbit_coupled_nominal.yaml", "whole_spacecraft.power_thermal_orbit_coupled.v1", "campaign request uses HF-5 coupled template fallback")
            if mentions_comm_payload_coupled:
                return CapabilityTemplateSelection("whole_spacecraft_comm_payload_mission_coupled_nominal.yaml", "whole_spacecraft.comm_payload_mission_coupled.v1", "campaign request uses HF-6 coupled template fallback")
            if mentions_maneuver_orbit_attitude:
                return CapabilityTemplateSelection("whole_spacecraft_maneuver_orbit_attitude_nominal.yaml", "whole_spacecraft.maneuver_orbit_attitude.v1", "campaign request uses HF-7 maneuver-coupled template fallback")
            if mentions_orbit_adcs_integration:
                return CapabilityTemplateSelection("whole_spacecraft_orbit_adcs_fidelity_nominal.yaml", "whole_spacecraft.orbit_adcs_fidelity.v1", "campaign request uses INT-1 orbit+ADCS integration template fallback")
            if mentions_thermal and (mentions_whole or (mentions_eps and mentions_orbit)):
                return CapabilityTemplateSelection("campaign_whole_spacecraft_basic_power_thermal_orbit_capability_sweep.yaml", "whole_spacecraft.basic_power_thermal_orbit.v1", "campaign + whole-spacecraft power-thermal-orbit keywords")
            if mentions_adcs and (mentions_whole or (mentions_eps and mentions_orbit)):
                return CapabilityTemplateSelection("campaign_whole_spacecraft_basic_power_attitude_orbit_capability_sweep.yaml", "whole_spacecraft.basic_power_attitude_orbit.v1", "campaign + whole-spacecraft power-attitude-orbit keywords")
            if mentions_whole or (mentions_eps and mentions_orbit):
                return CapabilityTemplateSelection("campaign_whole_spacecraft_basic_power_orbit_capability_sweep.yaml", "whole_spacecraft.basic_power_orbit.v1", "campaign + whole-spacecraft power-orbit keywords")
            if mentions_solar:
                return CapabilityTemplateSelection("campaign_component_solar_panel_capability_shadow_sweep.yaml", "component.solar_panel.v1", "campaign + solar/shadow keywords")
            if mentions_adcs:
                return CapabilityTemplateSelection("campaign_subsystem_adcs_basic_rw_pointing_capability_sweep.yaml", "subsystem.adcs.basic_rw_pointing.v1", "campaign + ADCS pointing keywords")
            for flag, template, cap, reason in [
                (mentions_ground_station, "campaign_component_ground_station_capability_sweep.yaml", "component.ground_station.v1", "campaign + ground-station keywords"),
                (mentions_antenna, "campaign_component_antenna_capability_sweep.yaml", "component.antenna.v1", "campaign + antenna keywords"),
                (mentions_transmitter, "campaign_component_transmitter_capability_sweep.yaml", "component.transmitter.v1", "campaign + transmitter keywords"),
                (mentions_link_budget and not mentions_comm_mission, "campaign_component_link_budget_capability_sweep.yaml", "component.link_budget.v1", "campaign + link-budget keywords"),
                (mentions_power_sink, "campaign_component_power_sink_capability_sweep.yaml", "component.power_sink.v1", "campaign + power-sink keywords"),
                (mentions_heater, "campaign_component_heater_capability_sweep.yaml", "component.heater.v1", "campaign + heater keywords"),
                (mentions_radiator, "campaign_component_radiator_capability_sweep.yaml", "component.radiator.v1", "campaign + radiator keywords"),
                (mentions_thermal_node, "campaign_component_thermal_node_capability_sweep.yaml", "component.thermal_node.v1", "campaign + thermal-node keywords"),
                (mentions_payload_sensor, "campaign_component_payload_sensor_capability_sweep.yaml", "component.payload_sensor.v1", "campaign + payload-sensor keywords"),
                (mentions_payload, "campaign_component_payload_capability_sweep.yaml", "component.payload.v1", "campaign + payload keywords"),
                (mentions_propulsion, "campaign_subsystem_propulsion_source_native_v1_sweep.yaml", "subsystem.propulsion.source_native.v1", "campaign + propulsion/fuel keywords"),
            ]:
                if flag:
                    return CapabilityTemplateSelection(template, cap, reason)
            if mentions_thermal:
                return CapabilityTemplateSelection("campaign_subsystem_thermal_basic_lumped_capability_sweep.yaml", "subsystem.thermal.basic_lumped.v1", "campaign + thermal keywords")
            if mentions_comm:
                return CapabilityTemplateSelection("campaign_subsystem_comm_basic_ground_pass_capability_sweep.yaml", "subsystem.comm.basic_ground_pass.v1", "campaign + communication/downlink keywords")
            if mentions_rw:
                return CapabilityTemplateSelection("campaign_component_reaction_wheel_capability_fault_sweep.yaml", "component.reaction_wheel.v1", "campaign + reaction wheel keywords")
            if mentions_orbit:
                return CapabilityTemplateSelection("campaign_orbit_environment_capability_grid.yaml", "orbit_environment.leo_simple.v1", "campaign + orbit keywords")
            if mentions_eps:
                return CapabilityTemplateSelection("campaign_subsystem_eps_basic_capability_eclipse_sweep.yaml", "subsystem.eps.basic.v1", "campaign + EPS/eclipse keywords")
            return CapabilityTemplateSelection("campaign_component_battery_capability_fault_sweep.yaml", "component.battery.v1", "campaign fallback")

        if mentions_composite_full:
            if is_fault and is_degradation:
                return CapabilityTemplateSelection("whole_spacecraft_composite_digital_twin_mixed.yaml", "whole_spacecraft.composite_digital_twin.v1", "V37-B full-subsystem mixed fault/degradation keywords")
            if is_fault:
                return CapabilityTemplateSelection("whole_spacecraft_composite_digital_twin_fault.yaml", "whole_spacecraft.composite_digital_twin.v1", "V37-B full-subsystem runtime-fault keywords")
            if is_degradation:
                return CapabilityTemplateSelection("whole_spacecraft_composite_digital_twin_degradation.yaml", "whole_spacecraft.composite_digital_twin.v1", "V37-B full-subsystem degradation keywords")
            return CapabilityTemplateSelection("whole_spacecraft_composite_digital_twin_nominal.yaml", "whole_spacecraft.composite_digital_twin.v1", "V37-B full-subsystem nominal keywords")
        if mentions_source_native and mentions_payload and not mentions_whole:
            return CapabilityTemplateSelection("subsystem_payload_source_native_v1_nominal.yaml", "subsystem.payload.source_native.v1", "source-native payload keywords")
        if mentions_source_native and mentions_comm and not mentions_whole:
            return CapabilityTemplateSelection("subsystem_comm_data_source_native_v1_nominal.yaml", "subsystem.comm_data.source_native.v1", "source-native comm-data keywords")
        if mentions_source_native and mentions_thermal and not mentions_whole:
            return CapabilityTemplateSelection("subsystem_thermal_source_native_v1_nominal.yaml", "subsystem.thermal.source_native.v1", "source-native thermal keywords")
        if mentions_source_native and mentions_eps and not mentions_whole:
            return CapabilityTemplateSelection("subsystem_eps_source_native_v1_nominal.yaml", "subsystem.eps.source_native.v1", "source-native EPS keywords")
        if mentions_power_thermal_orbit_coupled:
            return CapabilityTemplateSelection("whole_spacecraft_power_thermal_orbit_coupled_nominal.yaml", "whole_spacecraft.power_thermal_orbit_coupled.v1", "HF-5 power/thermal/orbit coupled keywords")
        if mentions_comm_payload_coupled:
            return CapabilityTemplateSelection("whole_spacecraft_comm_payload_mission_coupled_nominal.yaml", "whole_spacecraft.comm_payload_mission_coupled.v1", "HF-6 comm/payload mission-coupled keywords")
        if mentions_maneuver_orbit_attitude:
            return CapabilityTemplateSelection("whole_spacecraft_maneuver_orbit_attitude_nominal.yaml", "whole_spacecraft.maneuver_orbit_attitude.v1", "HF-7 propulsion/orbit/attitude coupled maneuver keywords")
        if mentions_orbit_adcs_integration:
            return CapabilityTemplateSelection("whole_spacecraft_orbit_adcs_fidelity_nominal.yaml", "whole_spacecraft.orbit_adcs_fidelity.v1", "INT-1 orbit+ADCS integration keywords")
        if mentions_orbit and mentions_medium_orbit:
            return CapabilityTemplateSelection("orbit_environment_orbit_fidelity_nominal.yaml", "orbit_environment.orbit_fidelity.v1", "orbit fidelity orbit/environment keywords")
        if mentions_data_queue:
            return CapabilityTemplateSelection("component_data_queue_capability_nominal.yaml", "component.data_queue.v1", "data-queue component keywords")
        if mentions_onboard_storage:
            return CapabilityTemplateSelection("component_onboard_storage_capability_nominal.yaml", "component.onboard_storage.v1", "onboard-storage component keywords")
        if mentions_thermal and (mentions_whole or (mentions_eps and mentions_orbit)):
            return CapabilityTemplateSelection("whole_spacecraft_unified_native_nominal.yaml", "whole_spacecraft.unified_native.v1", "recommended unified whole-spacecraft thermal/power/orbit path")
        if mentions_adcs and (mentions_whole or (mentions_eps and mentions_orbit)):
            return CapabilityTemplateSelection("whole_spacecraft_unified_native_nominal.yaml", "whole_spacecraft.unified_native.v1", "recommended unified whole-spacecraft ADCS/power/orbit path")
        if mentions_whole or (mentions_eps and mentions_orbit):
            return CapabilityTemplateSelection("whole_spacecraft_unified_native_nominal.yaml", "whole_spacecraft.unified_native.v1", "recommended unified whole-spacecraft path")
        if mentions_adcs_closed_loop and mentions_adcs and not (mentions_eps or mentions_whole):
            return CapabilityTemplateSelection("subsystem_adcs_unified_native_nominal.yaml", "subsystem.adcs_unified_native.v1", "recommended unified ADCS closed-loop path")
        if mentions_adcs and not (mentions_eps or mentions_whole):
            return CapabilityTemplateSelection("subsystem_adcs_unified_native_nominal.yaml", "subsystem.adcs_unified_native.v1", "recommended unified ADCS path")
        if mentions_orbit and mentions_medium_orbit:
            return CapabilityTemplateSelection("orbit_environment_orbit_fidelity_nominal.yaml", "orbit_environment.orbit_fidelity.v1", "orbit fidelity orbit/environment keywords")
        for flag, template, cap, reason in [
            (mentions_ground_station and not mentions_comm, "component_ground_station_capability_nominal.yaml", "component.ground_station.v1", "ground-station component keywords"),
            (mentions_antenna, "component_antenna_capability_nominal.yaml", "component.antenna.v1", "antenna component keywords"),
            (mentions_transmitter and not mentions_comm, "component_transmitter_capability_nominal.yaml", "component.transmitter.v1", "transmitter component keywords"),
            (mentions_link_budget and not mentions_comm_mission, "component_link_budget_capability_nominal.yaml", "component.link_budget.v1", "link-budget component keywords"),
            (mentions_power_sink, "component_power_sink_capability_nominal.yaml", "component.power_sink.v1", "power-sink component keywords"),
            (mentions_heater and not mentions_thermal, "component_heater_capability_nominal.yaml", "component.heater.v1", "heater component keywords"),
            (mentions_radiator and not mentions_thermal, "component_radiator_capability_nominal.yaml", "component.radiator.v1", "radiator component keywords"),
            (mentions_thermal_node and not mentions_thermal, "component_thermal_node_capability_nominal.yaml", "component.thermal_node.v1", "thermal-node component keywords"),
            (mentions_payload_sensor, "component_payload_sensor_capability_nominal.yaml", "component.payload_sensor.v1", "payload-sensor component keywords"),
            (mentions_payload, "component_payload_capability_nominal.yaml", "component.payload.v1", "payload component keywords"),
            (mentions_propulsion, "subsystem_propulsion_source_native_v1_nominal.yaml", "subsystem.propulsion.source_native.v1", "propulsion/fuel source-native subsystem keywords"),
        ]:
            if flag:
                return CapabilityTemplateSelection(template, cap, reason)
        if mentions_comm:
            return CapabilityTemplateSelection("subsystem_comm_basic_ground_pass_capability_nominal.yaml", "subsystem.comm.basic_ground_pass.v1", "communication/downlink ground-pass keywords")
        if mentions_thermal and not (mentions_eps or mentions_adcs):
            return CapabilityTemplateSelection("subsystem_thermal_basic_lumped_capability_nominal.yaml", "subsystem.thermal.basic_lumped.v1", "thermal nominal keywords")
        if mentions_eps:
            if is_fault:
                return CapabilityTemplateSelection("subsystem_eps_basic_capability_fault.yaml", "subsystem.eps.basic.v1", "EPS + fault keywords")
            if is_degradation:
                return CapabilityTemplateSelection("subsystem_eps_basic_capability_degradation.yaml", "subsystem.eps.basic.v1", "EPS + degradation keywords")
            if _contains_any(text, ["pdu", "甩载", "load shedding"]):
                return CapabilityTemplateSelection("subsystem_eps_basic_capability_pdu_channel_shedding.yaml", "subsystem.eps.basic.v1", "EPS + PDU/load-shedding keywords")
            if _contains_any(text, ["eclipse", "日食", "shadow", "阴影"]):
                return CapabilityTemplateSelection("subsystem_eps_basic_capability_eclipse.yaml", "subsystem.eps.basic.v1", "EPS + eclipse keywords")
            return CapabilityTemplateSelection("subsystem_eps_basic_capability_nominal.yaml", "subsystem.eps.basic.v1", "EPS nominal keywords")
        if mentions_solar:
            if is_fault:
                return CapabilityTemplateSelection("component_solar_panel_capability_fault.yaml", "component.solar_panel.v1", "solar fault keywords")
            if is_degradation:
                return CapabilityTemplateSelection("component_solar_panel_capability_degradation.yaml", "component.solar_panel.v1", "solar degradation keywords")
            return CapabilityTemplateSelection("component_solar_panel_capability_nominal.yaml", "component.solar_panel.v1", "solar nominal keywords")
        if mentions_rw:
            if is_fault:
                return CapabilityTemplateSelection("component_reaction_wheel_capability_fault.yaml", "component.reaction_wheel.v1", "reaction wheel fault keywords")
            if is_degradation:
                return CapabilityTemplateSelection("component_reaction_wheel_capability_degradation.yaml", "component.reaction_wheel.v1", "reaction wheel degradation keywords")
            return CapabilityTemplateSelection("component_reaction_wheel_capability_nominal.yaml", "component.reaction_wheel.v1", "reaction wheel nominal keywords")
        if mentions_orbit:
            return CapabilityTemplateSelection("orbit_environment_capability_leo_nominal.yaml", "orbit_environment.leo_simple.v1", "orbit/environment keywords")
        if mentions_battery or is_fault:
            if is_fault:
                return CapabilityTemplateSelection("component_battery_capability_fault.yaml", "component.battery.v1", "battery fault keywords")
            if is_degradation:
                return CapabilityTemplateSelection("component_battery_capability_degradation.yaml", "component.battery.v1", "battery degradation keywords")
            return CapabilityTemplateSelection("component_battery_capability_nominal.yaml", "component.battery.v1", "battery nominal keywords")
        return CapabilityTemplateSelection("subsystem_eps_basic_capability_nominal.yaml", "subsystem.eps.basic.v1", "default capability fallback")

    def draft_task_spec(self, *, request: CapabilityAgentRequest, context: Mapping[str, Any]) -> dict[str, Any]:
        selection = self.select_template(request.request, context)
        selection = _selection_replacement_for_allowed(selection, request.allowed_capabilities)
        try:
            spec = copy.deepcopy(_load_template(selection.template_file, request.examples_dir))
        except FileNotFoundError:
            spec = _synthesize_template(selection.capability_id, selection.template_file)
        _apply_capability_request_overrides(spec, request, base_template=selection.template_file, selection_reason=selection.reason)
        return spec


def _effect_mode_from_spec(spec: Mapping[str, Any]) -> str:
    """Infer the requested health mode from both legacy and modern effect fields."""

    modifiers = spec.get("modifiers") if isinstance(spec.get("modifiers"), Mapping) else {}
    has_faults = bool(spec.get("faults")) or bool(modifiers.get("faults"))
    has_degradations = bool(spec.get("degradations")) or bool(modifiers.get("degradations"))
    has_constraints = bool(modifiers.get("constraints"))
    category_count = sum(bool(item) for item in (has_faults, has_degradations, has_constraints))
    if category_count > 1:
        return "mixed"
    if has_faults:
        return "fault"
    if has_degradations:
        return "degradation"
    if has_constraints:
        return "constraint"
    return "nominal"


def _effect_time_s(text: str, keywords: Sequence[str], *, default: float) -> float:
    """Extract the time associated with a specific effect phrase.

    Unlike the legacy global onset parser, this keeps independent event times
    for requests such as ``20秒载荷关机，30秒通信中断``.
    """

    lower = text.lower()
    for keyword in keywords:
        pos = lower.find(keyword.lower())
        if pos < 0:
            continue
        before = text[max(0, pos - 24):pos]
        matches = list(re.finditer(r"(\d+(?:\.\d+)?)\s*(?:秒|s|sec(?:ond)?s?)(?:时|后)?\s*(?:开始|发生|注入|进入|at)?\s*$", before, flags=re.IGNORECASE))
        if matches:
            return float(matches[-1].group(1))
        after = text[pos + len(keyword):pos + len(keyword) + 48]
        after = re.split(r"[，,。；;]", after, maxsplit=1)[0]
        match = re.search(r"(?:在|于|at)\s*(\d+(?:\.\d+)?)\s*(?:秒|s|sec(?:ond)?s?)(?:时)?", after, flags=re.IGNORECASE)
        if match:
            return float(match.group(1))
    return float(default)


def _effect_duration_s(text: str, keywords: Sequence[str]) -> float:
    lower = text.lower()
    for keyword in keywords:
        pos = lower.find(keyword.lower())
        if pos < 0:
            continue
        window = text[pos:pos + len(keyword) + 50]
        match = re.search(r"(?:持续|维持|for)\s*(\d+(?:\.\d+)?)\s*(?:秒|s|sec(?:ond)?s?)", window, flags=re.IGNORECASE)
        if match:
            return float(match.group(1))
    return -1.0


def _effect_recovery_time_s(text: str, keywords: Sequence[str]) -> float | None:
    """Extract an absolute recovery time following an effect phrase."""

    lower = text.lower()
    for keyword in keywords:
        pos = lower.find(keyword.lower())
        if pos < 0:
            continue
        window = text[pos:pos + len(keyword) + 90]
        match = re.search(
            r"(\d+(?:\.\d+)?)\s*(?:秒|s|sec(?:ond)?s?)\s*(?:后)?(?:恢复|解除|结束|recover(?:y|ed)?)",
            window,
            flags=re.IGNORECASE,
        )
        if match:
            return float(match.group(1))
    return None


def _requested_output_fields(text: str) -> list[str]:
    catalog: tuple[tuple[tuple[str, ...], str], ...] = (
        (("姿态误差", "指向误差", "attitude error", "pointing error"), "attitude_error_deg"),
        (("电池soc", "电池 soc", "荷电状态", "battery soc"), "battery_soc"),
        (("温度", "热控温度", "thermal temperature"), "thermal_temp_c"),
        (("载荷数据率", "载荷生成率", "payload data rate", "payload generated"), "payload_generated_bps"),
        (("下行数据率", "下行速率", "downlink rate", "downlink delivered"), "downlink_delivered_bps"),
        (("存储量", "数据存储", "storage bits", "data storage"), "data_storage_bits"),
        (("推进剂余量", "剩余燃料", "propellant remaining"), "propellant_remaining_kg"),
        (("任务成功率", "任务成功度", "任务成功评分", "任务成功分数", "mission success", "mission score"), "mission_success_score"),
        (("能量守恒", "energy conservation"), "energy_conservation_status"),
        (("数据守恒", "data conservation"), "data_conservation_status"),
        (("耦合状态", "代理耦合", "跨系统耦合", "coupling status", "proxy coupling"), "proxy_coupling_runtime_status"),
    )
    return [field for keywords, field in catalog if _contains_any(text, keywords)]


def _contextualize_requested_output_fields(capability_id: str, fields: Sequence[str]) -> list[str]:
    """Map legacy natural-language aliases to fields declared by a capability.

    The global aliases remain useful for whole-spacecraft requests, while a
    subsystem must request the subsystem's authoritative observable field.
    """

    aliases: dict[str, dict[str, str]] = {
        "subsystem.eps.basic.v1": {
            "battery_soc": "eps.battery.soc",
        },
        "subsystem.thermal.basic_lumped.v1": {
            "thermal_temp_c": "thermal.node.bus_temp_c",
        },
    }
    mapping = aliases.get(capability_id, {})
    return list(dict.fromkeys(mapping.get(field, field) for field in fields))


def _composite_effect_overrides(text: str, *, onset_s: float | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Map explicit whole-spacecraft effect language to registered V37-B routes.

    The returned boolean distinguishes an explicit recognized request from a
    vague ``fault``/``degradation`` keyword.  Recognized requests replace the
    demonstration effect carried by the selected example template.
    """

    default_onset = float(onset_s) if onset_s is not None else 0.0
    fault_specs: list[dict[str, Any]] = []
    degradation_specs: list[dict[str, Any]] = []

    fault_catalog: tuple[tuple[tuple[str, ...], str, str, str], ...] = (
        (("reaction wheel jamming", "rw jamming", "反作用轮卡滞", "飞轮卡滞"), "adcs.reaction_wheel", "jamming", "adcs_rw_jamming"),
        (("bearing seizure", "rw seizure", "轴承抱死", "飞轮抱死"), "adcs.reaction_wheel", "bearing_seizure", "adcs_rw_bearing_seizure"),
        (("battery capacity loss", "电池容量损失", "电池容量下降故障"), "eps.battery", "capacity_drop", "eps_battery_capacity_loss"),
        (("battery open circuit", "open-circuit battery", "电池开路"), "eps.battery", "open_circuit", "eps_battery_open_circuit"),
        (("thruster ignition failure", "ignition failure", "推进器点火失败", "点火失败"), "propulsion.thruster", "ignition_failure", "propulsion_thruster_ignition_failure"),
        (("payload instrument off", "payload off", "载荷关闭", "载荷关机", "载荷失效", "载荷断电"), "payload.instrument", "instrument_off", "payload_instrument_off"),
        (("downlink link loss", "link loss", "transmitter outage", "下行中断", "链路丢失", "通信中断"), "comm.transmitter", "transmitter_outage", "comm_data_downlink_link_loss"),
        (("storage capacity loss", "存储容量损失", "数管存储故障"), "comm.storage", "storage_capacity_loss", "comm_data_storage_capacity_loss"),
        (("heater stuck off", "heater failure", "加热器卡关", "加热器失效"), "thermal.heater", "heater_stuck_off", "thermal_heater_stuck_off"),
        (("heater stuck on", "加热器常开", "加热器卡开"), "thermal.heater", "heater_stuck_on", "thermal_heater_stuck_on"),
        (("radiator rejection loss", "radiator efficiency loss", "散热器散热能力下降", "散热器失效"), "thermal.radiator", "radiator_rejection_loss", "thermal_radiator_rejection_loss"),
    )
    for keywords, target, fault_type, scenario in fault_catalog:
        if _contains_any(text, keywords):
            event_onset = _effect_time_s(text, keywords, default=default_onset)
            event_duration = _effect_duration_s(text, keywords)
            fault_specs.append({
                "modifier_id": f"nl_{scenario}",
                "target": target,
                "fault_type": fault_type,
                "onset_time_s": event_onset,
                "duration_s": event_duration,
                "severity": 1.0,
                "parameters": {"scenario": scenario},
            })

    degradation_catalog: tuple[tuple[tuple[str, ...], str, str], ...] = (
        (("solar panel efficiency loss", "solar array efficiency loss", "太阳阵列效率下降", "太阳帆板效率下降", "太阳能板效率下降"), "eps.solar_panel", "solar_panel_efficiency_loss_20pct"),
        (("multi subsystem end of life", "whole spacecraft end of life", "整星寿命末期", "多分系统寿命末期", "全系统退化"), "whole_spacecraft", "multi_subsystem_end_of_life"),
        (("adcs end of life", "adcs寿命末期", "姿控寿命末期"), "adcs.reaction_wheel", "adcs_rw_friction_and_sensor_noise"),
        (("thermal end of life", "热控寿命末期"), "thermal.radiator", "thermal_heater_and_radiator_degradation"),
        (("propulsion end of life", "推进寿命末期"), "propulsion.fuel_tank", "fuel_leak_and_pressure_loss"),
    )
    for keywords, target, scenario in degradation_catalog:
        if _contains_any(text, keywords):
            event_onset = _effect_time_s(text, keywords, default=0.0)
            event_duration = _effect_duration_s(text, keywords)
            recovery_time = _effect_recovery_time_s(text, keywords)
            if event_duration < 0.0 and recovery_time is not None and recovery_time >= event_onset:
                event_duration = recovery_time - event_onset
            degradation_specs.append({
                "modifier_id": f"nl_{scenario}",
                "target": target,
                "degradation_type": scenario,
                "onset_time_s": event_onset,
                "duration_s": event_duration,
                "severity": 1.0,
                "parameters": {"scenario": scenario},
            })

    return fault_specs, degradation_specs, bool(fault_specs or degradation_specs)


def _apply_capability_request_overrides(spec: dict[str, Any], request: CapabilityAgentRequest, *, base_template: str, selection_reason: str) -> None:
    text = request.request
    cap_id = str(spec.get("capability_id", ""))
    req_hash = _short_hash(text)
    base_id = str(spec.get("task_id") or Path(base_template).stem)
    task_id = request.task_id or f"gen_{_slugify(base_id, max_len=52)}_{req_hash}"
    spec["schema_version"] = TASK_SPEC_VERSION
    spec["task_id"] = task_id
    spec.setdefault("tags", [])
    if isinstance(spec["tags"], list) and "agent_generated" not in spec["tags"]:
        spec["tags"].append("agent_generated")
    spec.setdefault("metadata", {})
    if isinstance(spec["metadata"], dict):
        spec["metadata"].setdefault("agent", {})
        spec["metadata"]["agent"].update({
            "generated_by": _agent_version_for_backend(request.backend),
            "backend": request.backend,
            "request_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "request_excerpt": text[:240],
            "base_template": base_template,
            "selection_reason": selection_reason,
        })

    duration_s = _extract_seconds(text, ["duration", "simulate", "simulation", "仿真", "时长", "运行"]) or _extract_seconds(text, ["持续"])
    sample_s = _extract_seconds(text, ["sample", "sampling", "采样", "输出间隔", "步长", "间隔"])
    onset_s = _extract_seconds(text, ["onset", "fault at", "故障在", "发生", "注入", "开始"])
    eclipse_duration_s = _extract_seconds(text, ["eclipse duration", "日食持续", "日食段", "阴影持续"])
    magnitude = _extract_first_float(text, ["magnitude", "severity", "幅值", "严重度", "强度"], min_value=0.0, max_value=1.0)
    initial_soc = _extract_first_float(text, ["initial soc", "initial SOC", "soc", "初始soc", "初始 SOC", "初始电量", "荷电状态"], min_value=0.0, max_value=1.0)
    battery_capacity = _extract_first_float(text, ["battery capacity", "capacity", "电池容量", "容量"], min_value=1.0)
    solar_power = _extract_first_float(text, ["solar power", "solar array", "太阳阵列功率", "太阳功率"], min_value=0.0)
    payload_power = _extract_first_float(text, ["payload power", "payload load", "载荷功率", "载荷负载"], min_value=0.0)
    bus_load = _extract_first_float(text, ["bus load", "bus power", "平台功率", "母线负载"], min_value=0.0)
    altitude_m = _extract_distance_m(text, ["altitude", "高度", "轨道高度"])
    pointing_error_deg = _extract_first_float(text, ["pointing error", "attitude error", "姿态误差", "指向误差", "初始误差"], min_value=0.0)
    control_kp = _extract_first_float(text, ["control gain", "控制增益", "kp"], min_value=0.0)
    torque_limit = _extract_first_float(text, ["torque limit", "max torque", "力矩上限", "最大力矩", "扭矩上限"], min_value=0.0)
    internal_power = _extract_first_float(text, ["internal power", "heat power", "internal heat", "内部功耗", "内热", "热耗散", "功耗"], min_value=0.0)
    solar_heat = _extract_first_float(text, ["solar heat", "太阳热", "外部热", "热输入"], min_value=0.0)
    radiator_area = _extract_first_float(text, ["radiator area", "散热器面积", "散热面积"], min_value=0.0)
    heater_setpoint = _extract_first_float(text, ["heater setpoint", "加热器设定", "加热器阈值", "heater threshold"], min_value=-100.0)
    generated_bps = _extract_first_float(text, ["generated bps", "data generation", "generation rate", "数据生成", "数据产生", "生成速率"], min_value=0.0)
    data_capacity_bits = _extract_bits_value(text, ["queue capacity", "buffer capacity", "storage capacity", "队列容量", "缓存容量", "存储容量", "容量"])
    downlink_bps = _extract_first_float(text, ["downlink bps", "downlink", "downlink rate", "下行", "下行速率", "下载速率"], min_value=0.0)
    raw_rate_bps = _extract_first_float(text, ["raw rate", "downlink rate", "baud", "下行速率", "链路速率", "码率"], min_value=1.0)
    tx_power_w = _extract_first_float(text, ["tx power", "transmit power", "发射功率", "发射机功率"], min_value=0.0)
    min_elevation_deg = _extract_first_float(text, ["min elevation", "minimum elevation", "最低仰角", "最小仰角"], min_value=-90.0)
    fuel_capacity_kg = _extract_mass_kg(text, ["fuel capacity", "propellant capacity", "tank capacity", "fuel tank capacity", "燃料箱容量", "推进剂容量", "燃料容量"])
    initial_fuel_kg = _extract_mass_kg(text, ["initial fuel", "initial propellant", "propellant remaining", "fuel remaining", "初始燃料", "初始推进剂", "剩余燃料", "推进剂余量"])
    min_soc_for_burn = _extract_ratio_value(text, ["min soc for burn", "minimum soc", "最低soc", "最低荷电", "点火最低电量"])
    burn_requested = True if _contains_any(text, ["burn", "ignition", "点火", "喷气", "工作", "burn requested"]) else None
    battery_soc_for_burn = _extract_ratio_value(text, ["battery soc", "eps soc", "电池soc", "电池荷电", "荷电状态"])
    thrust_n = _extract_first_float(text, ["thrust", "thrust_n", "推力"], min_value=0.0)
    isp_s = _extract_first_float(text, ["specific impulse", "isp", "比冲"], min_value=1.0)
    burn_start_s = _extract_seconds(text, ["burn start", "start burn", "点火开始", "开始点火", "机动开始"])
    burn_duration_s = _extract_seconds(text, ["burn duration", "burn time", "点火持续", "燃烧持续", "机动持续"])

    if duration_s is not None:
        _set_path(spec, "simulation.duration_s", duration_s)
        if spec.get("task_type") == "campaign":
            _set_path(spec, "campaign.base_spec.simulation.duration_s", duration_s)
    if sample_s is not None:
        _set_path(spec, "simulation.sample_s", sample_s)
        if spec.get("task_type") == "campaign":
            _set_path(spec, "campaign.base_spec.simulation.sample_s", sample_s)
    if onset_s is not None and cap_id != "whole_spacecraft.composite_digital_twin.v1":
        for path in ("faults[0].onset_time_s", "campaign.base_spec.faults[0].onset_time_s"):
            try:
                _set_path(spec, path, onset_s)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:02',
                    exception=exc,
                    strict=False,
                )
    if magnitude is not None and cap_id != "whole_spacecraft.composite_digital_twin.v1":
        for path in ("faults[0].magnitude", "campaign.base_spec.faults[0].magnitude"):
            try:
                _set_path(spec, path, magnitude)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:03',
                    exception=exc,
                    strict=False,
                )
    if initial_soc is not None:
        for path in ("parameters.initial_soc", "campaign.base_spec.parameters.initial_soc"):
            try:
                _set_path(spec, path, initial_soc)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:04',
                    exception=exc,
                    strict=False,
                )
    if battery_capacity is not None and (cap_id in {"component.battery.v1", "subsystem.eps.basic.v1", "subsystem.eps.source_native.v1", "whole_spacecraft.basic_power_orbit.v1", "whole_spacecraft.basic_power_attitude_orbit.v1", "whole_spacecraft.basic_power_thermal_orbit.v1", "whole_spacecraft.power_thermal_orbit_coupled.v1", "whole_spacecraft.comm_payload_mission_coupled.v1"} or any(word in text.lower() for word in ["battery", "电池", "eps", "电源"])):
        for path in ("parameters.capacity_wh", "parameters.battery_capacity_wh", "campaign.base_spec.parameters.capacity_wh", "campaign.base_spec.parameters.battery_capacity_wh"):
            try:
                _set_path(spec, path, battery_capacity)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:05',
                    exception=exc,
                    strict=False,
                )
    if solar_power is not None:
        for path in ("parameters.max_power_w", "parameters.solar_array_max_power_w", "campaign.base_spec.parameters.max_power_w", "campaign.base_spec.parameters.solar_array_max_power_w"):
            try:
                _set_path(spec, path, solar_power)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:06',
                    exception=exc,
                    strict=False,
                )
    if payload_power is not None:
        for path in ("parameters.payload_load_power_w", "campaign.base_spec.parameters.payload_load_power_w"):
            try:
                _set_path(spec, path, payload_power)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:07',
                    exception=exc,
                    strict=False,
                )
    if bus_load is not None:
        for path in ("parameters.bus_load_power_w", "campaign.base_spec.parameters.bus_load_power_w"):
            try:
                _set_path(spec, path, bus_load)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:08',
                    exception=exc,
                    strict=False,
                )
    if eclipse_duration_s is not None:
        for path in ("parameters.eclipse_duration_s", "campaign.base_spec.parameters.eclipse_duration_s"):
            try:
                _set_path(spec, path, eclipse_duration_s)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:09',
                    exception=exc,
                    strict=False,
                )
    if altitude_m is not None:
        for path in ("orbit_environment.altitude_m", "campaign.base_spec.orbit_environment.altitude_m"):
            try:
                _set_path(spec, path, altitude_m)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:10',
                    exception=exc,
                    strict=False,
                )
    if pointing_error_deg is not None:
        for path in ("parameters.initial_pointing_error_deg", "campaign.base_spec.parameters.initial_pointing_error_deg"):
            try:
                _set_path(spec, path, pointing_error_deg)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:11',
                    exception=exc,
                    strict=False,
                )
    if control_kp is not None:
        for path in ("parameters.control_kp_nm_per_rad", "campaign.base_spec.parameters.control_kp_nm_per_rad"):
            try:
                _set_path(spec, path, control_kp)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:12',
                    exception=exc,
                    strict=False,
                )
    if torque_limit is not None:
        for path in ("parameters.max_rw_torque_nm", "parameters.max_wheel_torque_nm", "campaign.base_spec.parameters.max_rw_torque_nm", "campaign.base_spec.parameters.max_wheel_torque_nm"):
            try:
                _set_path(spec, path, torque_limit)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:13',
                    exception=exc,
                    strict=False,
                )
    if cap_id == "subsystem.adcs_closed_loop.basic.v1":
        lower_for_mode = text.lower()
        if _contains_any(lower_for_mode, ["detumble", "消旋"]):
            _set_path(spec, "parameters.target_mode", "detumble")
        elif _contains_any(lower_for_mode, ["nadir", "对地", "对地指向"]):
            _set_path(spec, "parameters.target_mode", "nadir")
        elif _contains_any(lower_for_mode, ["sun pointing", "太阳指向", "sun-pointing"]):
            _set_path(spec, "parameters.target_mode", "sun")
    if internal_power is not None:
        for path in ("parameters.internal_power_w", "campaign.base_spec.parameters.internal_power_w"):
            try:
                _set_path(spec, path, internal_power)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:14',
                    exception=exc,
                    strict=False,
                )
    if solar_heat is not None:
        for path in ("parameters.solar_heat_w", "campaign.base_spec.parameters.solar_heat_w"):
            try:
                _set_path(spec, path, solar_heat)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:15',
                    exception=exc,
                    strict=False,
                )
    if radiator_area is not None:
        for path in ("parameters.radiator_area_m2", "campaign.base_spec.parameters.radiator_area_m2"):
            try:
                _set_path(spec, path, radiator_area)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:16',
                    exception=exc,
                    strict=False,
                )
    if heater_setpoint is not None:
        for path in ("parameters.heater_setpoint_c", "campaign.base_spec.parameters.heater_setpoint_c"):
            try:
                _set_path(spec, path, heater_setpoint)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:17',
                    exception=exc,
                    strict=False,
                )

    if generated_bps is not None:
        for path in ("parameters.generated_bps", "parameters.data_generation_rate_bps", "campaign.base_spec.parameters.generated_bps", "campaign.base_spec.parameters.data_generation_rate_bps"):
            try:
                _set_path(spec, path, generated_bps)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:18',
                    exception=exc,
                    strict=False,
                )
    if data_capacity_bits is not None and cap_id in {"component.data_queue.v1", "component.onboard_storage.v1", "subsystem.comm.basic_ground_pass.v1", "subsystem.comm_data.source_native.v1"}:
        for path in ("parameters.capacity_bits", "parameters.storage_capacity_bits", "campaign.base_spec.parameters.capacity_bits", "campaign.base_spec.parameters.storage_capacity_bits"):
            try:
                _set_path(spec, path, data_capacity_bits)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:19',
                    exception=exc,
                    strict=False,
                )
    if downlink_bps is not None:
        for path in ("parameters.downlink_bps", "parameters.downlink_rate_bps", "campaign.base_spec.parameters.downlink_bps", "campaign.base_spec.parameters.downlink_rate_bps"):
            try:
                _set_path(spec, path, downlink_bps)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:20',
                    exception=exc,
                    strict=False,
                )
    if raw_rate_bps is not None:
        for path in ("parameters.raw_rate_bps", "campaign.base_spec.parameters.raw_rate_bps"):
            try:
                _set_path(spec, path, raw_rate_bps)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:21',
                    exception=exc,
                    strict=False,
                )
    if tx_power_w is not None:
        for path in ("parameters.tx_power_w", "campaign.base_spec.parameters.tx_power_w"):
            try:
                _set_path(spec, path, tx_power_w)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:22',
                    exception=exc,
                    strict=False,
                )
    if min_elevation_deg is not None:
        for path in ("parameters.ground_station.min_elevation_deg", "campaign.base_spec.parameters.ground_station.min_elevation_deg"):
            try:
                _set_path(spec, path, min_elevation_deg)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:23',
                    exception=exc,
                    strict=False,
                )
    if fuel_capacity_kg is not None:
        for path in ("parameters.fuel_capacity_kg", "campaign.base_spec.parameters.fuel_capacity_kg"):
            try:
                _set_path(spec, path, fuel_capacity_kg)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:24',
                    exception=exc,
                    strict=False,
                )
    if initial_fuel_kg is not None:
        for path in ("parameters.initial_fuel_kg", "parameters.initial_propellant_kg", "campaign.base_spec.parameters.initial_fuel_kg", "campaign.base_spec.parameters.initial_propellant_kg"):
            try:
                _set_path(spec, path, initial_fuel_kg)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:25',
                    exception=exc,
                    strict=False,
                )
    if min_soc_for_burn is not None:
        for path in ("parameters.min_soc_for_burn", "campaign.base_spec.parameters.min_soc_for_burn"):
            try:
                _set_path(spec, path, min_soc_for_burn)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:26',
                    exception=exc,
                    strict=False,
                )
    if burn_requested is not None:
        for path in ("parameters.burn_requested", "campaign.base_spec.parameters.burn_requested"):
            try:
                _set_path(spec, path, burn_requested)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:27',
                    exception=exc,
                    strict=False,
                )
    if thrust_n is not None:
        for path in ("parameters.thrust_n", "campaign.base_spec.parameters.thrust_n"):
            try:
                _set_path(spec, path, thrust_n)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:28',
                    exception=exc,
                    strict=False,
                )
    if isp_s is not None:
        for path in ("parameters.specific_impulse_s", "campaign.base_spec.parameters.specific_impulse_s"):
            try:
                _set_path(spec, path, isp_s)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:29',
                    exception=exc,
                    strict=False,
                )
    if burn_start_s is not None:
        for path in ("parameters.burn_start_s", "campaign.base_spec.parameters.burn_start_s"):
            try:
                _set_path(spec, path, burn_start_s)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:30',
                    exception=exc,
                    strict=False,
                )
    if burn_duration_s is not None:
        for path in ("parameters.burn_duration_s", "campaign.base_spec.parameters.burn_duration_s"):
            try:
                _set_path(spec, path, burn_duration_s)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:31',
                    exception=exc,
                    strict=False,
                )
    if battery_soc_for_burn is not None:
        for path in ("parameters.battery_soc", "campaign.base_spec.parameters.battery_soc"):
            try:
                _set_path(spec, path, battery_soc_for_burn)
            except Exception as exc:
                record_runtime_diagnostic(
                    code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:32',
                    exception=exc,
                    strict=False,
                )

    # A3/A4/A5 hardening: create modern scenario modifiers from common natural
    # language cues.  These modifiers decorate an existing capability; they are
    # not standalone runnable capabilities and do not let the model write Python.
    modifier_faults = []
    modifier_degradations = []
    modifier_constraints = []
    lower_text = text.lower()
    capacity_drop = _extract_ratio_value(text, ["capacity drop", "capacity loss", "容量下降", "容量降低", "容量损失", "容量衰减"])
    if capacity_drop is not None and ("battery" in lower_text or "电池" in lower_text or "容量" in lower_text):
        modifier_faults.append({
            "modifier_id": "nl_battery_capacity_drop",
            "target": "eps.battery",
            "fault_type": "capacity_drop",
            "onset_time_s": onset_s if onset_s is not None else 0.0,
            "duration_s": -1,
            "severity": float(capacity_drop),
        })
    solar_eff_scale = _extract_ratio_value(text, ["efficiency scale", "效率缩放", "效率退化到", "效率变为", "效率降到", "效率"] )
    if solar_eff_scale is not None and ("solar" in lower_text or "太阳" in lower_text or "帆板" in lower_text):
        modifier_degradations.append({
            "modifier_id": "nl_solar_efficiency_scale",
            "target": "eps.solar_panel",
            "degradation_type": "efficiency_scale",
            "onset_time_s": onset_s if onset_s is not None else 0.0,
            "duration_s": -1,
            "scale": float(solar_eff_scale),
        })
    if _contains_any(text, ["transmitter outage", "发射机失效", "发射机故障", "通信发射机故障", "下行中断"]):
        modifier_faults.append({
            "modifier_id": "nl_transmitter_outage",
            "target": "comm.transmitter",
            "fault_type": "transmitter_outage",
            "onset_time_s": onset_s if onset_s is not None else 0.0,
            "duration_s": -1,
            "severity": 1.0,
        })
    if _contains_any(text, ["反作用轮饱和", "飞轮饱和", "反作用轮达到速度限制", "轮速饱和", "reaction wheel saturation", "wheel speed limit"]):
        modifier_constraints.append({
            "modifier_id": "nl_reaction_wheel_speed_limit",
            "target": "adcs.reaction_wheel",
            "constraint_type": "reaction_wheel_speed_limit",
            "onset_time_s": onset_s if onset_s is not None else 0.0,
            "duration_s": -1,
            "severity": 1.0,
            "label": "反作用轮达到速度限制",
        })
    replace_composite_defaults = False
    if cap_id == "whole_spacecraft.composite_digital_twin.v1":
        spacecraft = spec.setdefault("spacecraft", {})
        if isinstance(spacecraft, dict):
            mission = spacecraft.setdefault("mission", {})
            if isinstance(mission, dict):
                mission.update({
                    "template": cap_id,
                    "objective": "whole_spacecraft_end_to_end_simulation",
                    "subsystems": ["orbit", "adcs", "eps", "thermal", "communication", "payload"],
                    "success_metric": "mission_success_score",
                    "success_metric_basis": "deterministic_gate_fraction_not_probability",
                })
        explicit_faults, explicit_degradations, recognized = _composite_effect_overrides(text, onset_s=onset_s)

        def merge_explicit(
            explicit: list[dict[str, Any]],
            generic: list[dict[str, Any]],
        ) -> list[dict[str, Any]]:
            explicit_targets = {str(item.get("target") or "") for item in explicit}
            return [*explicit, *[item for item in generic if str(item.get("target") or "") not in explicit_targets]]

        modifier_faults = merge_explicit(explicit_faults, modifier_faults)
        modifier_degradations = merge_explicit(explicit_degradations, modifier_degradations)
        replace_composite_defaults = recognized or bool(modifier_faults or modifier_degradations or modifier_constraints)

        def deduplicate(items: list[dict[str, Any]], type_key: str) -> list[dict[str, Any]]:
            seen: set[tuple[str, str]] = set()
            result: list[dict[str, Any]] = []
            for item in items:
                key = (str(item.get("target") or ""), str(item.get(type_key) or ""))
                if key in seen:
                    continue
                seen.add(key)
                result.append(item)
            return result

        modifier_faults = deduplicate(modifier_faults, "fault_type")
        modifier_degradations = deduplicate(modifier_degradations, "degradation_type")
        modifier_constraints = deduplicate(modifier_constraints, "constraint_type")

    if modifier_faults or modifier_degradations or modifier_constraints or replace_composite_defaults:
        modifiers = dict(spec.get("modifiers") or {}) if isinstance(spec.get("modifiers"), Mapping) else {}
        if replace_composite_defaults:
            spec["faults"] = []
            spec["degradations"] = []
            modifiers["faults"] = modifier_faults
            modifiers["degradations"] = modifier_degradations
            modifiers["constraints"] = modifier_constraints
        else:
            if modifier_faults:
                modifiers["faults"] = list(modifiers.get("faults") or []) + modifier_faults
            if modifier_degradations:
                modifiers["degradations"] = list(modifiers.get("degradations") or []) + modifier_degradations
            if modifier_constraints:
                modifiers["constraints"] = list(modifiers.get("constraints") or []) + modifier_constraints
        spec["modifiers"] = modifiers
        if isinstance(spec.get("target"), dict):
            spec["target"]["mode"] = _effect_mode_from_spec(spec)

    _normalize_event_windows(spec)

    requested_fields = _contextualize_requested_output_fields(cap_id, _requested_output_fields(text))
    output_resolution = resolve_output_intents(text, cap_id)
    requested_fields = list(dict.fromkeys([*requested_fields, *output_resolution.fields]))
    if requested_fields:
        spec.setdefault("outputs", {})
        if isinstance(spec["outputs"], dict):
            existing = list(spec["outputs"].get("record_fields") or spec["outputs"].get("qoi") or [])
            spec["outputs"]["record_fields"] = list(dict.fromkeys([*existing, *requested_fields]))
            existing_plots = list(spec["outputs"].get("plots") or [])
            spec["outputs"]["plots"] = list(dict.fromkeys([*existing_plots, *requested_fields]))
        if isinstance(spec.get("metadata"), dict):
            spec["metadata"].setdefault("agent", {})
            spec["metadata"]["agent"]["requested_output_fields"] = requested_fields
            if output_resolution.changed:
                spec["metadata"]["agent"]["output_intent_resolution"] = output_resolution.to_dict()

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
                location='src/sat_sim/capability_agent.py:_apply_capability_request_overrides:01',
                exception=exc,
                strict=False,
            )




class TextModelTaskSpecBackend:
    """TaskSpec backend backed by an external text-generation model.

    The model receives the same capability-constrained prompt produced for human
    orchestration.  It must return a YAML/JSON TaskSpec draft.  This backend
    never executes Python from the model; it only parses the draft and lets the
    trusted local validator/compiler/exporter continue the pipeline.
    """

    def __init__(self, *, name: str, config: LLMCallConfig) -> None:
        self.name = name
        self.config = config
        self.last_call: LLMCallResult | None = None
        self.last_raw_output: str | None = None

    def draft_task_spec(self, *, request: CapabilityAgentRequest, context: Mapping[str, Any]) -> dict[str, Any]:
        prompt = build_capability_agent_prompt(
            request.request,
            allowed_capabilities=request.allowed_capabilities,
            advisory_context=request.advisory_context,
        )
        if self.config.structured_output == "json_object":
            prompt += (
                "\n\n## A4 structured output requirement\n"
                "Return valid json only. Use either a TaskSpec JSON object directly "
                "or {\"task_spec\": <TaskSpec object>}. Do not use YAML or Markdown fences. "
                "Do not use Python expressions, comments, ellipses, trailing commas, or list multiplication such as [1.0] * 30; "
                "every array value must be a literal JSON array or be represented by scalar configuration fields.\n"
            )
        elif self.config.structured_output == "tool_call":
            prompt += (
                "\n\n## A4 structured output requirement\n"
                "Call the generate_task_spec tool exactly once. Put the complete TaskSpec object "
                "in the task_spec argument.\n"
            )
        backend = make_text_llm_backend(self.config)
        result = backend.generate_text(prompt)
        self.last_call = result
        self.last_raw_output = result.raw_text
        if not result.ok:
            raise TaskSpecError(f"LLM backend {self.name} failed: {result.error or 'unknown error'}")
        return extract_task_spec_from_model_text(result.raw_text)

    def draft_task_spec_with_validation_errors(
        self,
        *,
        request: CapabilityAgentRequest,
        context: Mapping[str, Any],
        previous_draft: Mapping[str, Any],
        validation: ValidationResult,
    ) -> dict[str, Any]:
        """Ask the model once to correct representation/schema errors.

        The correction prompt is still capability-constrained. The downstream
        planner/generator consistency gate prevents capability drift.
        """

        prompt = build_capability_agent_prompt(
            request.request,
            allowed_capabilities=request.allowed_capabilities,
            advisory_context=request.advisory_context,
        )
        prompt += (
            "\n\n## Validator feedback for one-shot correction\n"
            "The previous TaskSpec draft failed validation. Produce one corrected JSON/YAML TaskSpec. "
            "Do not change capability_id, do not add unsupported effects, do not raise claim level, and do not relax thresholds.\n"
            f"Validation errors:\n```json\n{json.dumps(validation.to_dict(), indent=2, ensure_ascii=False)}\n```\n"
            f"Previous draft:\n```json\n{json.dumps(dict(previous_draft), indent=2, ensure_ascii=False)}\n```\n"
        )
        backend = make_text_llm_backend(self.config)
        result = backend.generate_text(prompt)
        self.last_call = result
        self.last_raw_output = result.raw_text
        if not result.ok:
            raise TaskSpecError(f"LLM backend {self.name} correction failed: {result.error or 'unknown error'}")
        return extract_task_spec_from_model_text(result.raw_text)


def _task_type_for_capability_contract(contract: CapabilityContract) -> str:
    """Return the TaskSpec task_type for a capability contract."""

    level = str(contract.target_level)
    if level == "integrated" and "orbit_environment" in contract.capability_id:
        return "orbit_environment"
    if level in {"component", "subsystem", "whole_spacecraft"}:
        return level
    return level or "component"


def _target_type_for_capability(contract: CapabilityContract) -> str:
    """Return the default fault target_type for a capability contract."""

    target = str(contract.target_name)
    if target in {"eps", "basic_power_orbit"}:
        return "battery"
    return target or "unknown"


def _infer_capability_id_from_request_text(request_text: str) -> str | None:
    """Infer a high-confidence capability route from the user request.

    This is intentionally conservative.  It is not an LLM replacement; it is a
    guardrail for cases where a real model chooses a plausible but overly broad
    capability, e.g. mapping a solar-panel shadow request to EPS.  Only explicit
    component/integrated cues override a model-provided capability.
    """

    explicit_ids = explicit_capability_ids_in_text(request_text)
    if len(explicit_ids) == 1:
        return explicit_ids[0]
    if len(explicit_ids) > 1:
        return None
    text = request_text.lower()
    strong_eps = _contains_any(text, [
        "eps", "电源系统", "电源分系统", "供电系统", "pdu", "负载", "load",
        "电池、太阳", "电池和太阳", "battery and solar", "battery, solar",
    ])
    strong_solar = _contains_any(text, ["solar panel", "solar array", "太阳阵列", "太阳帆板", "太阳能板", "帆板"])
    strong_battery = _contains_any(text, ["battery", "电池", "soc", "荷电"])
    strong_rw = _contains_any(text, ["reaction wheel", "反作用轮", "动量轮", "飞轮"])
    strong_adcs = _contains_any(text, ["adcs", "姿控", "姿态控制", "指向", "指向控制", "pointing", "attitude", "姿态误差", "指向误差"])
    strong_adcs_closed_loop = _contains_any(text, ["closed-loop", "closed loop", "闭环", "闭环姿控", "闭环姿态控制", "detumble", "消旋", "nadir", "对地指向", "sun pointing", "太阳指向", "quaternion", "四元数", "gyro proxy", "简单陀螺", "陀螺代理"])
    strong_thermal = _contains_any(text, ["thermal", "热控", "热轨", "温度", "散热", "散热器", "radiator", "heater", "加热器", "电池温度", "热平衡"])
    strong_comm = _contains_any(text, ["comm", "communication", "通信", "下行", "downlink", "data backlog", "数据积压", "ground pass", "地面站通信"])
    strong_comm_mission = _contains_any(text, ["通信", "地面站通信", "ground pass", "data backlog", "数据积压", "backlog", "地面站", "可见性", "ground station"])
    strong_data_queue = _contains_any(text, ["data queue", "queue bits", "数据队列", "数据缓存", "队列", "缓存积压", "dropped bits", "丢弃数据"])
    strong_onboard_storage = _contains_any(text, ["onboard storage", "stored bits", "data storage", "星上存储", "载荷存储", "存储容量", "存储溢出", "overflow bits"])
    strong_ground_station_component = _contains_any(text, ["ground station component", "地面站 component", "地面站部件", "地面站可见性 component"])
    strong_antenna_component = _contains_any(text, ["antenna", "天线", "antenna gain", "天线增益"])
    strong_transmitter_component = _contains_any(text, ["transmitter", "发射机", "tx power", "发射功率", "功放"])
    strong_link_budget_component = _contains_any(text, ["link budget", "link-budget", "链路预算", "ebn0", "ber", "链路裕度"])
    strong_power_sink_component = _contains_any(text, ["power sink", "用电负载", "功耗负载", "simple load"])
    strong_heater_component = _contains_any(text, ["heater component", "加热器 component", "加热器部件", "heater power"])
    strong_radiator_component = _contains_any(text, ["radiator component", "散热器 component", "散热器部件", "heat rejection", "热排散"])
    strong_thermal_node_component = _contains_any(text, ["thermal node", "热节点", "温度节点", "thermal profile"])
    strong_payload_sensor_component = _contains_any(text, ["payload sensor", "载荷传感器", "观测质量", "cloud fraction", "有效观测"])
    strong_payload_component = _contains_any(text, ["payload source-native", "source-native payload", "payload instrument", "payload component", "payload", "载荷部件", "成像载荷", "payload power"])
    strong_source_native = _contains_any(text, ["source-native", "source native", "源码", "原生", "原 src", "原src", "source"])
    strong_propulsion_source = _contains_any(text, ["propulsion", "propellant", "fuel tank", "fuel pressure", "thruster", "推进", "推进剂", "燃料箱", "燃料箱压力", "推进器", "推力器", "点火", "喷气"])
    strong_orbit = _contains_any(text, ["orbit environment", "leo", "轨道环境", "轨道", "磁场", "地面站", "ground station", "orbit trace"])
    strong_medium_orbit = _contains_any(text, [
        "medium fidelity", "medium-fidelity", "中等保真", "中保真", "j2", "j2摄动", "j2 摄动",
        "analytic sun", "sun vector", "太阳向量", "日食几何", "eclipse geometry",
        "umbra", "penumbra", "本影", "半影", "frame-tagged", "frame tagged",
    ])
    strong_power_thermal_orbit_coupled = (
        _contains_any(text, ["power thermal orbit coupled", "power-thermal-orbit coupled", "电源热控轨道耦合", "电源-热控-轨道耦合", "整星电源热控轨道耦合", "功率热轨道耦合", "电源热轨道耦合"])
        or (strong_thermal and strong_eps and strong_orbit and _contains_any(text, ["coupled", "coupling", "耦合", "heater", "加热器", "derating", "降额"]))
    )
    strong_comm_payload_coupled = (
        _contains_any(text, ["comm payload mission", "comm-payload mission", "mission-coupled comm", "通信载荷任务耦合", "通信-载荷", "载荷通信", "数据守恒"])
        or (strong_comm_mission and (strong_payload_component or strong_onboard_storage or strong_data_queue or _contains_any(text, ["payload data", "载荷数据", "data generation", "数据生成"])) and _contains_any(text, ["coupled", "coupling", "耦合", "联合", "联动", "任务级", "mission"]))
    )
    strong_maneuver_coupled = strong_propulsion_source and (strong_orbit or _contains_any(text, ["orbit perturbation", "轨道扰动", "整星", "whole spacecraft"])) and _contains_any(text, ["maneuver", "delta-v", "delta v", "dv", "burn", "finite burn", "机动", "变轨", "轨控", "点火", "喷气", "速度增量", "轨道姿态耦合", "推进轨道姿态", "姿态扰动", "耦合", "proxy", "代理"])
    strong_orbit_adcs_integration = strong_orbit and strong_adcs and _contains_any(text, ["orbit adcs", "orbit-adcs", "轨道姿控", "轨道姿态", "轨道和姿控", "lvlh", "frame consistency", "坐标系一致", "nadir pointing from orbit", "sun pointing from orbit", "对地指向轨道", "太阳指向轨道", "耦合", "integration", "集成"]) and not strong_maneuver_coupled
    strong_whole = (
        _contains_any(text, ["whole spacecraft", "spacecraft", "整星", "全星", "星上", "power orbit", "power-orbit", "电源轨道", "轨道电源"])
        or (strong_eps and strong_orbit)
        or (strong_battery and strong_orbit and strong_adcs)
    )
    strong_unified_runtime = _contains_any(text, [
        "统一运行图", "unified runtime", "unified process/task",
        "统一 process/task", "统一消息链", "统一basilisk",
    ])
    strong_composite_full = (
        _contains_any(text, ["全分系统", "全分系统整星", "所有分系统", "六大分系统", "六分系统", "六系统", "完整六分系统", "完整整星", "完整全星", "全系统整星", "整星全链路", "整星数字样机", "full spacecraft", "all subsystems", "six subsystems", "end-to-end spacecraft"])
        and (strong_whole or sum(bool(item) for item in (strong_orbit, strong_adcs, strong_eps, strong_thermal, strong_comm, strong_payload_component)) >= 3)
    ) or all((strong_orbit, strong_adcs, strong_eps, strong_thermal, strong_comm, strong_payload_component))

    if strong_unified_runtime and strong_whole:
        return "whole_spacecraft.unified_native.v1"
    if strong_unified_runtime and strong_adcs:
        return "subsystem.adcs_unified_native.v1"
    if strong_unified_runtime and strong_propulsion_source:
        return "subsystem.propulsion.unified_native.v1"
    if strong_composite_full:
        return "whole_spacecraft.composite_digital_twin.v1"
    if strong_maneuver_coupled:
        return "whole_spacecraft.maneuver_orbit_attitude.v1"
    if strong_orbit_adcs_integration:
        return "whole_spacecraft.orbit_adcs_fidelity.v1"
    if strong_power_thermal_orbit_coupled:
        return "whole_spacecraft.power_thermal_orbit_coupled.v1"
    if strong_comm_payload_coupled:
        return "whole_spacecraft.comm_payload_mission_coupled.v1"
    if strong_medium_orbit and strong_orbit:
        return "orbit_environment.orbit_fidelity.v1"
    if strong_whole and strong_thermal and (strong_eps or strong_orbit):
        return "whole_spacecraft.power_thermal_orbit_coupled.v1"
    if strong_whole and strong_adcs:
        return "subsystem.adcs_fidelity.v1"
    if strong_source_native and strong_payload_component and not strong_whole:
        return "subsystem.payload.source_native.v1"
    if strong_source_native and strong_comm and not strong_whole:
        return "subsystem.comm_data.source_native.v1"
    if strong_source_native and strong_thermal and not strong_whole:
        return "subsystem.thermal.source_native.v1"
    if strong_source_native and strong_eps and not strong_whole:
        return "subsystem.eps.source_native.v1"
    if strong_propulsion_source:
        return "subsystem.propulsion.source_native.v1"
    if strong_data_queue:
        return "component.data_queue.v1"
    if strong_onboard_storage:
        return "component.onboard_storage.v1"
    if strong_ground_station_component:
        return "component.ground_station.v1"
    if strong_link_budget_component and (not strong_comm_mission or _contains_any(text, ["standalone", "stand-alone", "do not model a full ground pass", "独立"])):
        return "component.link_budget.v1"
    if strong_antenna_component:
        return "component.antenna.v1"
    if strong_transmitter_component and not strong_comm:
        return "component.transmitter.v1"
    if strong_power_sink_component:
        return "component.power_sink.v1"
    if strong_heater_component:
        return "component.heater.v1"
    if strong_radiator_component:
        return "component.radiator.v1"
    if strong_thermal_node_component:
        return "component.thermal_node.v1"
    if strong_payload_sensor_component:
        return "component.payload_sensor.v1"
    if strong_payload_component:
        return "component.payload.v1"
    if strong_comm:
        return "subsystem.comm.basic_ground_pass.v1"
    if strong_thermal:
        return "subsystem.thermal.basic_lumped.v1"
    if strong_whole:
        return "whole_spacecraft.power_thermal_orbit_coupled.v1"
    if strong_orbit:
        return "orbit_environment.orbit_fidelity.v1"
    if strong_eps:
        return "subsystem.eps.basic.v1"
    if strong_adcs_closed_loop and strong_adcs:
        return "subsystem.adcs_fidelity.v1"
    if strong_adcs:
        return "subsystem.adcs_fidelity.v1"
    if strong_solar:
        return "component.solar_panel.v1"
    if strong_rw:
        return "component.reaction_wheel.v1"
    if strong_battery:
        return "component.battery.v1"
    return None


def _solar_shadow_factor_from_request(request_text: str) -> float | None:
    """Extract a solar shadow/illumination factor in [0, 1] from request text."""

    value = _extract_first_float(request_text, ["shadow factor", "shadow", "阴影因子", "遮挡因子", "遮挡", "shadow_factor"], min_value=0.0, max_value=1.0)
    if value is not None:
        return max(0.0, min(1.0, float(value)))
    return None


def _rewrite_to_solar_panel_capability(spec: Mapping[str, Any], request: CapabilityAgentRequest) -> dict[str, Any]:
    """Rewrite an over-broad EPS/other draft into component.solar_panel.v1.

    Real LLMs often treat "solar array shadow" as an EPS subsystem scenario.  If
    the request explicitly names a solar-panel/array component and does not ask
    for EPS loads/battery behavior, the component capability is the expected
    route for the A2/A3 eval and for component-level script generation.
    """

    out = copy.deepcopy(dict(spec))
    params = dict(out.get("parameters") or {}) if isinstance(out.get("parameters"), Mapping) else {}
    # Map common EPS solar parameter aliases to component solar-panel names.
    if "max_power_w" not in params:
        for alias in ("solar_array_max_power_w", "solar_power_w", "array_power_w"):
            if alias in params:
                params["max_power_w"] = params.get(alias)
                break
    params.setdefault("max_power_w", _extract_first_float(request.request, ["最大功率", "max power", "solar array", "太阳阵列"], min_value=0.0) or 120.0)
    params.setdefault("efficiency", params.get("solar_array_efficiency", 1.0))
    params.setdefault("panel_count", params.get("solar_panel_count", 1))
    params.setdefault("deployment_fraction", params.get("solar_deployment_fraction", 1.0))
    params.setdefault("initial_normal_b", params.get("solar_initial_normal_b", [1.0, 0.0, 0.0]))
    params.setdefault("sun_vector_b", params.get("sun_vector_b", [1.0, 0.0, 0.0]))
    shadow_factor = _solar_shadow_factor_from_request(request.request)
    if shadow_factor is not None:
        params["shadow_factor"] = shadow_factor
    elif "shadow_factor" not in params:
        params["shadow_factor"] = 1.0
    # Preserve an LLM-provided shadow profile if present.
    if "shadow_profile" in params:
        profile = params.get("shadow_profile")
        if isinstance(profile, list) and profile:
            params["shadow_profile"] = profile
    params = {k: v for k, v in params.items() if k in {
        "max_power_w", "efficiency", "panel_count", "deployment_fraction", "initial_normal_b", "sun_vector_b",
        "sun_vector_profile_b", "shadow_factor", "shadow_profile", "enable_tracking", "max_slew_rate_rad_s",
        "solar_tracking_max_slew_rate_rad_s",
    }}

    degradation_requested = _contains_any(request.request, ["degradation", "退化", "衰减", "遮挡", "阴影", "shadow"])
    mode = "degradation" if degradation_requested else "nominal"
    out.update({
        "task_type": "component",
        "capability_id": "component.solar_panel.v1",
        "target": {"level": "component", "name": "solar_panel", "mode": mode},
        "parameters": params,
    })
    if mode == "degradation":
        sf = float(params.get("shadow_factor", 1.0)) if isinstance(params.get("shadow_factor", 1.0), (int, float)) else 1.0
        # A nonzero degradation payload is required by the component adapter.
        # If the request only gives shadow factor, translate it into an
        # efficiency-loss proxy while preserving shadow_factor as a parameter.
        loss_pct = max(0.0, min(100.0, (1.0 - sf) * 100.0))
        if loss_pct <= 0.0:
            loss_pct = _extract_first_float(request.request, ["效率损失", "efficiency loss", "退化"], min_value=0.0, max_value=100.0) or 35.0
        out["degradations"] = {
            "eps": {
                "solar_panel": {
                    "efficiency_loss_pct": loss_pct,
                    "radiation_damage_factor": 0.0,
                }
            }
        }
        out.pop("faults", None)
    return out



_ORBIT_ENVIRONMENT_PAYLOAD_KEYS = {
    "earth_radius_m",
    "earth_mu_m3_s2",
    "earth_rotation_rad_s",
    "altitude_m",
    "inclination_deg",
    "raan_deg",
    "arg_lat0_deg",
    "sun_vector_n",
    "magnetic_dipole_axis_n",
    "magnetic_equator_strength_t",
    "ground_station",
}


def _normalize_orbit_environment_payload(out: dict[str, Any]) -> None:
    """Move LLM orbit-environment payload aliases into ``orbit_environment``.

    DeepSeek-style drafts often put LEO environment fields under
    ``parameters`` because the capability catalog lists them as parameters.
    The strict TaskSpec schema, however, requires orbit-environment tasks to
    include a non-empty top-level ``orbit_environment`` object.  This repair is
    deterministic and only applies to the registered simple LEO capability.
    """

    if out.get("capability_id") != "orbit_environment.leo_simple.v1":
        return
    orbit = dict(out.get("orbit_environment") or {}) if isinstance(out.get("orbit_environment"), Mapping) else {}
    params = dict(out.get("parameters") or {}) if isinstance(out.get("parameters"), Mapping) else {}

    # The strict TaskSpec schema allows orbit dynamics fields under
    # orbit_environment.  The ground_station object remains in parameters; the
    # adapter merges both maps at runtime.
    for key in list(params.keys()):
        if key in _ORBIT_ENVIRONMENT_PAYLOAD_KEYS and key != "ground_station" and key not in orbit:
            orbit[key] = params.pop(key)

    if isinstance(orbit.get("ground_station"), Mapping):
        params["ground_station"] = dict(orbit.pop("ground_station"))

    # A model may choose the right capability but omit the required object.
    # Provide capability defaults so validation can proceed and the adapter can
    # execute a deterministic LEO profile.
    if not orbit:
        orbit.update({
            "altitude_m": 500000.0,
            "inclination_deg": 51.6,
            "raan_deg": 0.0,
            "arg_lat0_deg": -110.0,
            "sun_vector_n": [1.0, 0.0, 0.0],
            "magnetic_dipole_axis_n": [0.0, 0.0, 1.0],
            "magnetic_equator_strength_t": 3.12e-05,
        })

    out["orbit_environment"] = orbit
    if params:
        out["parameters"] = params
    else:
        out.pop("parameters", None)


def _solar_panel_degradation_payload_from_request(request: CapabilityAgentRequest, params: Mapping[str, Any]) -> dict[str, Any]:
    """Build a minimal solar-panel degradation payload from request/params."""

    # Prefer explicit efficiency-loss text.  Fall back to shadow_factor as an
    # occlusion proxy; preserve the actual shadow factor in parameters.
    loss = _extract_first_float(
        request.request,
        ["efficiency_loss_pct", "efficiency loss", "效率损失", "效率衰减", "退化", "degradation"],
        min_value=0.0,
        max_value=100.0,
    )
    if loss is None:
        try:
            shadow = float(params.get("shadow_factor", 1.0))
            loss = max(0.0, min(100.0, (1.0 - shadow) * 100.0))
        except Exception:
            loss = None
    if loss is None or loss <= 0.0:
        loss = 35.0
    return {"efficiency_loss_pct": float(loss), "radiation_damage_factor": 0.0}


def _normalize_solar_panel_degradations(out: dict[str, Any], request: CapabilityAgentRequest) -> None:
    """Coerce common solar-panel degradation shapes into strict schema.

    Real model outputs often use an event list such as
    ``degradations: [{degradation_type: efficiency_loss_pct, magnitude: 10}]``.
    The TaskSpec schema requires a structured degradation object:
    ``degradations.eps.solar_panel``.
    """

    if out.get("capability_id") != "component.solar_panel.v1":
        return
    params = dict(out.get("parameters") or {}) if isinstance(out.get("parameters"), Mapping) else {}
    degradation_obj: dict[str, Any] | None = None
    raw = out.get("degradations")

    if isinstance(raw, list):
        payload: dict[str, Any] = {}
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            dtype = str(item.get("degradation_type") or item.get("type") or "").lower()
            item_parameters = (
                item.get("parameters")
                if isinstance(item.get("parameters"), Mapping)
                else {}
            )
            value = item.get(
                "efficiency_loss_pct",
                item_parameters.get(
                    "value_pct",
                    item.get("magnitude", item.get("value")),
                ),
            )
            if "radiation" in dtype:
                try:
                    payload["radiation_damage_factor"] = float(value)
                except Exception:
                    payload.setdefault("radiation_damage_factor", 0.0)
            elif "efficiency" in dtype or "loss" in dtype or "degradation" in dtype or dtype in {"", "solar_panel_degradation"}:
                try:
                    payload["efficiency_loss_pct"] = float(value)
                except Exception as exc:
                    record_runtime_diagnostic(
                        code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                        category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                        location='src/sat_sim/capability_agent.py:_normalize_solar_panel_degradations:01',
                        exception=exc,
                        strict=False,
                    )
        if payload:
            payload.setdefault("radiation_damage_factor", 0.0)
            degradation_obj = {"eps": {"solar_panel": payload}}
    elif isinstance(raw, Mapping):
        raw_map = dict(raw)
        eps = raw_map.get("eps") if isinstance(raw_map.get("eps"), Mapping) else None
        solar = eps.get("solar_panel") if isinstance(eps, Mapping) and isinstance(eps.get("solar_panel"), Mapping) else None
        if isinstance(solar, Mapping):
            degradation_obj = {"eps": {"solar_panel": dict(solar)}}
        elif isinstance(raw_map.get("solar_panel"), Mapping):
            degradation_obj = {"eps": {"solar_panel": dict(raw_map["solar_panel"])}}
        elif any(k in raw_map for k in ("efficiency_loss_pct", "radiation_damage_factor")):
            degradation_obj = {"eps": {"solar_panel": {
                "efficiency_loss_pct": float(raw_map.get("efficiency_loss_pct", 35.0)),
                "radiation_damage_factor": float(raw_map.get("radiation_damage_factor", 0.0)),
            }}}

    target = out.get("target") if isinstance(out.get("target"), Mapping) else {}
    mode = str(target.get("mode") or "")
    degradation_requested = mode == "degradation" or _contains_any(
        request.request,
        ["degradation", "退化", "衰减", "遮挡", "阴影", "shadow", "occlusion"],
    )
    if degradation_obj is None and degradation_requested:
        degradation_obj = {"eps": {"solar_panel": _solar_panel_degradation_payload_from_request(request, params)}}

    if degradation_obj is not None:
        out["degradations"] = degradation_obj
        modifiers = (
            out.get("modifiers")
            if isinstance(out.get("modifiers"), dict)
            else None
        )
        if modifiers is not None:
            modifiers["degradations"] = []
        out.setdefault("target", {})
        if isinstance(out["target"], dict):
            out["target"]["mode"] = "degradation"
        out.pop("faults", None)

def _route_capability_if_high_confidence(spec: Mapping[str, Any], request: CapabilityAgentRequest) -> dict[str, Any]:
    """Apply high-confidence local routing repairs before strict validation."""

    inferred = _infer_capability_id_from_request_text(request.request)
    if inferred is None:
        return copy.deepcopy(dict(spec))
    if inferred not in set(request.allowed_capabilities):
        return copy.deepcopy(dict(spec))
    current = spec.get("capability_id")
    # Do not override EPS when the user explicitly asks for EPS/system/load
    # behavior.  `_infer_capability_id_from_request_text` already gives EPS
    # priority over solar in those cases.
    if inferred == current:
        return copy.deepcopy(dict(spec))
    # Preserve an explicit, active capability selected by the model.  The
    # deterministic planner consistency gate later decides whether that route
    # matches the user request.  Overwriting it here made canonical model output
    # lose its locked identity before the audited planner fallback could run.
    if isinstance(current, str) and current in set(request.allowed_capabilities):
        replacement = get_replacement_capability_id(current)
        if replacement != inferred:
            return copy.deepcopy(dict(spec))
    if inferred == "component.solar_panel.v1":
        return _rewrite_to_solar_panel_capability(spec, request)
    # Orbit/battery/RW routing is less often wrong, but if a request explicitly
    # identifies one of these and the model chose a different capability, trust
    # the capability contract route and let the standard alias repair fill the
    # schema.  For now only solar rewrites parameters deeply; the others are
    # represented well by existing model outputs or template repair.
    out = copy.deepcopy(dict(spec))
    original_capability_id = str(current) if isinstance(current, str) else None
    out["capability_id"] = inferred
    if original_capability_id and original_capability_id != inferred:
        replacement = get_replacement_capability_id(original_capability_id)
        if replacement == inferred:
            _apply_capability_migration_metadata(out, {
                "from": original_capability_id,
                "to": inferred,
                "reason": "deprecated_replacement",
                "lifecycle_status": get_capability(original_capability_id).lifecycle_status,
            })
    try:
        contract = get_capability(inferred)
        out["task_type"] = _task_type_for_capability_contract(contract)
        out["target"] = {"level": contract.target_level, "name": contract.target_name, "mode": str(_mapping(out.get("target")).get("mode") or "nominal")}
    except Exception as exc:
        record_runtime_diagnostic(
            code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
            category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
            location='src/sat_sim/capability_agent.py:_route_capability_if_high_confidence:01',
            exception=exc,
            strict=False,
        )
    return out


def _strip_keys(mapping: dict[str, Any], allowed: set[str]) -> None:
    """Remove non-schema keys in-place from a mapping."""

    for key in list(mapping.keys()):
        if key not in allowed:
            mapping.pop(key, None)


def _external_draft_capability_id(spec: Mapping[str, Any] | None) -> str | None:
    """Read a capability id from legacy, canonical, or wrapped model output."""

    if not isinstance(spec, Mapping):
        return None
    source: Mapping[str, Any] = spec
    for key in ("task_spec", "taskspec", "TaskSpec", "spec"):
        nested = source.get(key)
        if isinstance(nested, Mapping):
            source = nested
            break
    direct = source.get("capability_id")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    model = source.get("model")
    if isinstance(model, Mapping):
        value = model.get("capability_id")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _replace_external_draft_capability_id(
    spec: Mapping[str, Any],
    capability_id: str,
) -> dict[str, Any]:
    out = copy.deepcopy(dict(spec))
    source = out
    for key in ("task_spec", "taskspec", "TaskSpec", "spec"):
        nested = source.get(key)
        if isinstance(nested, dict):
            source = nested
            break
    if isinstance(source.get("capability_id"), str):
        source["capability_id"] = capability_id
    elif isinstance(source.get("model"), dict):
        source["model"]["capability_id"] = capability_id
    else:
        source["capability_id"] = capability_id
    return out


def _capability_id_punctuation_alias(candidate: str | None, expected: str) -> bool:
    if not candidate:
        return False
    compact = lambda value: re.sub(r"[^a-z0-9]+", "", value.lower())
    return candidate != expected and compact(candidate) == compact(expected)


def _canonical_event_to_legacy(item: Mapping[str, Any], *, kind: str, index: int) -> dict[str, Any]:
    """Project a permissive canonical event into the legacy Agent draft shape."""

    src = dict(item)
    start = float(src.get("start_s", src.get("onset_time_s", 0.0)) or 0.0)
    end = src.get("end_s")
    duration = -1.0 if end is None else max(0.0, float(end) - start)
    effect = src.get("effect") or src.get(f"{kind}_type") or src.get("type") or "unspecified"
    target = src.get("target") or "unspecified"
    base = {
        "target": str(target),
        "target_type": str(src.get("target_type") or target),
        "onset_time_s": start,
        "duration_s": duration,
        "magnitude": src.get("magnitude", src.get("scale", 1.0)),
        "parameters": dict(src.get("parameters") or {}) if isinstance(src.get("parameters"), Mapping) else {},
    }
    if kind == "fault":
        base.update({"fault_id": str(src.get("id") or src.get("fault_id") or f"fault_{index + 1}"), "fault_type": str(effect)})
    elif kind == "degradation":
        base.update({"degradation_id": str(src.get("id") or src.get("degradation_id") or f"degradation_{index + 1}"), "degradation_type": str(effect)})
    else:
        base.update({"constraint_id": str(src.get("id") or src.get("constraint_id") or f"constraint_{index + 1}"), "constraint_type": str(effect)})
    return base


def _project_external_draft_to_legacy(spec: Mapping[str, Any], request: CapabilityAgentRequest) -> dict[str, Any]:
    """Accept canonical, legacy, and wrapped TaskSpec model output before validation.

    The capability Agent backend historically validates a flat TaskSpec 0.1
    draft before the product-facing facade migrates it to CanonicalTaskSpec 1.0.
    Local models increasingly return canonical objects.  Project canonical
    identity and event fields into the flat compatibility shape *before* key
    pruning or Pydantic validation, so required subsystem/spacecraft scaffolds
    can be restored deterministically from the authoritative capability contract.
    """

    source = copy.deepcopy(dict(spec))
    for key in ("task_spec", "taskspec", "TaskSpec", "spec"):
        nested = source.get(key)
        if isinstance(nested, Mapping):
            source = copy.deepcopy(dict(nested))
            break

    model = dict(source.get("model") or {}) if isinstance(source.get("model"), Mapping) else {}
    task = dict(source.get("task") or {}) if isinstance(source.get("task"), Mapping) else {}
    events = dict(source.get("events") or {}) if isinstance(source.get("events"), Mapping) else {}
    canonical_like = bool(model or task or events or source.get("schema_version") == CANONICAL_TASK_SPEC_VERSION)
    if not canonical_like:
        return source

    out: dict[str, Any] = {}
    capability_id = _external_draft_capability_id(source)
    target = dict(model.get("target") or source.get("target") or {}) if isinstance(model.get("target") or source.get("target"), Mapping) else {}
    simulation = dict(source.get("simulation") or {}) if isinstance(source.get("simulation"), Mapping) else {}

    contract = None
    if capability_id:
        try:
            resolved_id, _migration = resolve_capability_id_for_product(capability_id, allowed_capabilities=request.allowed_capabilities)
            capability_id = resolved_id
            contract = get_capability(capability_id)
        except Exception:
            contract = None
    if contract is not None:
        target.setdefault("level", contract.target_level)
        target.setdefault("name", contract.target_name)
        simulation.setdefault("level", _task_type_for_capability_contract(contract))
        if simulation.get("level") == "subsystem":
            simulation.setdefault("subsystem", contract.target_name)
    simulation.setdefault("level", target.get("level") or "component")
    if simulation.get("level") == "subsystem" and not simulation.get("subsystem") and target.get("name"):
        simulation["subsystem"] = target.get("name")
    target.setdefault("level", simulation.get("level"))
    target.setdefault("name", simulation.get("subsystem") if simulation.get("level") == "subsystem" else target.get("level"))
    target.setdefault("mode", "nominal")

    out["schema_version"] = TASK_SPEC_VERSION
    out["task_id"] = str(task.get("id") or source.get("task_id") or f"gen_model_task_{_short_hash(request.request)}")
    out["description"] = str(task.get("description") or source.get("description") or "")
    out["tags"] = list(task.get("tags") or source.get("tags") or [])
    if capability_id:
        out["capability_id"] = capability_id
    out["target"] = target
    out["task_type"] = _task_type_for_capability_contract(contract) if contract is not None else str(simulation.get("level") or "component")
    out["simulation"] = simulation

    spacecraft = model.get("spacecraft") or source.get("spacecraft")
    if isinstance(spacecraft, Mapping):
        out["spacecraft"] = copy.deepcopy(dict(spacecraft))
    if out["task_type"] == "whole_spacecraft" and not out.get("spacecraft"):
        out["spacecraft"] = {"mission": {"template": capability_id or "whole_spacecraft", "configuration_source": "agent_contract_repair"}}
    orbit_environment = model.get("orbit_environment") or source.get("orbit_environment")
    if isinstance(orbit_environment, Mapping):
        out["orbit_environment"] = copy.deepcopy(dict(orbit_environment))

    parameter_src = source.get("parameters") if isinstance(source.get("parameters"), Mapping) else {}
    values = dict(parameter_src.get("values") or {}) if isinstance(parameter_src.get("values"), Mapping) else {
        key: copy.deepcopy(value) for key, value in dict(parameter_src).items()
        if key not in {"profile", "overrides", "registry_id", "registry_hash"}
    }
    if parameter_src.get("profile") is not None:
        values.setdefault("profile", parameter_src.get("profile"))
    out["parameters"] = values

    output_src = dict(source.get("outputs") or {}) if isinstance(source.get("outputs"), Mapping) else {}
    if "record_fields" not in output_src and isinstance(output_src.get("qoi"), list):
        output_src["record_fields"] = list(output_src.get("qoi") or [])
    out["outputs"] = output_src

    faults = events.get("faults", source.get("faults", []))
    degradations = events.get("degradations", source.get("degradations", []))
    constraints = events.get("constraints", source.get("constraints", []))
    out["faults"] = [_canonical_event_to_legacy(item, kind="fault", index=i) for i, item in enumerate(faults or []) if isinstance(item, Mapping)]
    legacy_degradations = [_canonical_event_to_legacy(item, kind="degradation", index=i) for i, item in enumerate(degradations or []) if isinstance(item, Mapping)]
    legacy_constraints = [_canonical_event_to_legacy(item, kind="constraint", index=i) for i, item in enumerate(constraints or []) if isinstance(item, Mapping)]
    if legacy_degradations or legacy_constraints:
        out["modifiers"] = {"degradations": legacy_degradations, "constraints": legacy_constraints}

    if isinstance(source.get("mission"), Mapping):
        out["mission"] = copy.deepcopy(dict(source.get("mission") or {}))
    if isinstance(source.get("assurance"), Mapping):
        out["assurance"] = copy.deepcopy(dict(source.get("assurance") or {}))
    if isinstance(source.get("metadata"), Mapping):
        out["metadata"] = copy.deepcopy(dict(source.get("metadata") or {}))
    return out


def _coerce_common_llm_aliases(spec: Mapping[str, Any], request: CapabilityAgentRequest) -> dict[str, Any]:
    """Coerce common LLM schema drift into the strict TaskSpec schema.

    Real models often output reasonable-but-nonconforming fields such as
    ``task_type: fault_simulation``, ``simulation.sample_interval_s`` or
    ``faults[*].trigger_time_s``.  These are not user errors; they are expected
    model drift.  Keep this repair deterministic and conservative so Python
    script generation remains trusted-local rather than model-authored.
    """

    out = _route_capability_if_high_confidence(_project_external_draft_to_legacy(spec, request), request)
    protected_assurance = copy.deepcopy(out.get("assurance")) if isinstance(out.get("assurance"), Mapping) else None
    protected_mission = copy.deepcopy(out.get("mission")) if isinstance(out.get("mission"), Mapping) else None
    capability_id = out.get("capability_id")
    migration_payload: Mapping[str, Any] | None = None
    if isinstance(capability_id, str) and capability_id.strip():
        resolved_capability_id, migration = resolve_capability_id_for_product(capability_id, allowed_capabilities=request.allowed_capabilities)
        if resolved_capability_id != capability_id:
            out["capability_id"] = resolved_capability_id
            migration_payload = migration
            _apply_capability_migration_metadata(out, migration_payload)
            capability_id = resolved_capability_id
    contract: CapabilityContract | None = None
    if isinstance(capability_id, str) and capability_id.strip():
        try:
            contract = get_capability(capability_id)
        except Exception:
            contract = None
    explicit_effect: str | None = None
    effect_match = re.search(r"\beffect\s*=\s*([A-Za-z0-9_.:-]+)", request.request)
    declared: set[str] = set()
    if contract is not None:
        modes = contract.data.get("modes") if isinstance(contract.data.get("modes"), Mapping) else {}

        def effect_values(value: Any) -> set[str]:
            if isinstance(value, Mapping):
                return {
                    effect
                    for nested in value.values()
                    for effect in effect_values(nested)
                }
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                return {str(effect) for effect in value}
            return {str(value)} if isinstance(value, str) and value else set()

        declared = {
            effect
            for kind in ("fault", "degradation", "constraint")
            for effect in effect_values(
                (
                    (modes.get(kind) or {}).get("effects")
                    or (modes.get(kind) or {}).get(f"{kind}_types")
                )
                if isinstance(modes.get(kind), Mapping)
                else None
            )
        }
    if effect_match and contract is not None:
        candidate = effect_match.group(1)
        if candidate in declared:
            explicit_effect = candidate
    if explicit_effect is None and contract is not None:
        alias_match = re.search(r"模拟[“\"]([^”\"]+)[”\"]", request.request)
        if alias_match:
            try:
                from .form_schema import capability_form_schema

                alias = alias_match.group(1).strip()
                alias_kind: str | None = None
                for suffix, kind in (
                    ("（故障）", "faults"),
                    ("（退化）", "degradations"),
                    ("（约束）", "constraints"),
                ):
                    if alias.endswith(suffix):
                        alias = alias[: -len(suffix)].strip()
                        alias_kind = kind
                        break
                catalog = capability_form_schema(contract.capability_id).get("event_catalog") or {}
                alias_effects = {
                    str(row.get("effect"))
                    for kind in ("faults", "degradations", "constraints")
                    if alias_kind is None or kind == alias_kind
                    for row in catalog.get(kind, [])
                    if isinstance(row, Mapping)
                    and str(row.get("label") or "").strip() == alias
                    and str(row.get("effect") or "") in declared
                }
                if len(alias_effects) == 1:
                    explicit_effect = next(iter(alias_effects))
            except Exception as exc:
                record_runtime_diagnostic(
                    code="AGENT_OPTIONAL_NORMALIZATION_SKIPPED",
                    category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                    location="src/sat_sim/capability_agent.py:_coerce_common_llm_aliases:effect_alias",
                    exception=exc,
                    strict=False,
                )
    explicit_event_parameters: dict[str, Any] = {}
    parameter_match = re.search(
        r"(?:参数为|with\s+parameters)\s*(\{.*?\})",
        request.request,
        flags=re.IGNORECASE,
    )
    if parameter_match:
        try:
            parsed_parameters = json.loads(parameter_match.group(1))
            if isinstance(parsed_parameters, dict):
                explicit_event_parameters = parsed_parameters
        except (TypeError, ValueError, json.JSONDecodeError):
            explicit_event_parameters = {}

    catalog_event_parameters: dict[str, Any] = {}
    if explicit_effect and contract is not None:
        effect_contract = next(
            (
                effect
                for effect in contract.operator_contract.effects
                if effect.effect_id == explicit_effect
            ),
            None,
        )
        explicit_kind = str(getattr(effect_contract, "kind", ""))
        source_key = {
            "fault": "faults",
            "degradation": "degradations",
            "constraint": "constraints",
        }.get(explicit_kind)
        if source_key:
            target_payload = (
                dict(out.get("target") or {})
                if isinstance(out.get("target"), Mapping)
                else {}
            )
            target_payload["mode"] = explicit_kind
            out["target"] = target_payload
            modifier_payload = (
                out.get("modifiers")
                if isinstance(out.get("modifiers"), Mapping)
                else {}
            )
            try:
                from .form_schema import capability_form_schema

                catalog = capability_form_schema(contract.capability_id).get("event_catalog") or {}
                row = next(
                    item
                    for item in catalog.get(source_key, [])
                    if str(item.get("effect") or "") == explicit_effect
                )
                event = dict(row.get("default_event") or {})
                catalog_event_parameters = (
                    dict(event.get("parameters") or {})
                    if isinstance(event.get("parameters"), Mapping)
                    else {}
                )

                def has_usable_event(value: Any) -> bool:
                    if isinstance(value, Mapping):
                        return bool(value)
                    if isinstance(value, list):
                        return any(isinstance(item, Mapping) for item in value)
                    return False

                if not has_usable_event(out.get(source_key)) and not has_usable_event(
                    modifier_payload.get(source_key)
                ):
                    start_match = re.search(
                        r"(?:从|from)\s*(\d+(?:\.\d+)?)\s*(?:秒|s)",
                        request.request,
                        flags=re.IGNORECASE,
                    )
                    if start_match:
                        event["start_s"] = float(start_match.group(1))
                    event_parameters = (
                        dict(event.get("parameters") or {})
                        if isinstance(event.get("parameters"), Mapping)
                        else {}
                    )
                    event["parameters"] = {
                        **catalog_event_parameters,
                        **explicit_event_parameters,
                    }
                    out[source_key] = [
                        _canonical_event_to_legacy(
                            event,
                            kind=explicit_kind,
                            index=0,
                        )
                    ]
            except (KeyError, StopIteration, TypeError, ValueError):
                pass

    def normalized_unit_severity(value: Any) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return 1.0
        if 1.0 < numeric <= 100.0:
            numeric /= 100.0
        return min(1.0, max(0.0, numeric))

    # Normalize task_type aliases commonly invented by LLMs.
    task_type = out.get("task_type")
    if isinstance(task_type, str):
        alias_map = {
            "fault_simulation": None,
            "fault": None,
            "fault_case": None,
            "nominal_simulation": None,
            "nominal": None,
            "degradation_simulation": None,
            "degradation": None,
            "simulation": None,
            "component_simulation": "component",
            "subsystem_simulation": "subsystem",
            "orbit_simulation": "orbit_environment",
            "orbit_environment_simulation": "orbit_environment",
            "whole_spacecraft_simulation": "whole_spacecraft",
        }
        if task_type in alias_map:
            replacement = alias_map[task_type]
            if replacement is None and contract is not None:
                replacement = _task_type_for_capability_contract(contract)
            if replacement is None:
                target = out.get("target") if isinstance(out.get("target"), Mapping) else {}
                replacement = str(target.get("level") or "component")
            out["task_type"] = replacement
    elif contract is not None:
        out["task_type"] = _task_type_for_capability_contract(contract)

    # Capability wins over model-provided target metadata.  Remove unknown keys
    # such as target.domain because the strict schema forbids them.
    target = dict(out.get("target") or {}) if isinstance(out.get("target"), Mapping) else {}
    if contract is not None:
        target["level"] = contract.target_level
        target["name"] = contract.target_name
    target["mode"] = _effect_mode_from_spec(out)
    _strip_keys(target, {"level", "name", "mode"})
    if target:
        out["target"] = target

    # Normalize simulation aliases and remove keys not allowed by schema.
    sim = dict(out.get("simulation") or {}) if isinstance(out.get("simulation"), Mapping) else {}
    if "sample_s" not in sim:
        for alias in ("sample_interval_s", "sampling_interval_s", "step_s", "time_step_s", "dt_s"):
            if alias in sim:
                sim["sample_s"] = sim.get(alias)
                break
    if "duration_s" not in sim:
        for alias in ("simulation_duration_s", "total_duration_s", "end_time_s"):
            if alias in sim:
                sim["duration_s"] = sim.get(alias)
                break
    _strip_keys(sim, {"level", "subsystem", "duration_s", "step_s", "sample_s", "random_seed", "seed", "backend", "time_base", "epoch_utc", "time_system", "solver"})
    if contract is not None:
        canonical_level = _task_type_for_capability_contract(contract)
        sim.setdefault("level", canonical_level)
        if canonical_level == "subsystem":
            sim.setdefault("subsystem", contract.target_name)
    else:
        sim.setdefault("level", str(out.get("task_type") or "component"))
        if sim.get("level") == "subsystem":
            target_name = str(target.get("name") or "").strip()
            if target_name:
                sim.setdefault("subsystem", target_name)
    sim.setdefault("duration_s", (_extract_seconds(request.request, ["duration", "simulate", "simulation", "仿真", "时长", "运行"]) or _extract_seconds(request.request, ["持续"]) or 600.0))
    sim.setdefault("sample_s", _extract_seconds(request.request, ["sample", "sampling", "采样", "输出间隔", "间隔"]) or min(10.0, float(sim.get("duration_s") or 10.0)))
    sim.setdefault("step_s", min(float(sim.get("sample_s") or 1.0), float(sim.get("step_s") or sim.get("sample_s") or 1.0)))
    if "random_seed" not in sim and "seed" in sim:
        sim["random_seed"] = sim.pop("seed")
    sim.setdefault("random_seed", 0)
    accepted_backends: list[str] = []
    if contract is not None:
        implementation = contract.data.get("implementation") if isinstance(contract.data.get("implementation"), Mapping) else {}
        accepted_backends = [str(item) for item in implementation.get("accepted_task_backends", []) if str(item).strip()]
    if accepted_backends:
        if str(sim.get("backend") or "").strip() not in accepted_backends:
            sim["backend"] = accepted_backends[0]
    else:
        sim.setdefault("backend", "python")
    sim.setdefault("time_base", "simulation_seconds")
    sim.setdefault("time_system", "UTC")
    out["simulation"] = sim

    # Normalize outputs: remove model-requested trace field lists; adapter output
    # schemas are authoritative.
    outputs = dict(out.get("outputs") or {}) if isinstance(out.get("outputs"), Mapping) else {}
    _strip_keys(outputs, {"output_root", "trace_format", "record_fields", "qoi", "files", "plots", "include_summary", "include_trace", "include_labels", "include_manifest"})
    outputs.setdefault("output_root", str(request.output_root or Path("datasets") / str(out.get("task_id", "generated"))))
    outputs.setdefault("trace_format", "csv")
    outputs.setdefault("include_summary", True)
    outputs.setdefault("include_trace", True)
    outputs.setdefault("include_labels", True)
    outputs.setdefault("include_manifest", True)
    out["outputs"] = outputs

    # Optional profile/vector parameters with empty container defaults mean
    # "use the scalar/default path".  If a model echoes those placeholders,
    # omit them because adapters correctly reject explicitly empty profiles.
    if contract is not None and isinstance(out.get("parameters"), dict):
        parameter_contracts = (
            contract.data.get("parameters")
            if isinstance(contract.data.get("parameters"), Mapping)
            else {}
        )
        for name, value in list(out["parameters"].items()):
            field_contract = parameter_contracts.get(name)
            if (
                isinstance(field_contract, Mapping)
                and not field_contract.get("required")
                and isinstance(value, (list, dict))
                and not value
            ):
                out["parameters"].pop(name)

    # Orbit-environment tasks require a top-level orbit_environment object.
    # DeepSeek-style outputs often put these fields under parameters because
    # they are capability parameters; normalize before strict validation.
    _normalize_orbit_environment_payload(out)

    if explicit_effect:
        target_mode = str(target.get("mode") or "")
        if target_mode == "fault" and isinstance(out.get("faults"), list):
            for item in out["faults"]:
                if isinstance(item, dict):
                    item["fault_type"] = explicit_effect
                    item["target"] = contract.target_name
                    item["target_type"] = _target_type_for_capability(contract)
        elif target_mode == "degradation" and isinstance(out.get("degradations"), list):
            for item in out["degradations"]:
                if isinstance(item, dict):
                    item["degradation_type"] = explicit_effect
                    item["target"] = contract.target_name
                    item["target_type"] = _target_type_for_capability(contract)
        elif target_mode == "constraint" and isinstance(out.get("constraints"), list):
            for item in out["constraints"]:
                if isinstance(item, dict):
                    item["constraint_type"] = explicit_effect
                    item["target"] = contract.target_name
                    item["target_type"] = _target_type_for_capability(contract)

    # Solar-panel degradation tasks require structured degradation payloads, not
    # LLM-style event lists.
    _normalize_solar_panel_degradations(out, request)

    # Normalize fault objects and remove additional properties.  Prefer explicit
    # aliases from the model, then request text, then a safe default.
    faults = out.get("faults")
    if isinstance(faults, list):
        onset_from_request = _extract_seconds(request.request, ["onset", "fault at", "故障在", "发生", "注入", "开始"])
        normalized_faults: list[dict[str, Any]] = []
        for i, item in enumerate(faults):
            if not isinstance(item, Mapping):
                continue
            fault = dict(item)
            if "onset_time_s" not in fault or float(fault.get("onset_time_s") or 0.0) == 0.0:
                for alias in ("trigger_time_s", "fault_time_s", "injection_time_s", "start_time_s", "time_s"):
                    if alias in fault:
                        fault["onset_time_s"] = fault.get(alias)
                        break
                else:
                    if onset_from_request is not None:
                        fault["onset_time_s"] = onset_from_request
            fault.setdefault("fault_id", f"f_{i:03d}")
            if contract is not None:
                fault.setdefault("target", contract.target_name)
                fault.setdefault("target_type", _target_type_for_capability(contract))
            else:
                fault.setdefault("target", target.get("name", "unknown"))
                fault.setdefault("target_type", target.get("name", "unknown"))
            fault.setdefault("fault_type", "open_circuit" if (contract and contract.capability_id == "component.battery.v1") else "unknown")
            fault.setdefault("onset_time_s", 0.0)
            # If the user only says a fault is injected at a time, keep it
            # persistent.  Preserve explicit positive duration from the model.
            if "duration_s" not in fault:
                fault["duration_s"] = -1
            fault["magnitude"] = normalized_unit_severity(fault.get("magnitude", 1.0))
            _strip_keys(fault, {"fault_id", "target", "target_type", "fault_type", "onset_time_s", "duration_s", "magnitude", "parameters", "label"})
            normalized_faults.append(fault)
        out["faults"] = normalized_faults

    # Explicit contract effects are native runtime events.  Project legacy LLM
    # list shapes into the audited modifier representation before validation so
    # canonical migration preserves category, time window and runtime delivery.
    if explicit_effect:
        target_mode = str(target.get("mode") or "")
        source_key = {
            "fault": "faults",
            "degradation": "degradations",
            "constraint": "constraints",
        }.get(target_mode)
        type_key = {
            "fault": "fault_type",
            "degradation": "degradation_type",
            "constraint": "constraint_type",
        }.get(target_mode)
        source_rows = out.get(source_key) if source_key else None
        modifiers = dict(out.get("modifiers") or {}) if isinstance(out.get("modifiers"), Mapping) else {}
        modifier_rows = modifiers.get(source_key) if source_key else None
        # Capability-specific structured payloads (for example
        # degradations.eps.solar_panel) are already canonical legacy input and
        # must not be replaced by an empty generic modifier list.
        if not isinstance(source_rows, (list, Mapping)) or (
            isinstance(source_rows, list) and not source_rows
        ):
            source_rows = modifier_rows
        if source_key and type_key and isinstance(source_rows, list) and source_rows:
            promoted: list[dict[str, Any]] = []
            for index, item in enumerate(source_rows):
                if not isinstance(item, Mapping):
                    continue
                start = float(item.get("onset_time_s", item.get("start_s", 0.0)) or 0.0)
                promoted.append({
                    "modifier_id": str(
                        item.get("modifier_id")
                        or item.get(f"{target_mode}_id")
                        or f"{target_mode}_{index + 1}"
                    ),
                    "target": contract.target_name,
                    "target_type": _target_type_for_capability(contract),
                    type_key: explicit_effect,
                    "onset_time_s": start,
                    "duration_s": item.get("duration_s", -1.0),
                    "severity": normalized_unit_severity(
                        item.get("severity", item.get("magnitude", 1.0))
                    ),
                    "parameters": {
                        **catalog_event_parameters,
                        **dict(item.get("parameters") or {}),
                        **explicit_event_parameters,
                    },
                })
            modifiers[source_key] = promoted
            out["modifiers"] = modifiers
            out.pop(source_key, None)

    # Apply S5 source-alignment aliases/defaults after high-confidence capability
    # routing.  This maps user/LLM parameter names to canonical TaskSpec fields
    # without letting the model choose Python imports or source APIs.
    if isinstance(out.get("capability_id"), str):
        try:
            out = align_task_spec_to_source(out, fill_defaults=True).task_spec
        except Exception as exc:
            record_runtime_diagnostic(
                code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                location='src/sat_sim/capability_agent.py:_coerce_common_llm_aliases:01',
                exception=exc,
                strict=False,
            )
        try:
            from .taskspec_pruning import prune_task_spec_for_capability
            out = prune_task_spec_for_capability(out).task_spec
        except Exception as exc:
            record_runtime_diagnostic(
                code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                location='src/sat_sim/capability_agent.py:_coerce_common_llm_aliases:02',
                exception=exc,
                strict=False,
            )

    if protected_assurance is not None:
        out["assurance"] = protected_assurance
    if protected_mission is not None:
        out["mission"] = protected_mission
    if migration_payload is not None:
        _apply_capability_migration_metadata(out, migration_payload)

    # Keep top-level schema compact.  Drop common metadata fields that belong in
    # capability contracts, not TaskSpecs.
    allowed_top = {"schema_version", "task_id", "task_type", "capability_id", "target", "simulation", "mission", "spacecraft", "orbit_environment", "parameters", "faults", "degradations", "constraints", "modifiers", "outputs", "campaign", "validation", "assurance", "metadata", "tags"}
    _strip_keys(out, allowed_top)
    return out

def _normalize_external_draft(spec: Mapping[str, Any], request: CapabilityAgentRequest) -> dict[str, Any]:
    """Annotate and normalize a model-generated TaskSpec draft."""

    out = _coerce_common_llm_aliases(spec, request)
    out.setdefault("schema_version", TASK_SPEC_VERSION)
    if request.task_id:
        out["task_id"] = request.task_id
    out.setdefault("task_id", f"gen_model_task_{_short_hash(request.request)}")
    out.setdefault("metadata", {})
    if isinstance(out["metadata"], dict):
        out["metadata"].setdefault("agent", {})
        out["metadata"]["agent"].update({
            "generated_by": _agent_version_for_backend(request.backend),
            "backend": "external_draft" if request.draft_spec_path else request.backend,
            "request_sha256": hashlib.sha256(request.request.encode("utf-8")).hexdigest(),
            "request_excerpt": request.request[:240],
        })
    if request.output_root is not None:
        outputs = dict(out.get("outputs") or {})
        outputs["output_root"] = str(request.output_root)
        out["outputs"] = outputs
    out.setdefault("outputs", {})
    if isinstance(out["outputs"], dict):
        out["outputs"].setdefault("output_root", str(Path("datasets") / str(out.get("task_id", "generated"))))
        out["outputs"].setdefault("trace_format", "csv")
        out["outputs"].setdefault("include_summary", True)
        out["outputs"].setdefault("include_trace", True)
        out["outputs"].setdefault("include_labels", True)
        out["outputs"].setdefault("include_manifest", True)
    return out


def _record_representation_repair(out: dict[str, Any], code: str) -> None:
    metadata = out.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        out["metadata"] = metadata = {}
    agent = metadata.setdefault("agent", {})
    if not isinstance(agent, dict):
        metadata["agent"] = agent = {}
    codes = agent.setdefault("representation_repair_reason_codes", [])
    if isinstance(codes, list) and code not in codes:
        codes.append(code)


def _ensure_representation_scaffolds(out: dict[str, Any]) -> None:
    """Add representation-only scaffolds required by the legacy validator."""
    if out.get("task_type") == "whole_spacecraft" and not isinstance(out.get("spacecraft"), Mapping):
        out["spacecraft"] = {"mission": {"template": str(out.get("capability_id") or "whole_spacecraft")}}
        _record_representation_repair(out, "REPRESENTATION_SAFE_SPACECRAFT_SCAFFOLD_ADDED")
    elif out.get("task_type") == "whole_spacecraft" and isinstance(out.get("spacecraft"), dict):
        spacecraft = out["spacecraft"]
        mission = spacecraft.setdefault("mission", {})
        if isinstance(mission, dict) and not mission.get("template"):
            mission["template"] = str(out.get("capability_id") or "whole_spacecraft")
            _record_representation_repair(out, "REPRESENTATION_SAFE_SPACECRAFT_MISSION_TEMPLATE_ADDED")
    if out.get("task_type") == "orbit_environment" and not isinstance(out.get("orbit_environment"), Mapping):
        out["orbit_environment"] = {"step_s": 1.0}
        _record_representation_repair(out, "REPRESENTATION_SAFE_ORBIT_ENVIRONMENT_SCAFFOLD_ADDED")

def _repair_capability_spec_once(spec: Mapping[str, Any], validation: ValidationResult, request: CapabilityAgentRequest) -> dict[str, Any]:
    out = _coerce_common_llm_aliases(_normalize_external_draft(spec, request), request)
    out.setdefault("simulation", {"duration_s": 600.0, "sample_s": 60.0, "seed": 0, "backend": "python"})
    if isinstance(out.get("simulation"), dict):
        sim = out["simulation"]
        sim.setdefault("duration_s", 600.0)
        sim.setdefault("sample_s", 60.0)
        sim.setdefault("seed", 0)
        sim.setdefault("backend", "python")
        try:
            if float(sim.get("duration_s", 0)) <= 0:
                sim["duration_s"] = 600.0
            if float(sim.get("sample_s", 0)) <= 0:
                sim["sample_s"] = 60.0
            if float(sim.get("sample_s", 0)) > float(sim.get("duration_s", 0)):
                sim["sample_s"] = float(sim["duration_s"])
        except Exception:
            sim["duration_s"] = 600.0
            sim["sample_s"] = 60.0
            sim["backend"] = "python"
    capability_id = out.get("capability_id")
    if isinstance(capability_id, str):
        try:
            contract = get_capability(capability_id)
            out.setdefault("target", {})
            if isinstance(out.get("target"), dict):
                out["target"].setdefault("level", contract.target_level)
                out["target"].setdefault("name", contract.target_name)
                out["target"]["mode"] = _effect_mode_from_spec(out)
            expected_task_type = _task_type_for_capability_contract(contract)
            if out.get("task_type") != expected_task_type:
                out["task_type"] = expected_task_type
        except Exception as exc:
            record_runtime_diagnostic(
                code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                location='src/sat_sim/capability_agent.py:_repair_capability_spec_once:01',
                exception=exc,
                strict=False,
            )
    _ensure_representation_scaffolds(out)
    faults = out.get("faults")
    if isinstance(faults, list):
        duration_s = None
        try:
            duration_s = float(out.get("simulation", {}).get("duration_s"))  # type: ignore[union-attr]
        except Exception as exc:
            record_runtime_diagnostic(
                code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                location='src/sat_sim/capability_agent.py:_repair_capability_spec_once:02',
                exception=exc,
                strict=False,
            )
        seen: set[str] = set()
        for i, fault in enumerate(faults):
            if not isinstance(fault, dict):
                continue
            fault.setdefault("fault_id", f"f_{i:03d}")
            if str(fault["fault_id"]) in seen:
                fault["fault_id"] = f"{fault['fault_id']}_{i}"
            seen.add(str(fault["fault_id"]))
            if isinstance(capability_id, str):
                try:
                    contract = get_capability(capability_id)
                    fault.setdefault("target", contract.target_name)
                    fault.setdefault("target_type", _target_type_for_capability(contract))
                except Exception as exc:
                    record_runtime_diagnostic(
                        code='AGENT_OPTIONAL_NORMALIZATION_SKIPPED',
                        category=DiagnosticCategory.AGENT_NORMALIZATION_FALLBACK,
                        location='src/sat_sim/capability_agent.py:_repair_capability_spec_once:03',
                        exception=exc,
                        strict=False,
                    )
            if "onset_time_s" not in fault or float(fault.get("onset_time_s") or 0.0) == 0.0:
                for alias in ("trigger_time_s", "fault_time_s", "injection_time_s", "start_time_s", "time_s"):
                    if alias in fault:
                        fault["onset_time_s"] = fault.get(alias)
                        break
            fault.setdefault("duration_s", -1)
            fault.setdefault("magnitude", 1.0)
            try:
                onset = max(0.0, float(fault.get("onset_time_s", 0.0)))
                if duration_s is not None and onset > duration_s:
                    onset = duration_s * 0.5
                fault["onset_time_s"] = onset
            except Exception:
                fault["onset_time_s"] = 0.0
            _strip_keys(fault, {"fault_id", "target", "target_type", "fault_type", "onset_time_s", "duration_s", "magnitude", "parameters", "label"})
    return out


def _template_fallback_for_planner_route(
    request: CapabilityAgentRequest,
    context: Mapping[str, Any],
    *,
    selected_capability_id: str | None,
) -> dict[str, Any] | None:
    """Build a deterministic template draft for the planner-selected route.

    A-close uses this as a safety net for command/LLM backends whose draft
    chooses a different capability from the deterministic planner.  The fallback
    keeps the pipeline source-grounded: it still emits a normal TaskSpec and
    never executes model-authored Python.
    """

    if not selected_capability_id:
        return None
    try:
        fallback = CapabilityTemplateBackend().draft_task_spec(request=request, context=context)
    except Exception:
        return None
    if fallback.get("capability_id") != selected_capability_id:
        return None
    return fallback


def _backend_from_request(request: CapabilityAgentRequest) -> CapabilityAgentBackend:
    if request.backend == "template":
        return CapabilityTemplateBackend()
    if request.backend == "command":
        return TextModelTaskSpecBackend(
            name="command",
            config=LLMCallConfig(
                backend="command",
                command=request.model_command,
                timeout_s=request.model_timeout_s,
            ),
        )
    if request.backend == "openai":
        return TextModelTaskSpecBackend(
            name="openai",
            config=LLMCallConfig(
                backend="openai",
                model=request.model_name,
                base_url=request.model_base_url,
                api_key_env=request.model_api_key_env,
                timeout_s=request.model_timeout_s,
                temperature=request.model_temperature,
                max_output_tokens=request.model_max_output_tokens,
                structured_output=request.model_structured_output,
            ),
        )
    if request.backend in {"openai_compatible", "qwen", "vllm"}:
        return TextModelTaskSpecBackend(
            name=request.backend,
            config=LLMCallConfig(
                backend=request.backend,
                model=request.model_name,
                base_url=request.model_base_url,
                api_key_env=request.model_api_key_env,
                timeout_s=request.model_timeout_s,
                temperature=request.model_temperature,
                seed=request.model_seed,
                max_output_tokens=request.model_max_output_tokens,
                structured_output=request.model_structured_output or "json_object",
            ),
        )
    if request.backend == "deepseek":
        api_key_env = request.model_api_key_env if request.model_api_key_env and request.model_api_key_env != "OPENAI_API_KEY" else "DEEPSEEK_API_KEY"
        return TextModelTaskSpecBackend(
            name="deepseek",
            config=LLMCallConfig(
                backend="deepseek",
                model=request.model_name,
                base_url=request.model_base_url,
                api_key_env=api_key_env,
                timeout_s=request.model_timeout_s,
                temperature=request.model_temperature,
                max_output_tokens=request.model_max_output_tokens,
                structured_output=request.model_structured_output,
            ),
        )
    raise ValueError(f"unsupported capability Agent backend: {request.backend}")


def _planner_generation_consistency_issue(plan: Any, spec: Mapping[str, Any]) -> ValidationIssue | None:
    selected = getattr(plan, "selected_capability_id", None)
    if not getattr(plan, "supported", False) or not selected:
        return None
    generated = spec.get("capability_id") if isinstance(spec, Mapping) else None
    if generated == selected:
        return None
    metadata = spec.get("metadata") if isinstance(spec, Mapping) else {}
    replacement = metadata.get("route_replacement") if isinstance(metadata, Mapping) else None
    if isinstance(replacement, Mapping) and replacement.get("explicit_route_replacement") is True:
        reason = str(replacement.get("replacement_reason_code") or "")
        if reason in {"ROUTE_REPLACED_BY_COMPOSITE_POLICY", "UNSUPPORTED_FULL_SPACECRAFT_REQUEST", "USER_CONFIRMED_ALTERNATIVE"}:
            return None
    return ValidationIssue(
        "error",
        "$.capability_id",
        f"Generated capability_id {generated!r} does not match planner-selected capability_id {selected!r}.",
        "planner_generation_mismatch",
    )

def run_capability_script_generation(request: CapabilityAgentRequest) -> CapabilityScriptGenerationResult:
    """Run the A1 natural-language-to-script pipeline."""

    output_dir = Path(request.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    steps: list[CapabilityAgentStep] = []

    context = capability_agent_context_payload(allowed_capabilities=request.allowed_capabilities, include_contracts=True)
    context_json_path = output_dir / "capability_context.json"
    write_json(context_json_path, context)
    files["capability_context"] = str(context_json_path)
    steps.append(CapabilityAgentStep("context", "complete", "Loaded capability contracts for Agent use.", {"capabilities": list(request.allowed_capabilities)}))

    request_path = output_dir / "user_request.txt"
    request_path.write_text(request.request, encoding="utf-8")
    files["user_request"] = str(request_path)

    prompt_text = build_capability_agent_prompt(
        request.request,
        allowed_capabilities=request.allowed_capabilities,
        advisory_context=request.advisory_context,
    )
    prompt_path = output_dir / "agent_prompt.md"
    prompt_path.write_text(prompt_text, encoding="utf-8")
    files["agent_prompt"] = str(prompt_path)
    steps.append(CapabilityAgentStep("prompt", "complete", "Wrote capability-constrained prompt for an external LLM."))

    from .capability_planner import plan_capability_for_request

    plan = plan_capability_for_request(request.request, allowed_capabilities=request.allowed_capabilities)
    plan_path = output_dir / "capability_plan.json"
    write_json(plan_path, plan.to_dict())
    files["capability_plan"] = str(plan_path)
    steps.append(CapabilityAgentStep(
        "plan",
        "complete" if plan.supported else "boundary",
        plan.explanation,
        {
            "selected_capability_id": plan.selected_capability_id,
            "supported": plan.supported,
            "recommended_action": plan.recommended_action,
            "unsupported_requirements": [item.to_dict() for item in plan.unsupported_requirements],
        },
    ))

    if not plan.supported:
        task_id = request.task_id or f"gen_unsupported_{_short_hash(request.request)}"
        current = {
            "schema_version": TASK_SPEC_VERSION,
            "task_id": task_id,
            # This is a non-runnable rejection envelope. Keep its representation
            # at component level; the best partial route remains in planner
            # metadata and must not leak into an executable model identity.
            "task_type": "component",
            "simulation": {
                "level": "component",
                "duration_s": 600.0,
                "sample_s": 60.0,
                "seed": 0,
                "backend": "python",
            },
            "outputs": {
                "output_root": str(request.output_root or Path("datasets") / task_id),
                "trace_format": "csv",
                "include_summary": True,
                "include_trace": True,
                "include_labels": True,
                "include_manifest": True,
            },
            "metadata": {
                "agent": {
                    "generated_by": _agent_version_for_backend(request.backend),
                    "backend": request.backend,
                    "request_sha256": hashlib.sha256(request.request.encode("utf-8")).hexdigest(),
                    "request_excerpt": request.request[:240],
                },
                "planner": plan.to_dict(),
                "representation": "non_runnable_rejection_envelope",
            },
        }
        # Do not emit a runnable capability_id for unsupported requests.  The
        # planner result still records the best partial route, but the generated
        # TaskSpec placeholder must remain non-executable until the user narrows
        # the request to implemented capability boundaries.
        validation = ValidationResult((ValidationIssue("error", "$.request", plan.explanation, "capability_planner"),))
        task_yaml = output_dir / DEFAULT_TASK_YAML
        task_json = output_dir / "generated_task.json"
        validation_json = output_dir / "validation.json"
        _write_yaml(task_yaml, current)
        write_json(task_json, current)
        write_json(validation_json, validation.to_dict())
        files.update({"generated_task_yaml": str(task_yaml), "generated_task_json": str(task_json), "validation": str(validation_json)})
        steps.append(CapabilityAgentStep("validate", "failed", "Planner boundary prevented unsupported TaskSpec generation.", validation.to_dict()))
        result = CapabilityScriptGenerationResult(
            ok=False,
            request=request,
            task_spec=current,
            validation=validation,
            compiled=None,
            script=None,
            run_result=None,
            output_dir=output_dir,
            files=files,
            steps=tuple(steps),
        )
        report_path = output_dir / "capability_agent_report.json"
        files["capability_agent_report"] = str(report_path)
        result = CapabilityScriptGenerationResult(
            ok=False,
            request=request,
            task_spec=current,
            validation=validation,
            compiled=None,
            script=None,
            run_result=None,
            output_dir=output_dir,
            files=files,
            steps=tuple(steps),
        )
        write_json(report_path, result.to_dict())
        return result

    def _return_draft_failure(*, exc: Exception, backend_name: str | None = None) -> CapabilityScriptGenerationResult:
        task_id = request.task_id or f"gen_invalid_llm_output_{_short_hash(request.request)}"
        current = {
            "schema_version": TASK_SPEC_VERSION,
            "task_id": task_id,
            "task_type": plan.selected_task_type or "component",
            "simulation": {"duration_s": 600.0, "sample_s": 60.0, "seed": 0, "backend": "python"},
            "outputs": {
                "output_root": str(request.output_root or Path("datasets") / task_id),
                "trace_format": "csv",
                "include_summary": True,
                "include_trace": True,
                "include_labels": True,
                "include_manifest": True,
            },
            "metadata": {
                "agent": {
                    "generated_by": _agent_version_for_backend(request.backend),
                    "backend": request.backend,
                    "request_sha256": hashlib.sha256(request.request.encode("utf-8")).hexdigest(),
                    "request_excerpt": request.request[:240],
                },
                "planner": plan.to_dict(),
                "llm_output_error": {
                    "backend": backend_name or request.backend,
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                    "policy": "record_invalid_model_output_without_fallback",
                },
            },
        }
        validation = ValidationResult((ValidationIssue("error", "$.model_output", f"LLM TaskSpec draft could not be parsed: {exc}", "llm_output_parse"),))
        task_yaml = output_dir / DEFAULT_TASK_YAML
        task_json = output_dir / "generated_task.json"
        validation_json = output_dir / "validation.json"
        _write_yaml(task_yaml, current)
        write_json(task_json, current)
        write_json(validation_json, validation.to_dict())
        files.update({"generated_task_yaml": str(task_yaml), "generated_task_json": str(task_json), "validation": str(validation_json)})
        steps.append(CapabilityAgentStep("draft", "failed", "LLM output was not valid literal JSON/YAML TaskSpec data; recorded raw output and stopped before compile/export.", {"backend": backend_name or request.backend, "error": str(exc)}))
        result = CapabilityScriptGenerationResult(
            ok=False,
            request=request,
            task_spec=current,
            validation=validation,
            compiled=None,
            script=None,
            run_result=None,
            output_dir=output_dir,
            files=files,
            steps=tuple(steps),
        )
        report_path = output_dir / "capability_agent_report.json"
        files["capability_agent_report"] = str(report_path)
        result = CapabilityScriptGenerationResult(
            ok=False,
            request=request,
            task_spec=current,
            validation=validation,
            compiled=None,
            script=None,
            run_result=None,
            output_dir=output_dir,
            files=files,
            steps=tuple(steps),
        )
        write_json(report_path, result.to_dict())
        return result

    backend: CapabilityAgentBackend | None = None
    if request.draft_spec_path is not None:
        try:
            draft = load_model_draft(request.draft_spec_path)
        except Exception as exc:
            return _return_draft_failure(exc=exc, backend_name="draft_spec_path")
        steps.append(CapabilityAgentStep("draft", "complete", "Loaded TaskSpec draft from external LLM output file.", {"draft_spec_path": str(request.draft_spec_path)}))
    else:
        backend = _backend_from_request(request)
        try:
            draft = backend.draft_task_spec(request=request, context=context)
        except Exception as exc:
            raw_text = getattr(backend, "last_raw_output", None)
            call_result = getattr(backend, "last_call", None)
            if raw_text is not None:
                raw_path = output_dir / "raw_model_output.txt"
                raw_path.write_text(str(raw_text), encoding="utf-8")
                files["raw_model_output"] = str(raw_path)
            if call_result is not None:
                call_path = output_dir / "model_call.json"
                write_json(call_path, call_result.to_dict())
                files["model_call"] = str(call_path)
            fallback = _template_fallback_for_planner_route(request, context, selected_capability_id=plan.selected_capability_id) if plan.selected_capability_id else None
            if fallback is None:
                return _return_draft_failure(exc=exc, backend_name=getattr(backend, "name", request.backend))
            draft = fallback
            metadata = draft.setdefault("metadata", {}) if isinstance(draft, dict) else {}
            if isinstance(metadata, dict):
                metadata.setdefault("agent", {})
                if isinstance(metadata.get("agent"), dict):
                    metadata["agent"].update({
                        "model_fallback_used": True,
                        "model_fallback_reason": exc.__class__.__name__,
                        "model_fallback_message": str(exc)[:500],
                        "reason_code": "MODEL_DRAFT_UNAVAILABLE_TEMPLATE_FALLBACK",
                    })
            steps.append(CapabilityAgentStep(
                "model_fallback", "complete",
                "模型未在超时或输出约束内返回可解析 TaskSpec，已使用规划器选定能力的确定性模板生成有效配置。",
                {"backend": getattr(backend, "name", request.backend), "error": str(exc), "capability_id": plan.selected_capability_id},
            ))
        raw_text = getattr(backend, "last_raw_output", None)
        call_result = getattr(backend, "last_call", None)
        if raw_text is not None:
            raw_path = output_dir / "raw_model_output.txt"
            raw_path.write_text(str(raw_text), encoding="utf-8")
            files["raw_model_output"] = str(raw_path)
        if call_result is not None:
            call_path = output_dir / "model_call.json"
            write_json(call_path, call_result.to_dict())
            files["model_call"] = str(call_path)
            evidence = call_result.invocation_evidence()
            steps.append(CapabilityAgentStep(
                "model_invocation",
                "complete" if evidence.get("verified") else "failed",
                "Recorded hash-backed model invocation evidence." if evidence.get("verified") else "Model invocation was requested but no verifiable response evidence was produced.",
                evidence,
            ))
        steps.append(CapabilityAgentStep("draft", "complete", f"Generated TaskSpec draft with backend={backend.name}.", {"capability_id": _external_draft_capability_id(draft), "backend": backend.name}))

    if request.draft_spec_path is None and request.backend != "template" and plan.supported and plan.selected_capability_id:
        draft_capability_id = _external_draft_capability_id(draft)
        explicit_selected = re.search(
            rf"\bcapability_id\s*=\s*{re.escape(plan.selected_capability_id)}(?:\b|$)",
            request.request,
            flags=re.IGNORECASE,
        )
        if explicit_selected and _capability_id_punctuation_alias(
            draft_capability_id,
            plan.selected_capability_id,
        ):
            draft = _replace_external_draft_capability_id(
                draft,
                plan.selected_capability_id,
            )
            steps.append(CapabilityAgentStep(
                "capability_id_alias_normalization",
                "complete",
                "Restored the explicitly requested capability_id after punctuation-only model drift.",
                {
                    "model_capability_id": draft_capability_id,
                    "requested_capability_id": plan.selected_capability_id,
                },
            ))
            draft_capability_id = plan.selected_capability_id
        if draft_capability_id != plan.selected_capability_id:
            fallback = _template_fallback_for_planner_route(request, context, selected_capability_id=plan.selected_capability_id)
            if fallback is not None:
                steps.append(CapabilityAgentStep(
                    "planner_route_repair",
                    "complete",
                    "Replaced mismatched model draft with deterministic template for the planner-selected capability.",
                    {
                        "model_capability_id": draft_capability_id,
                        "planner_capability_id": plan.selected_capability_id,
                        "fallback_capability_id": fallback.get("capability_id"),
                    },
                ))
                draft = fallback

    current = _normalize_external_draft(draft, request)
    validation = validate_task_spec(current)
    if (
        not validation.ok
        and request.max_repair_attempts > 0
        and request.draft_spec_path is None
        and request.backend != "template"
        and backend is not None
        and hasattr(backend, "draft_task_spec_with_validation_errors")
    ):
        try:
            retry_draft = backend.draft_task_spec_with_validation_errors(  # type: ignore[attr-defined]
                request=request,
                context=context,
                previous_draft=current,
                validation=validation,
            )
            retry_call = getattr(backend, "last_call", None)
            if retry_call is not None:
                retry_path = output_dir / "model_call_retry.json"
                write_json(retry_path, retry_call.to_dict())
                files["model_call_retry"] = str(retry_path)
                retry_evidence = retry_call.invocation_evidence()
                steps.append(CapabilityAgentStep(
                    "model_invocation_retry",
                    "complete" if retry_evidence.get("verified") else "failed",
                    "Recorded hash-backed retry invocation evidence.",
                    retry_evidence,
                ))
            retry_current = _normalize_external_draft(retry_draft, request)
            retry_validation = validate_task_spec(retry_current)
            if retry_validation.ok or len(retry_validation.errors) <= len(validation.errors):
                current = retry_current
                validation = retry_validation
            steps.append(CapabilityAgentStep(
                "llm_validator_retry",
                "complete" if retry_validation.ok else "partial",
                "Asked the model once to correct validator-reported representation errors.",
                {"ok_after_retry": retry_validation.ok},
            ))
        except Exception as exc:
            steps.append(CapabilityAgentStep(
                "llm_validator_retry",
                "skipped",
                "One-shot model correction was unavailable or failed; continuing with deterministic local repair.",
                {"error": str(exc)},
            ))
    repair_count = 0
    while not validation.ok and repair_count < max(0, request.max_repair_attempts):
        current = _repair_capability_spec_once(current, validation, request)
        repair_count += 1
        validation = validate_task_spec(current)
        steps.append(CapabilityAgentStep("repair", "complete" if validation.ok else "partial", f"Applied conservative repair attempt {repair_count}.", {"ok_after_repair": validation.ok}))

    if (
        not validation.ok
        and request.max_repair_attempts > 0
        and request.draft_spec_path is None
        and request.backend != "template"
        and plan.supported
        and plan.selected_capability_id
    ):
        fallback = _template_fallback_for_planner_route(request, context, selected_capability_id=plan.selected_capability_id)
        if fallback is not None:
            current = _normalize_external_draft(fallback, request)
            validation = validate_task_spec(current)
            repair_count = 0
            while not validation.ok and repair_count < max(0, request.max_repair_attempts):
                current = _repair_capability_spec_once(current, validation, request)
                repair_count += 1
                validation = validate_task_spec(current)
            steps.append(CapabilityAgentStep(
                "planner_route_repair",
                "complete" if validation.ok else "failed",
                "Retried generation with deterministic template for the planner-selected capability after model draft validation failed.",
                {"planner_capability_id": plan.selected_capability_id, "ok_after_fallback": validation.ok},
            ))

    consistency_issue = _planner_generation_consistency_issue(plan, current)
    if consistency_issue is not None:
        validation = ValidationResult(tuple(list(validation.issues) + [consistency_issue]))
        steps.append(CapabilityAgentStep(
            "planner_generation_consistency",
            "failed",
            "Generated TaskSpec capability_id does not match deterministic planner route.",
            {"planner_capability_id": plan.selected_capability_id, "generated_capability_id": current.get("capability_id")},
        ))
    else:
        steps.append(CapabilityAgentStep(
            "planner_generation_consistency",
            "complete",
            "Generated capability route matches deterministic planner route or an allowed audited replacement.",
            {"planner_capability_id": plan.selected_capability_id, "generated_capability_id": current.get("capability_id")},
        ))

    task_yaml = output_dir / DEFAULT_TASK_YAML
    task_json = output_dir / "generated_task.json"
    validation_json = output_dir / "validation.json"
    _write_yaml(task_yaml, current)
    write_json(task_json, current)
    write_json(validation_json, validation.to_dict())
    files.update({"generated_task_yaml": str(task_yaml), "generated_task_json": str(task_json), "validation": str(validation_json)})

    compiled: CompiledTask | None = None
    script_result: ScriptExportResult | None = None
    run_result: TaskRunResult | None = None
    ok = False
    if validation.ok:
        compiled = compile_task_spec(current, validate=False)
        compiled_json = output_dir / "compiled_task.json"
        write_json(compiled_json, compiled.to_dict())
        files["compiled_task"] = str(compiled_json)
        steps.append(CapabilityAgentStep("compile", "complete", "Compiled TaskSpec through trusted compiler.", {"runner": compiled.runner, "capability_id": current.get("capability_id")}))
        script_path = Path(request.script_output) if request.script_output is not None else output_dir / DEFAULT_SCRIPT_NAME
        script_result = export_runner_script(task_yaml, script_path, kind="capability-python", output_root=request.output_root)
        files["generated_script"] = str(script_result.output_path)
        script_json = output_dir / "script_export.json"
        write_json(script_json, script_result.to_dict())
        files["script_export"] = str(script_json)
        steps.append(CapabilityAgentStep("export_script", "complete", "Exported deterministic capability-python simulation script.", {"script": str(script_result.output_path)}))
        if request.run_task and not request.dry_run:
            run_result = execute_compiled_task(compiled, task_spec=current, output_root=request.output_root or compiled.outputs.get("output_root"), write_dataset=True)
            run_json = output_dir / "run_result.json"
            write_json(run_json, run_result.to_dict())
            files["run_result"] = str(run_json)
            steps.append(CapabilityAgentStep("run", "complete", "Executed generated TaskSpec through the unified execution port.", {"summary": run_result.summary}))
        elif request.run_task and request.dry_run:
            steps.append(CapabilityAgentStep("run", "skipped", "dry_run=True; generated task was not executed."))
        ok = True
    else:
        steps.append(CapabilityAgentStep("validate", "failed", "Draft TaskSpec did not pass validation.", validation.to_dict()))

    result = CapabilityScriptGenerationResult(
        ok=ok,
        request=request,
        task_spec=current,
        validation=validation,
        compiled=compiled,
        script=script_result,
        run_result=run_result,
        output_dir=output_dir,
        files=files,
        steps=tuple(steps),
    )
    report_path = output_dir / "capability_agent_report.json"
    files["capability_agent_report"] = str(report_path)
    result = CapabilityScriptGenerationResult(
        ok=ok,
        request=request,
        task_spec=current,
        validation=validation,
        compiled=compiled,
        script=script_result,
        run_result=run_result,
        output_dir=output_dir,
        files=files,
        steps=tuple(steps),
    )
    write_json(report_path, result.to_dict())
    return result


def generate_capability_script_from_text(
    request_text: str,
    *,
    output_dir: str | Path = "generated_scripts",
    examples_dir: str | Path = "examples",
    backend: str = "template",
    draft_spec_path: str | Path | None = None,
    allowed_capabilities: Sequence[str] = DEFAULT_ALLOWED_CAPABILITIES,
    task_id: str | None = None,
    output_root: str | Path | None = None,
    script_output: str | Path | None = None,
    max_repair_attempts: int = 2,
    run_task: bool = False,
    dry_run: bool = False,
    model_command: str | None = None,
    model_name: str | None = None,
    model_base_url: str | None = None,
    model_api_key_env: str = "OPENAI_API_KEY",
    model_timeout_s: float = 60.0,
    model_temperature: float | None = None,
    model_seed: int | None = None,
    model_max_output_tokens: int | None = None,
    model_structured_output: str = "json_object",
) -> CapabilityScriptGenerationResult:
    """Convenience wrapper for one-shot capability script generation."""

    req = CapabilityAgentRequest(
        request=request_text,
        output_dir=output_dir,
        examples_dir=examples_dir,
        backend=backend,
        draft_spec_path=draft_spec_path,
        allowed_capabilities=tuple(allowed_capabilities),
        task_id=task_id,
        output_root=output_root,
        script_output=script_output,
        max_repair_attempts=max_repair_attempts,
        run_task=run_task,
        dry_run=dry_run,
        model_command=model_command,
        model_name=model_name,
        model_base_url=model_base_url,
        model_api_key_env=model_api_key_env,
        model_timeout_s=model_timeout_s,
        model_temperature=model_temperature,
        model_seed=model_seed,
        model_max_output_tokens=model_max_output_tokens,
        model_structured_output=model_structured_output,
    )
    return run_capability_script_generation(req)


__all__ = [
    "A1_AGENT_VERSION",
    "A2_AGENT_VERSION",
    "A3_AGENT_VERSION",
    "A4_AGENT_VERSION",
    "ACTIVE_DEFAULT_CAPABILITIES",
    "DEFAULT_ALLOWED_CAPABILITIES",
    "LEGACY_COMPATIBILITY_ALLOWED_CAPABILITIES",
    "REAL_LLM_EVAL_ALLOWED_CAPABILITIES",
    "ROUTE_B_MODEL_LIBRARY_CAPABILITIES",
    "ROUTE_B_STRICT_ALLOWED_CAPABILITIES",
    "LLM_EXPOSURE_PROFILES",
    "allowed_capabilities_for_profile",
    "CapabilityAgentBackend",
    "CapabilityAgentRequest",
    "CapabilityAgentStep",
    "CapabilityScriptGenerationResult",
    "CapabilityTemplateBackend",
    "TextModelTaskSpecBackend",
    "CapabilityTemplateSelection",
    "build_capability_agent_prompt",
    "capability_agent_context_payload",
    "extract_task_spec_from_model_text",
    "generate_capability_script_from_text",
    "load_model_draft",
    "requested_health_mode",
    "run_capability_script_generation",
]
