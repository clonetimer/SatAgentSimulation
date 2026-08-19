"""Single product-facing Agent facade introduced by V20.

``capability_agent`` remains the backend implementation and compatibility API.
All product CLI/UI integrations should call this module so generation, canonical
migration, guards, compilation, script export and optional execution follow one
ordered and auditable path.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence
import copy
import math
import re

import yaml

from .agent_guards import AgentGuardReport, agent_tool_policy_payload, evaluate_agent_guards
from .capability_agent import (
    DEFAULT_ALLOWED_CAPABILITIES,
    CapabilityAgentRequest,
    run_capability_script_generation,
)
from .script_exporter import ScriptExportResult, export_runner_script
from .task_compiler import CompiledTask, compile_task_spec
from .task_models import CANONICAL_TASK_SPEC_VERSION, canonicalize_task_spec, migrate_legacy_task_spec
from .coupling_requirements import attach_required_couplings
from .task_runner import TaskRunResult
from .dataset_writer import DatasetWriteResult
from .run_bundle import RunExecutionResult, execute_prepared_run, prepare_run
from .execution_planner import PlanningResult, attach_plan_metadata, plan_task_spec
from .task_spec import write_json
from .task_validator import ValidationResult, validate_task_spec
from .nl_outputs import apply_output_intents_to_spec

AGENT_FACADE_VERSION = "v26.agent-facade.v2"


def _backend_identity(request: "AgentFacadeRequest", steps: Sequence["AgentFacadeStep"] = ()) -> dict[str, Any]:
    """Return backend identity from hash-backed invocation evidence.

    A selected backend or a successful step name is not proof of model use.
    Verification requires an explicit ``model_invocation`` evidence payload with
    request/response hashes produced by the backend result object.
    """
    backend = str(request.backend or "template").strip().lower()
    if backend == "template":
        return {
            "backend": backend,
            "backend_class": "deterministic_template",
            "model_backend_requested": False,
            "actual_model_used": False,
            "actual_model_execution_verified": True,
            "model_name": None,
            "claim": "rules_and_templates_not_llm",
            "invocation_evidence": None,
        }
    if backend == "command":
        backend_class = "external_model_command"
    elif backend in {"openai", "openai_compatible", "qwen", "vllm", "deepseek"}:
        backend_class = "actual_model_service"
    else:
        backend_class = "unknown_model_backend"
    evidence_rows = [
        dict(item.payload)
        for item in steps
        if str(item.name).startswith("backend.model_invocation") and isinstance(item.payload, Mapping)
    ]
    verified_rows = [
        row for row in evidence_rows
        if row.get("verified") is True
        and row.get("request_id")
        and row.get("response_sha256")
        and row.get("prompt_sha256")
    ]
    verified = bool(verified_rows)
    evidence = verified_rows[-1] if verified_rows else (evidence_rows[-1] if evidence_rows else None)
    return {
        "backend": backend,
        "backend_class": backend_class,
        "model_backend_requested": True,
        "actual_model_used": True if verified else None,
        "actual_model_execution_verified": verified,
        "model_name": (evidence or {}).get("model_id") or request.model_name,
        "base_url": request.model_base_url,
        "claim": "model_backed_generation_verified" if verified else "model_backend_requested_not_verified",
        "invocation_evidence": evidence,
    }


@dataclass(frozen=True)
class AgentFacadeRequest:
    request: str
    output_dir: str | Path = "generated_tasks"
    examples_dir: str | Path = "examples"
    backend: str = "template"
    draft_spec_path: str | Path | None = None
    allowed_capabilities: tuple[str, ...] = DEFAULT_ALLOWED_CAPABILITIES
    task_id: str | None = None
    output_root: str | Path | None = None
    script_output: str | Path | None = None
    max_repair_attempts: int = 2
    auto_run: bool = False
    dry_run: bool = False
    model_command: str | None = None
    model_name: str | None = None
    model_base_url: str | None = None
    model_api_key_env: str = "OPENAI_API_KEY"
    model_timeout_s: float = 60.0
    model_temperature: float | None = None
    model_seed: int | None = None
    model_max_output_tokens: int | None = None
    model_structured_output: str = "json_object"
    input_kind: str = "natural_language"
    model_route: dict[str, Any] = field(default_factory=dict)
    knowledge_policy: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentFacadeStep:
    name: str
    status: str
    message: str = ""
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AgentFacadeResult:
    ok: bool
    request: AgentFacadeRequest
    task_spec: dict[str, Any]
    validation: ValidationResult
    guards: AgentGuardReport
    compiled: CompiledTask | None = None
    script: ScriptExportResult | None = None
    run_result: TaskRunResult | None = None
    run_bundle_result: RunExecutionResult | None = None
    planning: PlanningResult | None = None
    output_dir: Path | None = None
    files: dict[str, str] = field(default_factory=dict)
    steps: tuple[AgentFacadeStep, ...] = field(default_factory=tuple)
    reason_codes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        req = asdict(self.request)
        for key in ("output_dir", "examples_dir", "draft_spec_path", "output_root", "script_output"):
            if req.get(key) is not None:
                req[key] = str(req[key])
        req["allowed_capabilities"] = list(self.request.allowed_capabilities)
        return {
            "ok": self.ok,
            "agent_facade_version": AGENT_FACADE_VERSION,
            "task_spec_version": CANONICAL_TASK_SPEC_VERSION,
            "backend_identity": _backend_identity(self.request, self.steps),
            "request": req,
            "task_spec": self.task_spec,
            "validation": self.validation.to_dict(),
            "guards": self.guards.to_dict(),
            "compiled": self.compiled.to_dict() if self.compiled else None,
            "script": self.script.to_dict() if self.script else None,
            "run_result": self.run_result.to_dict() if self.run_result else None,
            "run_bundle_result": self.run_bundle_result.to_dict() if self.run_bundle_result else None,
            "planning": self.planning.to_dict() if self.planning else None,
            "output_dir": str(self.output_dir) if self.output_dir else None,
            "files": dict(self.files),
            "steps": [item.to_dict() for item in self.steps],
            "reason_codes": list(self.reason_codes),
        }


def _write_yaml(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(dict(payload), sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path



_EXPLICIT_NEGATIVE_EFFECT_RE = re.compile(
    r"(?:不|不要|无需|禁止|不添加|不注入|不设置|没有|无)(?:任何)?(?:故障|失效|异常|退化|漂移|偏置|噪声增加)"
    r"|(?:nominal|no|without)\s+(?:faults?|failures?|degradations?|anomalies?)",
    re.IGNORECASE,
)
_EXPLICIT_POSITIVE_EFFECT_RE = re.compile(
    r"故障|失效|卡滞|堵转|异常|退化|漂移|噪声增加|偏置|中断|关机|饱和"
    r"|开路|短路|摩擦(?:增加|增大|升高|恶化)|容量(?:下降|降低|降到|减至|减到|衰减|损失|减半)"
    r"|功率(?:下降|降低|衰减|损失|减半)|效率(?:下降|降低|衰减|损失)"
    r"|转速(?:限制|上限)|速度(?:限制|上限)|链路(?:丢失|中断)|推力(?:下降|损失|失效)"
    r"|fault|failure|degrad|jam|stuck|bias|dropout|open[ -]?circuit|short[ -]?circuit|saturation",
    re.IGNORECASE,
)
_EFFECT_DOMAIN_CHANGE_RE = re.compile(
    r"(?:反作用轮|飞轮|电池|太阳翼|太阳阵列|发射机|天线|链路|推进器|推力器|加热器|散热器|传感器|陀螺|星敏|"
    r"摩擦|容量|功率|效率|噪声|偏置|转速|速度|推力|温度)"
    r"[^。；;\n]{0,24}(?:增加|增大|升高|降低|下降|降到|减至|减到|衰减|损失|减半|恶化|卡住|失效|中断|限制|达到上限)"
    r"|(?:增加|增大|升高|降低|下降|衰减|损失|减半|恶化)"
    r"[^。；;\n]{0,24}(?:摩擦|容量|功率|效率|噪声|偏置|转速|速度|推力|温度)",
    re.IGNORECASE,
)
_RPM_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:r\s*/\s*min|rpm|转\s*/?\s*分(?:钟)?)", re.IGNORECASE)
_RPM_TARGET_CUE_RE = re.compile(
    r"(?:从\s*-?\d+(?:\.\d+)?\s*(?:rpm|r\s*/\s*min|转\s*/?\s*分(?:钟)?)\s*(?:加速|减速|变化|调整)?\s*(?:到|至)|"
    r"(?:目标|最终|达到|加速到|减速到)\s*-?\d+(?:\.\d+)?\s*(?:rpm|r\s*/\s*min|转\s*/?\s*分(?:钟)?)|"
    r"from\s+-?\d+(?:\.\d+)?\s*rpm\s+to\s+-?\d+(?:\.\d+)?\s*rpm)",
    re.IGNORECASE,
)


def _effect_intent_state(request_text: str) -> str:
    """Classify event intent without destructively guessing from missing keywords."""
    text = request_text or ""
    if _EXPLICIT_NEGATIVE_EFFECT_RE.search(text):
        return "explicit_negative"
    if _EXPLICIT_POSITIVE_EFFECT_RE.search(text) or _EFFECT_DOMAIN_CHANGE_RE.search(text):
        return "explicit_positive"
    return "unconfirmed"


def _event_collections(payload: Mapping[str, Any]) -> list[tuple[str, list[Any]]]:
    collections: list[tuple[str, list[Any]]] = []
    events = payload.get("events") if isinstance(payload.get("events"), Mapping) else {}
    modifiers = payload.get("modifiers") if isinstance(payload.get("modifiers"), Mapping) else {}
    for path, value in (
        ("events.faults", events.get("faults")),
        ("events.degradations", events.get("degradations")),
        ("faults", payload.get("faults")),
        ("modifiers.faults", modifiers.get("faults")),
        ("modifiers.degradations", modifiers.get("degradations")),
    ):
        if isinstance(value, list) and value:
            collections.append((path, value))
    legacy_degradations = payload.get("degradations")
    if isinstance(legacy_degradations, Mapping) and legacy_degradations:
        collections.append(("degradations", [legacy_degradations]))
    elif isinstance(legacy_degradations, list) and legacy_degradations:
        collections.append(("degradations", legacy_degradations))
    return collections


def _sanitize_generated_draft(spec: Mapping[str, Any], request_text: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Normalize unsafe model output without silently changing user intent.

    Explicit negative intent removes only faults/degradations.  Explicit positive
    intent preserves them.  Ambiguous generated events are retained as a draft
    but marked as requiring confirmation, so they cannot be auto-executed.
    """
    out = copy.deepcopy(dict(spec))
    repairs: list[dict[str, Any]] = []
    intent = _effect_intent_state(request_text)
    collections = _event_collections(out)

    if intent == "explicit_negative":
        events = out.get("events") if isinstance(out.get("events"), dict) else None
        if events is not None:
            if events.get("faults"):
                events["faults"] = []
                repairs.append({"path": "events.faults", "action": "removed_by_explicit_negative_intent"})
            if events.get("degradations"):
                events["degradations"] = []
                repairs.append({"path": "events.degradations", "action": "removed_by_explicit_negative_intent"})
        if out.get("faults"):
            out["faults"] = []
            repairs.append({"path": "faults", "action": "removed_by_explicit_negative_intent"})
        if isinstance(out.get("degradations"), (list, dict)) and out.get("degradations"):
            out["degradations"] = [] if isinstance(out.get("degradations"), list) else {}
            repairs.append({"path": "degradations", "action": "removed_by_explicit_negative_intent"})
        modifiers = out.get("modifiers") if isinstance(out.get("modifiers"), dict) else None
        if modifiers is not None:
            if modifiers.get("faults"):
                modifiers["faults"] = []
                repairs.append({"path": "modifiers.faults", "action": "removed_by_explicit_negative_intent"})
            if modifiers.get("degradations"):
                modifiers["degradations"] = []
                repairs.append({"path": "modifiers.degradations", "action": "removed_by_explicit_negative_intent"})
        target = out.get("target") if isinstance(out.get("target"), dict) else None
        if target is not None:
            target["mode"] = "nominal"
        model = out.get("model") if isinstance(out.get("model"), dict) else None
        if model is not None and isinstance(model.get("target"), dict):
            model["target"]["mode"] = "nominal"
    elif intent == "unconfirmed" and collections:
        metadata = out.setdefault("metadata", {})
        if isinstance(metadata, dict):
            safety = metadata.setdefault("agent_safety", {})
            if isinstance(safety, dict):
                safety.update({
                    "effect_intent": "unconfirmed",
                    "generated_events_require_confirmation": True,
                    "generated_event_paths": [path for path, _ in collections],
                })
        repairs.append({
            "path": "events",
            "action": "retained_but_requires_confirmation",
            "event_paths": [path for path, _ in collections],
        })

    def repair_list(items: Any, path: str) -> None:
        if not isinstance(items, list):
            return
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            for key in ("magnitude", "severity"):
                value = item.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0:
                    item[key] = 0.0
                    repairs.append({"path": f"{path}.{index}.{key}", "action": "clamped_to_zero", "original": value})

    events = out.get("events") if isinstance(out.get("events"), dict) else {}
    repair_list(events.get("faults"), "events.faults")
    repair_list(events.get("degradations"), "events.degradations")
    repair_list(events.get("constraints"), "events.constraints")
    repair_list(out.get("faults"), "faults")
    modifiers = out.get("modifiers") if isinstance(out.get("modifiers"), dict) else {}
    repair_list(modifiers.get("faults"), "modifiers.faults")
    repair_list(modifiers.get("degradations"), "modifiers.degradations")
    repair_list(modifiers.get("constraints"), "modifiers.constraints")

    model = out.get("model") if isinstance(out.get("model"), dict) else {}
    capability_id = str(model.get("capability_id") or out.get("capability_id") or "")
    simulation = out.get("simulation") if isinstance(out.get("simulation"), dict) else None
    if capability_id:
        try:
            from .capability_registry import get_capability
            contract = get_capability(capability_id)
            target_name = contract.target_name
            target_level = contract.target_level
            if target_level == "subsystem":
                if simulation is not None and not simulation.get("subsystem") and str(simulation.get("level") or target_level) == "subsystem":
                    simulation["level"] = "subsystem"
                    simulation["subsystem"] = target_name
                    repairs.append({"path": "simulation.subsystem", "action": "inferred_from_capability", "value": target_name})
                target = out.setdefault("target", {})
                if isinstance(target, dict):
                    if target.get("level") != "subsystem":
                        target["level"] = "subsystem"
                        repairs.append({"path": "target.level", "action": "inferred_from_capability", "value": "subsystem"})
                    if not target.get("name"):
                        target["name"] = target_name
                        repairs.append({"path": "target.name", "action": "inferred_from_capability", "value": target_name})
                    target.setdefault("mode", "nominal")
                model_target = model.setdefault("target", {}) if isinstance(model, dict) else {}
                if isinstance(model_target, dict):
                    model_target.setdefault("level", "subsystem")
                    model_target.setdefault("name", target_name)
                    model_target.setdefault("mode", "nominal")
        except Exception:
            target = out.get("target") if isinstance(out.get("target"), dict) else {}
            if simulation is not None and str(simulation.get("level") or "") == "subsystem" and not simulation.get("subsystem") and target.get("name"):
                simulation["subsystem"] = target["name"]
                repairs.append({"path": "simulation.subsystem", "action": "inferred_from_target", "value": target["name"]})
    return out, repairs


