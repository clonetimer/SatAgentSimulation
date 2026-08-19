"""Agent interaction policy for capability-backed script generation.

S7 keeps product behavior explicit.  The LLM may draft a TaskSpec, but local
policy decides whether a request should proceed with defaults, ask for missing
information, generate a minimal runnable script, or stop at an unsupported
capability boundary.  The policy is deterministic and source/capability driven.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from .capability_agent import DEFAULT_ALLOWED_CAPABILITIES
from .capability_planner import CapabilityPlanResult, MissingParameter, plan_capability_for_request
from .coupling_requirements import required_couplings_for_text, unsupported_couplings_for_capability


@dataclass(frozen=True)
class AgentPolicyQuestion:
    """One user-facing clarification question."""

    field: str
    question: str
    why: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class AgentInteractionDecision:
    """Deterministic policy decision for a natural-language request."""

    request: str
    action: str
    supported: bool
    selected_capability_id: str | None
    explanation: str
    plan: dict[str, Any]
    defaulted_parameters: tuple[dict[str, Any], ...] = ()
    questions: tuple[AgentPolicyQuestion, ...] = ()
    unsupported_requirements: tuple[dict[str, Any], ...] = ()
    boundary_warnings: tuple[str, ...] = ()
    fidelity: dict[str, Any] = field(default_factory=dict)
    can_generate_script: bool = False

    @property
    def ok_to_generate(self) -> bool:
        return self.can_generate_script

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request,
            "action": self.action,
            "supported": self.supported,
            "selected_capability_id": self.selected_capability_id,
            "explanation": self.explanation,
            "plan": dict(self.plan),
            "defaulted_parameters": [dict(item) for item in self.defaulted_parameters],
            "questions": [q.to_dict() for q in self.questions],
            "unsupported_requirements": [dict(item) for item in self.unsupported_requirements],
            "boundary_warnings": list(self.boundary_warnings),
            "fidelity": dict(self.fidelity),
            "can_generate_script": self.can_generate_script,
        }


def _text_requests_clarification(text: str) -> bool:
    lower = text.lower()
    cues = (
        "ask me", "clarify", "do not assume", "don't assume", "no defaults",
        "必须确认", "不要默认", "不要假设", "先问", "需要确认", "精确参数", "精确配置",
    )
    return any(cue in lower for cue in cues)



_CRITICAL_PARAMETER_TOKENS = (
    "orbit_radius", "altitude", "inclination", "spacecraft_mass", "inertia",
    "battery_capacity", "solar_panel_area", "solar_power", "payload_power",
    "ground_station", "frequency", "bandwidth", "antenna", "thruster",
    "lever_arm", "direction", "thrust_n", "min_operational_soc",
)

_QUANTITATIVE_ASSURANCE_CUES = (
    "精确", "定量", "工程", "验收", "裕度", "真实", "高保真", "标定", "验证",
    "准确", "可靠", "预算", "设计", "认证", "quantitative", "engineering",
    "validation", "verify", "high fidelity", "calibrat", "budget", "margin",
)

_CRITICAL_REQUEST_TERMS: dict[str, tuple[str, ...]] = {
    "orbit": ("轨道", "高度", "倾角", "orbit", "altitude", "inclination"),
    "mass": ("质量", "惯量", "mass", "inertia"),
    "battery": ("电池", "soc", "能源", "功率", "battery", "power", "energy"),
    "solar": ("太阳阵列", "食影", "背日", "solar", "eclipse"),
    "payload": ("载荷", "payload"),
    "ground": ("地面站", "下行", "链路", "ground station", "downlink", "link budget"),
    "rf": ("频率", "带宽", "天线", "链路", "frequency", "bandwidth", "antenna", "rf"),
    "thruster": ("推进", "推力", "力臂", "喷管", "thruster", "thrust", "lever arm"),
}

def _critical_parameter_domain(item: MissingParameter) -> str | None:
    name = item.name.lower().replace("-", "_")
    if any(token in name for token in ("orbit_radius", "altitude", "inclination")):
        return "orbit"
    if any(token in name for token in ("spacecraft_mass", "mass", "inertia")):
        return "mass"
    if any(token in name for token in ("battery_capacity", "min_operational_soc")):
        return "battery"
    if any(token in name for token in ("solar_panel_area", "solar_power")):
        return "solar"
    if "payload_power" in name:
        return "payload"
    if "ground_station" in name:
        return "ground"
    if any(token in name for token in ("frequency", "bandwidth", "antenna")):
        return "rf"
    if any(token in name for token in ("thruster", "lever_arm", "direction", "thrust_n")):
        return "thruster"
    return None

def _requires_critical_confirmation(text: str, item: MissingParameter) -> bool:
    domain = _critical_parameter_domain(item)
    if domain is None:
        return False
    lower = text.lower()
    if any(cue in lower for cue in _QUANTITATIVE_ASSURANCE_CUES):
        return True
    return any(term in lower for term in _CRITICAL_REQUEST_TERMS.get(domain, ()))

def _question_for_missing(item: MissingParameter) -> AgentPolicyQuestion:
    unit = f" ({item.unit})" if item.unit else ""
    if item.default is not None:
        question = f"请确认 {item.name}{unit}，默认可使用 {item.default!r}。"
    else:
        question = f"请确认 {item.name}{unit}；未提供时可以使用 capability 示例默认值。"
    return AgentPolicyQuestion(
        field=item.name,
        question=question,
        why="该参数会影响仿真含义；用户要求不自动假设或请求精确配置。",
    )


def _missing_as_default(item: MissingParameter) -> dict[str, Any]:
    payload = item.to_dict()
    payload["decision"] = "defaulted_by_policy"
    return payload


def decide_agent_interaction(
    request_text: str,
    *,
    allowed_capabilities: Sequence[str] | None = None,
    plan: CapabilityPlanResult | None = None,
) -> AgentInteractionDecision:
    """Return the S7 interaction policy decision for a request.

    Policy summary:
    - unsupported requested domains stop script generation and produce boundary
      explanations;
    - no selected capability asks the user to choose/clarify capability scope;
    - missing fields are filled from capability defaults unless the request says
      not to assume, in which case targeted questions are emitted;
    - otherwise generate a minimal runnable script with defaults recorded in
      manifest metadata by downstream alignment/export code.
    """

    text = request_text.strip()
    selected_plan = plan or plan_capability_for_request(
        text,
        allowed_capabilities=tuple(allowed_capabilities) if allowed_capabilities is not None else DEFAULT_ALLOWED_CAPABILITIES,
    )
    plan_payload = selected_plan.to_dict()
    from .fidelity_policy import evaluate_fidelity_request
    fidelity_decision = evaluate_fidelity_request(text, selected_capability_id=selected_plan.selected_capability_id).to_dict()
    unsupported = tuple(item.to_dict() for item in selected_plan.unsupported_requirements)
    if not fidelity_decision.get("supported", True) and not unsupported:
        unsupported = ({
            "domain": "high_fidelity",
            "matched_keywords": tuple(),
            "message": "High-fidelity engineering-grade simulation was requested but no declared high-fidelity capability is available.",
        },)
    if unsupported:
        return AgentInteractionDecision(
            request=text,
            action="reject_unsupported",
            supported=False,
            selected_capability_id=selected_plan.selected_capability_id,
            explanation="请求包含当前 src/capability 边界之外的能力；不生成虚假的 TaskSpec 或 Python 调用。",
            plan=plan_payload,
            unsupported_requirements=unsupported,
            boundary_warnings=selected_plan.boundary_warnings,
            fidelity=fidelity_decision,
            can_generate_script=False,
        )

    coupling_requirements = required_couplings_for_text(text)
    unsupported_couplings = unsupported_couplings_for_capability(
        selected_plan.selected_capability_id, coupling_requirements
    )
    if unsupported_couplings:
        coupling_payload = tuple({
            "domain": "physical_coupling",
            "matched_keywords": item.matched_terms,
            "message": item.description,
            "required_coupling_id": item.coupling_id,
        } for item in unsupported_couplings)
        return AgentInteractionDecision(
            request=text,
            action="reject_missing_physical_coupling",
            supported=False,
            selected_capability_id=selected_plan.selected_capability_id,
            explanation="所选 capability 缺少请求所依赖的跨分系统物理因果链；不生成可运行但物理无效的 TaskSpec。",
            plan=plan_payload,
            unsupported_requirements=coupling_payload,
            boundary_warnings=selected_plan.boundary_warnings,
            fidelity=fidelity_decision,
            can_generate_script=False,
        )

    if not selected_plan.selected_capability_id:
        question = AgentPolicyQuestion(
            field="capability_id",
            question="请确认要仿真的能力范围，例如电池、EPS、轨道环境、通信下行、数据队列或星上存储。",
            why="当前请求无法稳定路由到已注册 capability。",
        )
        return AgentInteractionDecision(
            request=text,
            action="ask_clarifying_question",
            supported=False,
            selected_capability_id=None,
            explanation="无法从请求中确定可执行 capability。",
            plan=plan_payload,
            questions=(question,),
            fidelity=fidelity_decision,
            can_generate_script=False,
        )

    missing = tuple(selected_plan.missing_parameters)
    critical_missing = tuple(item for item in missing if _requires_critical_confirmation(text, item))
    if missing and (_text_requests_clarification(text) or critical_missing):
        question_items = missing if _text_requests_clarification(text) else critical_missing
        questions = tuple(_question_for_missing(item) for item in question_items)
        return AgentInteractionDecision(
            request=text,
            action="ask_clarifying_question",
            supported=True,
            selected_capability_id=selected_plan.selected_capability_id,
            explanation=(
                "请求明确要求不要假设/需要精确参数，因此先返回有针对性的缺参问题。"
                if _text_requests_clarification(text)
                else "缺少会改变轨道、质量、能源、链路或推进物理含义的关键参数；必须确认后才能生成。"
            ),
            plan=plan_payload,
            questions=questions,
            boundary_warnings=selected_plan.boundary_warnings,
            fidelity=fidelity_decision,
            can_generate_script=False,
        )

    if missing:
        return AgentInteractionDecision(
            request=text,
            action="generate_minimal_script_with_defaults",
            supported=True,
            selected_capability_id=selected_plan.selected_capability_id,
            explanation="缺失参数均可由 capability 示例或 adapter 默认值补齐，生成最小可运行脚本并记录默认值。",
            plan=plan_payload,
            defaulted_parameters=tuple(_missing_as_default(item) for item in missing),
            boundary_warnings=selected_plan.boundary_warnings,
            fidelity=fidelity_decision,
            can_generate_script=True,
        )

    return AgentInteractionDecision(
        request=text,
        action="generate_script",
        supported=True,
        selected_capability_id=selected_plan.selected_capability_id,
        explanation="请求已可稳定路由且没有需要交互确认的关键缺参。",
        plan=plan_payload,
        boundary_warnings=selected_plan.boundary_warnings,
        fidelity=fidelity_decision,
        can_generate_script=True,
    )


__all__ = [
    "AgentInteractionDecision",
    "AgentPolicyQuestion",
    "decide_agent_interaction",
]