def _scalar(value: Any, default: float) -> float:
    if isinstance(value, (list, tuple)) and value:
        value = value[0]
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


_EXPLICIT_PARAMETER_ALIASES: dict[str, tuple[str, ...]] = {
    "simulation.duration_s": ("仿真时间", "运行时间", "持续时间", "duration", "run time"),
    "parameters.values.battery_capacity_wh": ("电池容量", "battery capacity"),
    "parameters.values.initial_soc": ("初始soc", "初始荷电状态", "initial soc", "state of charge"),
    "parameters.values.solar_power_w": ("太阳翼功率", "太阳阵列功率", "solar power"),
    "parameters.values.payload_power_w": ("载荷功耗", "载荷功率", "payload power"),
    "parameters.values.storage_capacity_bits": ("存储容量", "storage capacity"),
    "parameters.values.transmitter_baud_bps": ("数据率", "码率", "baud", "data rate"),
    "parameters.values.thermal_step_s": ("热控步长", "热仿真步长", "thermal step"),
    "parameters.values.orb_env_step_s": ("轨道环境步长", "环境步长", "orbit environment step"),
    "parameters.values.adcs_dyn_step_s": ("动力学步长", "姿态动力学步长", "dynamics step"),
    "parameters.values.adcs_fsw_step_s": ("控制周期", "飞控周期", "fsw step", "control period"),
}
_CRITICAL_PHYSICAL_PARAMETER_PATHS = frozenset({
    "parameters.values.battery_capacity_wh",
    "parameters.values.initial_soc",
    "parameters.values.solar_power_w",
    "parameters.values.payload_power_w",
    "parameters.values.storage_capacity_bits",
    "parameters.values.transmitter_baud_bps",
})


def _request_explicitly_sets(path: str, request_text: str) -> bool:
    text = (request_text or "").lower()
    aliases = _EXPLICIT_PARAMETER_ALIASES.get(path, ())
    for alias in aliases:
        pos = text.find(alias.lower())
        if pos < 0:
            continue
        window = text[max(0, pos - 28): pos + len(alias) + 48]
        if re.search(r"[-+]?\d+(?:\.\d+)?", window):
            return True
    return False


def _apply_request_semantics(canonical: dict[str, Any], request_text: str) -> list[dict[str, Any]]:
    """Apply deterministic unit semantics without inventing ambiguous control targets."""
    model = canonical.get("model") if isinstance(canonical.get("model"), dict) else {}
    if model.get("capability_id") != "component.reaction_wheel.v1":
        return []
    rpm_values = [float(match.group(1)) for match in _RPM_RE.finditer(request_text or "")]
    if not rpm_values:
        return []
    values = canonical.setdefault("parameters", {}).setdefault("values", {})
    initial_rpm = rpm_values[0]
    initial_rad_s = initial_rpm * 2.0 * math.pi / 60.0
    values["num_wheels"] = max(1, int(_scalar(values.get("num_wheels"), 1)))
    values["initial_wheel_speeds_rad_s"] = initial_rad_s
    repairs = [{
        "path": "parameters.values.initial_wheel_speeds_rad_s",
        "action": "converted_rpm_to_rad_s",
        "source_value_rpm": initial_rpm,
        "value_rad_s": initial_rad_s,
    }]
    if len(rpm_values) >= 2 and _RPM_TARGET_CUE_RE.search(request_text or ""):
        target_rpm = rpm_values[1]
        target_rad_s = target_rpm * 2.0 * math.pi / 60.0
        duration_s = max(_scalar(canonical.get("simulation", {}).get("duration_s"), 60.0), 1e-6)
        inertia = max(_scalar(values.get("wheel_inertia_kg_m2"), 0.1), 1e-9)
        damping = max(_scalar(values.get("damping_nms"), 1e-5), 0.0)
        max_torque = max(_scalar(values.get("max_motor_torque_nm"), 0.02), 0.0)
        command = inertia * (target_rad_s - initial_rad_s) / duration_s + damping * target_rad_s
        if max_torque > 0:
            command = max(-0.95 * max_torque, min(0.95 * max_torque, command))
        values["command_torque_nm"] = command
        values.pop("torque_profile_nm", None)
        metadata = canonical.setdefault("metadata", {})
        metadata["requested_reaction_wheel_target"] = {"rpm": target_rpm, "rad_s": target_rad_s}
        repairs.append({
            "path": "parameters.values.command_torque_nm",
            "action": "derived_from_explicit_target_speed",
            "target_rpm": target_rpm,
            "target_rad_s": target_rad_s,
            "value_nm": command,
        })
    elif len(rpm_values) >= 2:
        metadata = canonical.setdefault("metadata", {})
        safety = metadata.setdefault("agent_safety", {})
        safety.update({
            "ambiguous_reaction_wheel_rpm_values": rpm_values,
            "requires_confirmation": True,
        })
        repairs.append({
            "path": "parameters.values.command_torque_nm",
            "action": "not_derived_ambiguous_rpm_values",
            "rpm_values": rpm_values,
        })
    return repairs


def _canonical_provenance(spec: dict[str, Any], request: AgentFacadeRequest) -> dict[str, Any]:
    out = canonicalize_task_spec(spec)
    provenance = out.setdefault("provenance", {})
    fields = provenance.setdefault("fields", {})
    provenance_paths = ["task.id", "simulation.level", "simulation.duration_s", "model.capability_id"]
    parameters = out.get("parameters") if isinstance(out.get("parameters"), Mapping) else {}
    values = parameters.get("values") if isinstance(parameters.get("values"), Mapping) else {}
    provenance_paths.extend(f"parameters.values.{key}" for key in sorted(values, key=str))
    events = out.get("events") if isinstance(out.get("events"), Mapping) else {}
    for collection in ("faults", "degradations", "constraints"):
        for index, event in enumerate(events.get(collection) or []):
            if not isinstance(event, Mapping):
                continue
            event_parameters = event.get("parameters") if isinstance(event.get("parameters"), Mapping) else {}
            provenance_paths.extend(
                f"events.{collection}[{index}].parameters.{key}"
                for key in sorted(event_parameters, key=str)
            )

    form_like = request.input_kind in {"form", "task_spec", "json", "yaml", "patch"}
    for path in provenance_paths:
        existing = fields.get(path) if isinstance(fields, dict) else None
        if isinstance(existing, dict) and existing.get("source") not in {None, "legacy_migration"}:
            continue
        if form_like:
            source = "user_provided"
            evidence = f"explicit {request.input_kind} input"
        elif path == "task.id" and request.task_id:
            source = "user_explicit"
            evidence = "explicit Agent task_id argument"
        elif _request_explicitly_sets(path, request.request):
            source = "user_explicit"
            evidence = "numeric value found next to a parameter alias in the user request"
        elif request.backend == "template":
            source = "template_default"
            evidence = "deterministic template selection"
        else:
            source = "agent_inferred"
            evidence = "model-backed Agent generation pipeline"
        fields[path] = {"source": source, "evidence": evidence}

    metadata = out.setdefault("metadata", {})
    safety = metadata.get("agent_safety") if isinstance(metadata.get("agent_safety"), Mapping) else {}
    reasons: list[str] = []
    if safety.get("model_fallback_used"):
        reasons.append("MODEL_FALLBACK_REQUIRES_USER_CONFIRMATION")
    if safety.get("generated_events_require_confirmation"):
        reasons.append("GENERATED_EVENTS_REQUIRE_USER_CONFIRMATION")
    if safety.get("ambiguous_reaction_wheel_rpm_values"):
        reasons.append("AMBIGUOUS_REACTION_WHEEL_RPM_VALUES")

    capability_id = str((out.get("model") or {}).get("capability_id") or "") if isinstance(out.get("model"), Mapping) else ""
    if capability_id.startswith("whole_spacecraft."):
        for path in sorted(_CRITICAL_PHYSICAL_PARAMETER_PATHS):
            row = fields.get(path)
            if path in provenance_paths and isinstance(row, Mapping) and row.get("source") not in {"user_explicit", "user_provided"}:
                reasons.append(f"CRITICAL_PARAMETER_UNCONFIRMED:{path}")

    reasons = sorted(set(reasons))
    prior_agent = metadata.get("agent") if isinstance(metadata.get("agent"), dict) else {}
    metadata["agent"] = {
        **prior_agent,
        "facade_version": AGENT_FACADE_VERSION,
        "backend": request.backend,
        "request_text": request.request,
        "tool_policy_version": agent_tool_policy_payload()["policy_version"],
        "input_kind": request.input_kind,
        "model_route": dict(request.model_route),
        "knowledge_policy": dict(request.knowledge_policy),
        "requires_confirmation": bool(reasons),
        "confirmation_reasons": reasons,
    }
    return canonicalize_task_spec(out)


def _model_fallback_used(backend_result: Any) -> bool:
    metadata = (getattr(backend_result, "task_spec", None) or {}).get("metadata")
    if isinstance(metadata, Mapping):
        if metadata.get("model_fallback_used") is True:
            return True
        if metadata.get("reason_code") == "MODEL_DRAFT_UNAVAILABLE_TEMPLATE_FALLBACK":
            return True
    for step in getattr(backend_result, "steps", ()):
        if str(getattr(step, "name", "")) in {"model_fallback", "planner_route_repair"}:
            return True
        payload = getattr(step, "payload", {})
        if isinstance(payload, Mapping) and payload.get("model_fallback_used") is True:
            return True
    return False

def run_agent(request: AgentFacadeRequest) -> AgentFacadeResult:
    output_dir = Path(request.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    steps: list[AgentFacadeStep] = [
        AgentFacadeStep("tool_policy", "complete", "Loaded semantic Agent tool whitelist.", payload=agent_tool_policy_payload())
    ]

    backend_request = CapabilityAgentRequest(
        request=request.request,
        output_dir=output_dir,
        examples_dir=request.examples_dir,
        backend=request.backend,
        draft_spec_path=request.draft_spec_path,
        allowed_capabilities=tuple(request.allowed_capabilities),
        task_id=request.task_id,
        output_root=request.output_root,
        script_output=request.script_output,
        max_repair_attempts=request.max_repair_attempts,
        run_task=False,
        dry_run=True,
        model_command=request.model_command,
        model_name=request.model_name,
        model_base_url=request.model_base_url,
        model_api_key_env=request.model_api_key_env,
        model_timeout_s=request.model_timeout_s,
        model_temperature=request.model_temperature,
        model_seed=request.model_seed,
        model_max_output_tokens=request.model_max_output_tokens,
        model_structured_output=request.model_structured_output,
        advisory_context=tuple(
            dict(item)
            for item in request.knowledge_policy.get(
                "approved_experience_advisories", ()
            )
            if isinstance(item, Mapping)
        ),
    )
    backend_result = run_capability_script_generation(backend_request)
    files.update(backend_result.files)
    steps.extend(
        AgentFacadeStep(
            f"backend.{item.name}",
            item.status,
            item.message,
            payload=dict(item.payload),
        )
        for item in backend_result.steps
    )
    backend_metadata = (backend_result.task_spec or {}).get("metadata")
    if isinstance(backend_metadata, Mapping) and backend_metadata.get("reason_code"):
        reason_code = str(backend_metadata["reason_code"])
        steps.append(AgentFacadeStep(
            "backend.template_source",
            "complete",
            "Recorded deterministic template source and fallback provenance.",
            reason_codes=(reason_code,),
            payload={
                "reason_code": reason_code,
                "template_source": backend_metadata.get("template_source"),
                "template_file_requested": backend_metadata.get("template_file_requested"),
            },
        ))

    if not backend_result.task_spec:
        validation = backend_result.validation
        empty_guard = AgentGuardReport(False, tool_policy=agent_tool_policy_payload())
        reason_codes = tuple(sorted({item.code for item in validation.errors} | {"AGENT_BACKEND_FAILED"}))
        result = AgentFacadeResult(
            False, request, {}, validation, empty_guard, output_dir=output_dir,
            files=files, steps=tuple(steps), reason_codes=reason_codes,
        )
        report_path = output_dir / "agent_report.json"
        files["agent_report"] = str(report_path)
        result = AgentFacadeResult(**{**result.__dict__, "files": files})
        write_json(report_path, result.to_dict())
        return result

    sanitized_draft, sanitizer_repairs = _sanitize_generated_draft(backend_result.task_spec, request.request)
    if _model_fallback_used(backend_result):
        safety = sanitized_draft.setdefault("metadata", {}).setdefault("agent_safety", {})
        safety.update({
            "model_fallback_used": True,
            "requires_confirmation": True,
        })
        sanitizer_repairs.append({
            "path": "metadata.agent_safety.model_fallback_used",
            "action": "blocked_automatic_execution_after_model_fallback",
        })
    migration = migrate_legacy_task_spec(sanitized_draft) if sanitized_draft.get("schema_version") != CANONICAL_TASK_SPEC_VERSION else None
    canonical = canonicalize_task_spec(sanitized_draft)
    canonical, attached_couplings = attach_required_couplings(canonical, request.request)
    semantic_repairs = _apply_request_semantics(canonical, request.request)
    if attached_couplings:
        semantic_repairs.append({
            "path": "mission.required_couplings",
            "action": "attached_physical_causal_requirements",
            "coupling_ids": list(attached_couplings),
        })
    canonical, output_resolution = apply_output_intents_to_spec(canonical, request.request)
    output_repairs: list[dict[str, Any]] = []
    if output_resolution.fields:
        output_repairs.append({
            "path": "outputs.plots",
            "action": "resolved_natural_language_output_intent",
            "fields": list(output_resolution.fields),
            "intents": list(output_resolution.requested_intents),
        })
    if output_resolution.ambiguous_intents:
        output_repairs.append({
            "path": "metadata.agent.output_intent_resolution.ambiguous_intents",
            "action": "recorded_output_intent_ambiguity",
            "items": list(output_resolution.ambiguous_intents),
        })
    if output_resolution.unsupported_intents:
        output_repairs.append({
            "path": "metadata.agent.output_intent_resolution.unsupported_intents",
            "action": "recorded_unsupported_output_intent",
            "items": list(output_resolution.unsupported_intents),
        })
    if sanitizer_repairs or semantic_repairs or output_repairs:
        metadata = canonical.setdefault("metadata", {})
        metadata["deterministic_repairs"] = [*sanitizer_repairs, *semantic_repairs, *output_repairs]
        canonical = canonicalize_task_spec(canonical)
        steps.append(AgentFacadeStep(
            "deterministic_repair",
            "complete",
            "Applied non-LLM event safety, engineering-unit normalization, and output-intent resolution.",
            reason_codes=("DETERMINISTIC_DRAFT_REPAIRED",),
            payload={"repairs": [*sanitizer_repairs, *semantic_repairs, *output_repairs]},
        ))
    canonical = _canonical_provenance(canonical, request)
    validation = validate_task_spec(canonical)
    guards = evaluate_agent_guards(canonical)
    steps.append(AgentFacadeStep(
        "canonicalize",
        "complete",
        "Produced CanonicalTaskSpec 1.0 from the backend draft.",
        reason_codes=("TASKSPEC_LEGACY_MIGRATED",) if migration else (),
        payload={"schema_version": canonical.get("schema_version")},
    ))
    steps.append(AgentFacadeStep(
        "guard",
        "complete" if guards.ok else "failed",
        "Applied capability, proxy, parameter-profile and claim guards.",
        reason_codes=tuple(item.code for item in guards.issues),
        payload=guards.to_dict(),
    ))

    task_yaml = _write_yaml(output_dir / "task_spec.yaml", canonical)
    task_json = write_json(output_dir / "task_spec.json", canonical)
    validation_json = write_json(output_dir / "validation.json", validation.to_dict())
    guard_json = write_json(output_dir / "guard_report.json", guards.to_dict())
    tool_json = write_json(output_dir / "agent_tool_policy.json", agent_tool_policy_payload())
    files.update({
        "task_spec_yaml": str(task_yaml),
        "task_spec_json": str(task_json),
        "validation": str(validation_json),
        "guard_report": str(guard_json),
        "agent_tool_policy": str(tool_json),
    })
    if migration is not None:
        migration_path = write_json(output_dir / "taskspec_migration.json", migration.to_dict())
        files["taskspec_migration"] = str(migration_path)

    compiled: CompiledTask | None = None
    script: ScriptExportResult | None = None
    run_result: TaskRunResult | None = None
    run_bundle_result: RunExecutionResult | None = None
    planning: PlanningResult | None = None
    if validation.ok and guards.ok:
        planning = plan_task_spec(canonical)
        files["plan_validation"] = str(write_json(output_dir / "plan_validation.json", planning.validation.to_dict()))
        if planning.resolved_spec is not None:
            resolved_payload = planning.resolved_spec.model_dump(mode="json")
            files["resolved_spec_json"] = str(write_json(output_dir / "resolved_spec.json", resolved_payload))
            files["resolved_spec_yaml"] = str(_write_yaml(output_dir / "resolved_spec.yaml", resolved_payload))
        if planning.execution_plan is not None:
            files["execution_plan"] = str(write_json(output_dir / "execution_plan.json", planning.execution_plan.model_dump(mode="json")))
        steps.append(AgentFacadeStep(
            "plan",
            "complete" if planning.ok else "failed",
            "Resolved capability ownership, dependencies, effects, outputs and a constrained execution DAG.",
            reason_codes=tuple(item.code for item in planning.validation.issues),
            payload={
                "ok": planning.ok,
                "resolved_spec_sha256": planning.resolved_spec.resolved_spec_sha256 if planning.resolved_spec else None,
                "execution_plan_sha256": planning.execution_plan.plan_sha256 if planning.execution_plan else None,
            },
        ))
        if planning.ok:
            compiled = attach_plan_metadata(compile_task_spec(canonical, validate=False), planning)
            files["compiled_task"] = str(write_json(output_dir / "compiled_task.json", compiled.to_dict()))
            script_path = Path(request.script_output) if request.script_output else output_dir / "generated_simulation.py"
            script = export_runner_script(task_yaml, script_path, kind="capability-python", output_root=request.output_root)
            files["generated_script"] = str(script.output_path)
            files["script_export"] = str(write_json(output_dir / "script_export.json", script.to_dict()))
            steps.append(AgentFacadeStep("compile", "complete", "Compiled only after the V24 execution plan passed validation."))
            confirmation_required = bool(
                isinstance(canonical.get("metadata"), Mapping)
                and isinstance(canonical["metadata"].get("agent"), Mapping)
                and canonical["metadata"]["agent"].get("requires_confirmation")
            )
            if request.auto_run and not request.dry_run and confirmation_required:
                reasons = tuple(canonical["metadata"]["agent"].get("confirmation_reasons") or ())
                steps.append(AgentFacadeStep(
                    "run",
                    "blocked",
                    "Automatic execution was blocked until the user confirms fallback, generated events, ambiguous values, or critical physical defaults.",
                    reason_codes=("AGENT_CONFIRMATION_REQUIRED", *reasons),
                    payload={"confirmation_reasons": list(reasons)},
                ))
            elif request.auto_run and not request.dry_run:
                prepared = prepare_run(
                    canonical,
                    output_root=Path(request.output_root) if request.output_root else output_dir / "runs",
                )
                files["run_bundle"] = prepared.bundle_root
                files["prepared_run"] = str(Path(prepared.bundle_root) / "runtime" / "prepared_run.json")
                steps.append(AgentFacadeStep(
                    "prepare_run",
                    "complete",
                    "Prepared an immutable V25 Run Bundle and locked the exact ExecutionPlan hash.",
                    payload={"run_id": prepared.run_id, "execution_plan_sha256": prepared.execution_plan_sha256},
                ))
                run_bundle_result = execute_prepared_run(
                    prepared.bundle_root,
                    expected_plan_sha256=prepared.execution_plan_sha256,
                    max_attempts=min(max(1, request.max_repair_attempts), 2),
                )
                dataset = None
                if run_bundle_result.dataset is not None:
                    dataset = DatasetWriteResult(
                        output_root=Path(run_bundle_result.dataset.output_root),
                        files=dict(run_bundle_result.dataset.files),
                        manifest=dict(run_bundle_result.dataset.manifest),
                    )
                run_result = TaskRunResult(
                    compiled=compiled,
                    summary=dict(run_bundle_result.summary),
                    trace_rows=tuple(dict(row) for row in run_bundle_result.trace_rows),
                    dataset=dataset,
                )
                files["run_result"] = str(write_json(output_dir / "run_result.json", run_bundle_result.model_dump(mode="json")))
                files["validation_outcome"] = str(Path(prepared.bundle_root) / "validation" / "validation_outcome.json")
                files["claim_report"] = str(Path(prepared.bundle_root) / "validation" / "claim_report.json")
                steps.append(AgentFacadeStep(
                    "run",
                    "complete" if run_bundle_result.run_record.status == "SUCCEEDED" else "failed",
                    "Executed through the V25 Plan/Execute runtime and produced V26 closed-loop validation.",
                    reason_codes=(run_bundle_result.validation.reason_code,),
                    payload={
                        "run_id": run_bundle_result.run_record.run_id,
                        "run_status": run_bundle_result.run_record.status,
                        "validation_result": run_bundle_result.validation.result,
                    },
                ))
            elif request.auto_run:
                steps.append(AgentFacadeStep("run", "skipped", "dry_run=True; execution was not started."))
        else:
            steps.append(AgentFacadeStep("compile", "blocked", "V24 plan validation blocked compilation."))
    else:
        steps.append(AgentFacadeStep("plan", "blocked", "TaskSpec validation or Agent guards blocked planning."))
        steps.append(AgentFacadeStep("compile", "blocked", "Validation or Agent guard failure blocked compilation."))

    planning_codes = {item.code for item in planning.validation.issues} if planning else set()
    step_reason_codes = {code for step in steps for code in step.reason_codes}
    reason_codes = tuple(sorted({item.code for item in validation.issues} | {item.code for item in guards.issues} | planning_codes | step_reason_codes))
    ok = validation.ok and guards.ok and planning is not None and planning.ok and compiled is not None
    report_path = output_dir / "agent_report.json"
    files["agent_report"] = str(report_path)
    result = AgentFacadeResult(
        ok=ok,
        request=request,
        task_spec=canonical,
        validation=validation,
        guards=guards,
        compiled=compiled,
        script=script,
        run_result=run_result,
        run_bundle_result=run_bundle_result,
        planning=planning,
        output_dir=output_dir,
        files=files,
        steps=tuple(steps),
        reason_codes=reason_codes,
    )
    write_json(report_path, result.to_dict())
    return result


def generate_from_text(
    request_text: str,
    *,
    output_dir: str | Path = "generated_tasks",
    examples_dir: str | Path = "examples",
    backend: str = "template",
    auto_run: bool = False,
    dry_run: bool = False,
    allowed_capabilities: Sequence[str] = DEFAULT_ALLOWED_CAPABILITIES,
    **kwargs: Any,
) -> AgentFacadeResult:
    return run_agent(AgentFacadeRequest(
        request=request_text,
        output_dir=output_dir,
        examples_dir=examples_dir,
        backend=backend,
        auto_run=auto_run,
        dry_run=dry_run,
        allowed_capabilities=tuple(allowed_capabilities),
        **kwargs,
    ))


__all__ = [
    "AGENT_FACADE_VERSION",
    "AgentFacadeRequest",
    "AgentFacadeStep",
    "AgentFacadeResult",
    "run_agent",
    "generate_from_text",
]
