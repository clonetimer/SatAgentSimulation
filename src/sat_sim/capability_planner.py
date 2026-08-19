"""Capability-aware planner/router for Agent workflows.

A5 adds an explicit planning pass before TaskSpec drafting.  The planner does
not generate Python and does not replace validation.  It scores registered
capabilities against a natural-language request, reports unsupported requested
subsystems, and records assumptions/defaults that the downstream Agent may use.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Sequence

from .capability_registry import explicit_capability_ids_in_text, get_capability, list_capabilities
from .reason_codes import ReasonCode


@dataclass(frozen=True)
class CapabilityRouteCandidate:
    capability_id: str
    score: float
    matched_keywords: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UnsupportedRequirement:
    domain: str
    matched_keywords: tuple[str, ...]
    message: str
    reason_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MissingParameter:
    name: str
    source: str
    policy: str
    default: Any = None
    unit: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CapabilityPlanResult:
    request: str
    supported: bool
    selected_capability_id: str | None
    selected_task_type: str | None
    candidates: tuple[CapabilityRouteCandidate, ...] = ()
    unsupported_requirements: tuple[UnsupportedRequirement, ...] = ()
    missing_parameters: tuple[MissingParameter, ...] = ()
    assumptions: tuple[str, ...] = ()
    boundary_warnings: tuple[str, ...] = ()
    allowed_capabilities: tuple[str, ...] = ()
    recommended_action: str = "generate_task_spec"
    explanation: str = ""

    @property
    def ok(self) -> bool:
        return self.supported and self.selected_capability_id is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request,
            "supported": self.supported,
            "selected_capability_id": self.selected_capability_id,
            "selected_task_type": self.selected_task_type,
            "candidates": [c.to_dict() for c in self.candidates],
            "unsupported_requirements": [u.to_dict() for u in self.unsupported_requirements],
            "missing_parameters": [m.to_dict() for m in self.missing_parameters],
            "assumptions": list(self.assumptions),
            "boundary_warnings": list(self.boundary_warnings),
            "allowed_capabilities": list(self.allowed_capabilities),
            "recommended_action": self.recommended_action,
            "explanation": self.explanation,
        }


def _contains_any(text: str, words: Sequence[str]) -> tuple[str, ...]:
    lower = text.lower()
    matches: list[str] = []
    for word in words:
        if word.lower() in lower:
            matches.append(word)
    return tuple(matches)


def _task_type_for_capability(capability_id: str) -> str:
    try:
        contract = get_capability(capability_id)
        level = contract.target_level
    except Exception:
        level = capability_id.split(".", 1)[0]
    if level == "integrated" and capability_id.startswith("orbit_environment."):
        return "orbit_environment"
    if level in {"component", "subsystem", "whole_spacecraft"}:
        return level
    return level or "component"


CAPABILITY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "subsystem.adcs_unified_native.v1": (
        "adcs统一运行图", "adcs 统一运行图", "统一adcs", "统一姿控运行图",
        "unified adcs", "adcs unified runtime", "unified adcs runtime",
        "basilisk adcs runtime", "统一 process/task", "统一消息链",
    ),
    "whole_spacecraft.unified_native.v1": (
        "整星统一运行图", "全星统一运行图", "统一整星", "整星统一basilisk",
        "whole spacecraft unified runtime", "unified whole-spacecraft",
        "whole-spacecraft unified runtime", "统一process/task整星",
    ),
    "subsystem.propulsion.unified_native.v1": (
        "推进统一运行图", "统一推进运行图", "propulsion unified runtime",
        "unified propulsion", "basilisk propulsion runtime",
    ),
    "subsystem.eps.unified_native.v1": (
        "电源统一运行图", "eps统一运行图", "统一电源运行图",
        "eps unified runtime", "unified eps", "basilisk eps runtime",
    ),
    "subsystem.comm_data.unified_native.v1": (
        "通信数据统一运行图", "通信统一运行图", "数据统一运行图",
        "comm data unified runtime", "unified comm data", "basilisk comm data runtime",
    ),
    "whole_spacecraft.composite_digital_twin.v1": (
        "全分系统", "所有分系统", "六大分系统", "完整整星", "完整全星", "整星全链路",
        "全系统整星", "整星数字样机", "卫星数字样机", "组合整星",
        "complete whole spacecraft", "full spacecraft", "all subsystems", "six subsystems",
        "end-to-end spacecraft", "composite spacecraft", "whole-spacecraft digital prototype",
    ),
    "component.battery.v1": (
        "battery", "电池", "soc", "荷电", "容量", "open circuit", "开路",
    ),
    "component.solar_panel.v1": (
        "solar panel", "solar array", "solar", "太阳阵列", "太阳帆板", "太阳能板", "太阳翼", "帆板", "遮挡", "阴影因子",
    ),
    "component.reaction_wheel.v1": (
        "reaction wheel", "rw", "反作用轮", "动量轮", "飞轮",
    ),
    "subsystem.adcs.basic_rw_pointing.v1": (
        "adcs", "姿控", "姿态控制", "指向", "指向控制", "pointing", "attitude", "attitude control", "姿态误差",
    ),
    "subsystem.adcs_fidelity.v1": (
        "adcs", "ADCS", "姿控", "姿态控制", "闭环姿控", "closed-loop ADCS", "closed loop ADCS", "quaternion", "四元数",
        "detumble", "消旋", "nadir", "对地指向", "sun pointing", "太阳指向", "gyro", "陀螺", "gyro bias",
        "star tracker", "星敏", "sensor dropout", "传感器丢失", "environmental torque", "环境扰动力矩", "gravity gradient",
        "磁力矩", "magnetic torque", "wheel momentum", "飞轮动量", "controller mode", "控制模式"
    ),
    "subsystem.adcs_closed_loop.basic.v1": (
        "adcs", "attitude", "attitude control", "pointing", "姿控", "姿态控制", "指向", "指向控制", "姿态误差", "指向误差",
        "adcs closed-loop", "closed-loop adcs", "closed loop adcs", "closed-loop", "closed loop",
        "闭环姿控", "闭环姿态控制", "闭环", "quaternion", "四元数", "detumble", "消旋",
        "nadir pointing", "对地指向", "sun pointing", "太阳指向", "basic gyro", "simple gyro", "简单陀螺", "陀螺代理",
    ),
    "subsystem.thermal.basic_lumped.v1": (
        "thermal", "热控", "热轨", "温度", "散热", "散热器", "radiator", "heater", "加热器", "电池温度", "热平衡",
    ),
    "subsystem.comm.basic_ground_pass.v1": (
        "comm", "communication", "通信", "下行", "downlink", "链路", "link budget", "data backlog", "数据积压", "ground pass", "地面站通信", "通信窗口",
    ),
    "component.data_queue.v1": (
        "data queue", "queue bits", "data buffer", "数据队列", "数据缓存", "队列", "缓存积压", "dropped bits", "丢弃数据",
    ),
    "component.onboard_storage.v1": (
        "onboard storage", "storage", "stored bits", "data storage", "星上存储", "载荷存储", "存储容量", "存储溢出", "overflow bits",
    ),

    "component.ground_station.v1": ("ground station", "地面站", "可见性", "access window", "ground access", "过境", "仰角"),
    "component.antenna.v1": ("antenna", "天线", "增益", "gain", "boresight", "离轴", "指向损失"),
    "component.transmitter.v1": ("transmitter", "发射机", "tx", "射频功放", "功放", "发射功率", "effective rate"),
    "component.link_budget.v1": ("link budget", "link-budget", "链路预算", "ebn0", "ber", "链路裕度", "slant range"),
    "component.power_sink.v1": ("power sink", "负载功耗", "功耗负载", "simple load", "用电负载"),
    "component.heater.v1": ("heater", "加热器", "加热", "恒温器", "heater power"),
    "component.radiator.v1": ("radiator", "散热器", "散热", "热排散", "heat rejection"),
    "component.thermal_node.v1": ("thermal node", "热节点", "温度节点", "thermal profile", "温度响应"),
    "component.payload.v1": ("payload instrument", "payload", "载荷", "成像载荷", "载荷功耗", "payload power"),
    "component.payload_sensor.v1": ("payload sensor", "载荷传感器", "观测质量", "cloud fraction", "云量", "有效观测"),
    "orbit_environment.leo_simple.v1": (
        "orbit environment", "leo", "轨道环境", "轨道", "高度", "磁场", "地面站", "ground station", "access", "可见性",
    ),
    "orbit_environment.orbit_fidelity.v1": (
        "orbit fidelity", "numerical orbit", "numerical propagator", "numerical propagation",
        "j2 acceleration", "force contribution", "force trace", "force model trace",
        "drag", "atmospheric drag", "exponential atmosphere", "srp proxy", "solar radiation pressure",
        "orbit validation", "orbit benchmark", "tolerance envelope",
        "轨道保真", "数值轨道", "数值传播", "轨道数值传播", "j2 加速度", "j2加速度",
        "力模型轨迹", "力模型贡献", "大气阻力", "指数大气", "太阳光压代理", "轨道 benchmark", "轨道基准", "容差包络",
    ),
    "orbit_environment.medium_fidelity.v1": (
        "orbit", "orbit environment", "leo", "轨道", "轨道环境", "轨道仿真", "高度", "磁场",
        "medium fidelity", "medium-fidelity", "medium fidelity orbit", "中等保真", "中保真",
        "j2", "j2摄动", "j2 摄动", "secular", "半长轴", "偏心率", "eccentricity",
        "analytic sun", "sun vector", "太阳向量", "日食几何", "eclipse geometry",
        "umbra", "penumbra", "本影", "半影", "frame-tagged", "frame tagged", "orbit trace",
    ),
    "subsystem.eps.basic.v1": (
        "eps", "电源系统", "电源分系统", "供电", "电源", "pdu", "负载", "甩载", "能量", "日食", "eclipse", "shadow",
    ),
    "subsystem.eps.source_native.v1": (
        "eps source", "source-native eps", "原生电源", "原src电源", "eps 源码", "电源分系统源码", "电源 source-native",
    ),
    "subsystem.comm_data.source_native.v1": (
        "comm_data", "comm data", "通信数据分系统", "原生通信数据", "source-native comm", "通信分系统源码", "数据处理分系统",
    ),
    "subsystem.thermal.source_native.v1": (
        "thermal source", "source-native thermal", "原生热控", "热控分系统源码", "thermal subsystem source",
    ),
    "subsystem.payload.source_native.v1": (
        "payload subsystem", "source-native payload", "载荷分系统", "原生载荷", "载荷分系统源码",
    ),
    "subsystem.propulsion.source_native.v1": (
        "propulsion subsystem", "propulsion", "propellant", "fuel tank", "fuel pressure", "fuel remaining",
        "thruster subsystem", "thruster", "burn", "ignition", "source-native propulsion",
        "推进分系统", "推进", "推进源码", "推进剂", "推进剂消耗", "推进剂余量",
        "燃料箱", "燃料箱压力", "燃料压力", "燃料消耗", "剩余燃料", "推进器", "推力器", "点火", "喷气",
    ),

    "whole_spacecraft.power_thermal_orbit_coupled.v1": (
        "power orbit", "power-orbit", "电源轨道", "轨道电源", "电源-轨道",
        "power thermal orbit", "power-thermal-orbit", "电源热控轨道", "电源-热控-轨道",
        "power thermal orbit coupled", "power-thermal-orbit coupled", "power thermal orbit coupling",
        "eps thermal orbit coupled", "eps-thermal-orbit", "power thermal orbit route-b",
        "电源热控轨道耦合", "电源-热控-轨道耦合", "电源 热控 轨道 耦合", "功率热轨耦合", "功率热轨道耦合", "电源热轨道耦合", "全星功率热轨耦合", "全星电源热轨联合",
        "轨道电源热控耦合", "热控电源轨道耦合", "整星电源热控轨道耦合",
        "eclipse solar eps thermal", "heater feedback", "battery derating", "thermal derating",
        "日食电源热控", "加热器反馈", "电池温度降额", "温度降额", "耦合电源热控",
    ),
    "whole_spacecraft.comm_payload_mission_coupled.v1": (
        "comm payload mission coupled", "comm-payload mission coupled", "communication payload mission",
        "payload comm coupling", "payload downlink storage", "payload data downlink",
        "storage downlink", "mission data coupling", "mission-coupled comm",
        "通信载荷耦合", "通信-载荷耦合", "载荷通信耦合", "通信载荷任务耦合",
        "载荷数据下行", "载荷 存储 下行", "地面站下行存储", "任务级通信",
        "数据守恒", "存储下行耦合", "载荷生成数据",
    ),
    "whole_spacecraft.maneuver_orbit_attitude.v1": (
        "maneuver orbit attitude", "maneuver-orbit-attitude", "propulsion orbit attitude",
        "propulsion orbit attitude coupled", "finite burn", "delta-v", "delta v", "dv",
        "thruster burn", "orbit maneuver", "attitude disturbance", "burn attitude disturbance",
        "推进轨道姿态耦合", "推进-轨道-姿态耦合", "轨道姿态推进耦合",
        "轨道机动", "推进机动", "变轨", "速度增量", "姿态扰动", "点火姿态扰动",
    ),
    "whole_spacecraft.orbit_adcs_fidelity.v1": (
        "orbit adcs", "orbit-adcs", "orbit attitude", "orbit-attitude",
        "orbit adcs integration", "orbit and adcs", "adcs orbit integration",
        "nadir pointing from orbit", "sun pointing from orbit", "lvlh", "lvLH",
        "frame consistency", "orbit trace to adcs", "orbit attitude benchmark",
        "轨道姿控", "轨道-姿控", "轨道姿态", "轨道姿态耦合", "轨道和姿控",
        "轨道 adcs", "轨道姿控集成", "对地指向轨道", "太阳指向轨道",
        "lvlh", "坐标系一致", "轨道姿控 benchmark", "轨道姿控基准",
    ),
    "whole_spacecraft.basic_power_thermal_orbit.v1": (
        "whole spacecraft", "spacecraft", "整星", "全星", "星上", "power thermal orbit", "power-thermal-orbit",
        "电源热控轨道", "电源-热控-轨道", "轨道电源热控", "热控电源轨道", "整星热控", "整星温度",
    ),
    "whole_spacecraft.basic_power_attitude_orbit.v1": (
        "whole spacecraft", "spacecraft", "整星", "全星", "星上", "power attitude orbit", "power-attitude-orbit",
        "电源姿态轨道", "电源-姿态-轨道", "姿态电源轨道", "姿控电源轨道", "轨道电源姿态",
    ),
    "whole_spacecraft.basic_power_orbit.v1": (
        "whole spacecraft", "spacecraft", "整星", "全星", "星上", "电源轨道", "轨道电源", "power orbit", "power-orbit", "电源-轨道",
    ),
}

UNSUPPORTED_DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "arbitrary_code_execution": (
        "ignore previous instructions", "ignore all previous instructions",
        "忽略之前的指令", "忽略以上指令", "绕过安全", "绕过校验",
        "os.system", "subprocess", "__import__", "eval(", "exec(",
        "任意python", "任意 python", "直接执行python", "直接执行 python",
        "/etc/passwd", "写入项目外", "write outside the project",
    ),
    "high_fidelity_orbit": (
        "flight-grade orbit", "flight grade orbit", "flight-grade propagator", "flight grade propagator",
        "flight-grade orbit propagator",
        "third-body", "third body", "third-body forces",
        "force-model tolerance", "force-model tolerances",
        "calibrated force", "calibrated force-model", "calibrated tolerances", "spice", "spice ephemeris",
        "nrlmsise", "nrlmsise-00", "external truth orbit", "truth ephemeris", "validated ephemeris",
        "高保真轨道", "完整轨道动力学", "三体摄动", "第三体摄动", "spice 星历", "外部真值", "轨道真值", "力模型容差", "校准力模型",
    ),
    "high_fidelity_whole_spacecraft": (
        "工程级全卫星高保真", "全卫星高保真", "高保真全卫星", "高保真整星",
        "工程级整星", "工程级全卫星", "飞行级数字孪生", "认证级数字孪生",
        "飞行数据校准", "飞行数据", "外部真实数据", "flight data", "flight-data",
        "飞控级", "认证级", "真实型号", "真实卫星", "完整高保真",
        "结构动力学", "柔性结构", "有限元结构", "流体晃动", "推进剂晃动",
        "电磁兼容", "电磁场仿真", "辐射输运", "空间辐射环境", "飞控软件",
        "flight-grade digital twin", "engineering-grade digital twin",
    ),
    "adcs_advanced": ("完整adcs", "完整 adcs", "高保真adcs", "高保真 adcs", "高保真陀螺", "calibrated gyro", "星敏感器高保真", "high-fidelity star tracker", "磁力矩器", "magnetorquer", "完整姿控", "完整姿态控制", "完整fsw", "full fsw", "flight software"),
    "thermal_advanced": ("高保真热", "高保真 thermal", "热网络", "有限元", "finite element", "thermal network", "multi-node thermal", "完整热控", "完整 thermal"),
    "communication_advanced": ("高保真通信", "完整通信", "多站调度", "contact plan", "adaptive coding", "调制编码", "doppler", "多普勒", "weather loss", "rf propagation", "antenna pointing", "天线指向"),
    "propulsion": ("推进变轨", "轨道机动", "变轨", "delta-v", "delta v", "高保真推进", "完整推进", "轨道机动规划", "精确变轨", "连续推力", "多脉冲优化"),
    "full_basilisk_graph": ("完整 basilisk", "basilisk message graph", "message graph", "完整消息图"),
}

SUPPORTED_UNSUPPORTED_EXCEPTIONS = {
    "propulsion": (
        "whole_spacecraft.maneuver_orbit_attitude.v1",
        "subsystem.propulsion.source_native.v1",
    ),
    "adcs_advanced": ("component.mtb.v1",),
}


COMPOSITE_FULL_SPACECRAFT_PHRASES = (
    "全分系统", "所有分系统", "六大分系统", "全链路", "全系统", "完整整星", "完整全星",
    "全分系统齐全", "各分系统齐全", "整星全链路", "complete whole spacecraft",
    "full spacecraft", "all subsystems", "six subsystems", "end-to-end spacecraft",
)


def _detect_cross_domain_gate_request(request_text: str) -> tuple[str, ...]:
    """Detect explicit ADCS-to-payload/communication causal-gate requests.

    These requests require the composite whole-spacecraft graph even when the
    user also says "统一运行图".  Routing by the generic unified-runtime cue
    alone would select a capability that does not own the requested causal edge.
    """

    text = request_text.lower()
    adcs = _contains_any(text, (
        "adcs", "姿控", "姿态", "姿态误差", "指向", "pointing", "attitude",
    ))
    mission = _contains_any(text, (
        "通信", "下行", "comm", "communication", "downlink",
        "载荷", "payload", "成像", "观测",
    ))
    causal = _contains_any(text, (
        "影响", "导致", "门控", "联动", "耦合", "依赖", "关闭", "禁止",
        "gating", "gate", "affect", "causes", "coupled", "depends on",
    ))
    if adcs and mission and causal:
        return tuple(dict.fromkeys((*adcs, *mission, *causal)))
    return ()


def _detect_composite_full_spacecraft_request(request_text: str) -> tuple[str, ...]:
    """Detect requests that require a multi-capability whole-spacecraft DAG.

    V36 treats orbit+ADCS+EPS+thermal+comm+payload requests as a product
    boundary: no active single capability currently owns all six domains.
    Returning a non-empty tuple means the planner must decline generation rather
    than silently choosing the closest partial capability.
    """

    phrase_matches = _contains_any(request_text, COMPOSITE_FULL_SPACECRAFT_PHRASES)
    lower = request_text.lower()
    domain_hits = {
        "orbit": bool(_contains_any(lower, ("orbit", "轨道", "leo", "轨道环境"))),
        "adcs": bool(_contains_any(lower, ("adcs", "姿控", "姿态控制", "attitude", "pointing"))),
        "eps": bool(_contains_any(lower, ("eps", "电源", "电源系统", "供电", "power"))),
        "thermal": bool(_contains_any(lower, ("thermal", "热控", "温度", "heater", "radiator"))),
        "comm": bool(_contains_any(lower, ("comm", "communication", "通信", "下行", "downlink"))),
        "payload": bool(_contains_any(lower, ("payload", "载荷", "成像", "观测"))),
    }
    if phrase_matches and (_contains_any(lower, ("整星", "全星", "spacecraft", "whole spacecraft")) or sum(domain_hits.values()) >= 3):
        return tuple(sorted(set(phrase_matches)))
    if all(domain_hits.values()):
        return tuple(k for k, hit in domain_hits.items() if hit)
    return ()


def _domain_message(domain: str) -> str:
    return {
        "high_fidelity_orbit": "Flight-grade/high-fidelity orbit validation is not supported yet: third-body forces, SPICE/truth ephemerides, finite gravity fields, and calibrated force-model tolerances are outside the current ORB-1 orbit-fidelity capability. Basic numerical J2, drag, and SRP proxies are supported.",
        "high_fidelity_whole_spacecraft": "Engineering-grade full-satellite high-fidelity modeling/validation is not supported by the current source-native Agent package; use declared basic/source-native capabilities or readiness reports instead.",
        "adcs_advanced": "Basic engineering magnetorquer simulation is available through component.mtb.v1; calibrated high-fidelity sensors, complete ADCS actuator integration, and full FSW remain unsupported.",
        "thermal_advanced": "Advanced/high-fidelity thermal networks or finite-element thermal analysis are not implemented yet; use subsystem.thermal.basic_lumped.v1 for basic lumped thermal behavior.",
        "communication_advanced": "Advanced communication features such as multi-station scheduling, adaptive coding, Doppler/weather RF propagation, or antenna pointing are not implemented yet; use subsystem.comm.basic_ground_pass.v1 for basic ground-pass link/data behavior.",
        "propulsion": "Advanced propulsion maneuver planning, precise multi-burn optimization, or high-fidelity continuous-thrust simulation is outside the current capability boundary; use whole_spacecraft.maneuver_orbit_attitude.v1 for a medium-fidelity finite-burn proxy or subsystem.propulsion.source_native.v1 for the existing source-native propulsion profile model.",
        "full_basilisk_graph": "Full Basilisk message-graph generation is outside the current capability boundary.",
    }.get(domain, f"Unsupported requested domain: {domain}")


def _score_capability(capability_id: str, request_text: str) -> CapabilityRouteCandidate:
    text = request_text.lower()
    keywords = CAPABILITY_KEYWORDS.get(capability_id, ())
    matches = _contains_any(text, keywords)
    score = float(len(matches))
    reasons = [f"matched keyword {m!r}" for m in matches]

    eps_matches = _contains_any(text, CAPABILITY_KEYWORDS["subsystem.eps.basic.v1"])
    orbit_matches = _contains_any(text, CAPABILITY_KEYWORDS["orbit_environment.leo_simple.v1"])
    whole_power_thermal_matches = _contains_any(text, CAPABILITY_KEYWORDS.get("whole_spacecraft.basic_power_thermal_orbit.v1", ()))
    whole_power_attitude_matches = _contains_any(text, CAPABILITY_KEYWORDS.get("whole_spacecraft.basic_power_attitude_orbit.v1", ()))
    whole_matches = _contains_any(text, CAPABILITY_KEYWORDS["whole_spacecraft.basic_power_orbit.v1"])
    solar_matches = _contains_any(text, CAPABILITY_KEYWORDS["component.solar_panel.v1"])
    battery_matches = _contains_any(text, CAPABILITY_KEYWORDS["component.battery.v1"])
    rw_matches = _contains_any(text, CAPABILITY_KEYWORDS["component.reaction_wheel.v1"])
    adcs_matches = _contains_any(text, CAPABILITY_KEYWORDS.get("subsystem.adcs.basic_rw_pointing.v1", ()))
    adcs_fidelity_matches = _contains_any(text, CAPABILITY_KEYWORDS.get("subsystem.adcs_fidelity.v1", ()))
    adcs_closed_loop_matches = _contains_any(text, CAPABILITY_KEYWORDS.get("subsystem.adcs_closed_loop.basic.v1", ()))
    thermal_matches = _contains_any(text, CAPABILITY_KEYWORDS.get("subsystem.thermal.basic_lumped.v1", ()))
    comm_matches = _contains_any(text, CAPABILITY_KEYWORDS.get("subsystem.comm.basic_ground_pass.v1", ()))
    data_queue_matches = _contains_any(text, CAPABILITY_KEYWORDS.get("component.data_queue.v1", ()))
    onboard_storage_matches = _contains_any(text, CAPABILITY_KEYWORDS.get("component.onboard_storage.v1", ()))
    power_thermal_coupled_matches = _contains_any(text, CAPABILITY_KEYWORDS.get("whole_spacecraft.power_thermal_orbit_coupled.v1", ()))
    comm_payload_coupled_matches = _contains_any(text, CAPABILITY_KEYWORDS.get("whole_spacecraft.comm_payload_mission_coupled.v1", ()))
    maneuver_coupled_matches = _contains_any(text, CAPABILITY_KEYWORDS.get("whole_spacecraft.maneuver_orbit_attitude.v1", ()))
    orbit_adcs_coupled_matches = _contains_any(text, CAPABILITY_KEYWORDS.get("whole_spacecraft.orbit_adcs_fidelity.v1", ()))
    payload_matches = _contains_any(text, CAPABILITY_KEYWORDS.get("component.payload.v1", ()) + CAPABILITY_KEYWORDS.get("component.payload_sensor.v1", ()))
    coupled_cues = _contains_any(text, ("coupled", "coupling", "耦合", "联合", "联动", "route-b", "route b", "任务级", "mission coupled", "mission-coupled"))
    propulsion_matches = _contains_any(text, CAPABILITY_KEYWORDS.get("subsystem.propulsion.source_native.v1", ()))
    finite_burn_proxy_matches = _contains_any(
        text,
        (
            "finite-burn", "finite burn", "burn proxy", "maneuver proxy", "thruster firing",
            "propellant mass update", "propellant depletion", "delta-v accumulation", "delta-v", "delta v",
            "orbit perturbation", "attitude disturbance trace", "attitude disturbance",
            "有限点火", "有限燃烧", "推进剂质量更新", "速度增量", "轨道扰动", "姿态扰动", "机动代理",
        ),
    )
    strong_maneuver_proxy_request = bool(propulsion_matches) and bool(finite_burn_proxy_matches or maneuver_coupled_matches)
    strong_orbit_adcs_request = (bool(orbit_adcs_coupled_matches) or (bool(orbit_matches) and (bool(adcs_matches) or bool(adcs_fidelity_matches) or bool(adcs_closed_loop_matches)) and bool(coupled_cues))) and not strong_maneuver_proxy_request

    source_native_cues = ("source-native", "source native", "源码", "原生", "原 src", "原src")
    if _contains_any(text, source_native_cues):
        if ".source_native." in capability_id:
            score += 10.0
            reasons.append("source-native/source-code cue")
        elif capability_id.startswith("subsystem.") or capability_id.startswith("whole_spacecraft."):
            score -= 4.0
            reasons.append("source-native route preferred by request")

    unified_runtime_cues = _contains_any(text, (
        "统一运行图", "unified runtime", "unified process/task",
        "统一 process/task", "统一消息链", "统一basilisk",
    ))
    if capability_id == "subsystem.adcs_unified_native.v1":
        if matches:
            score += 100.0
            reasons.append("explicit recommended ADCS unified-runtime cue")
        if unified_runtime_cues and (adcs_matches or adcs_fidelity_matches or adcs_closed_loop_matches):
            score += 80.0
            reasons.append("unified runtime + ADCS request")
        if rw_matches:
            score += 8.0
    elif capability_id == "whole_spacecraft.unified_native.v1":
        if matches:
            score += 110.0
            reasons.append("explicit recommended whole-spacecraft unified-runtime cue")
        requested_domains = sum(bool(item) for item in (adcs_matches or adcs_fidelity_matches, eps_matches or battery_matches, thermal_matches, comm_matches, payload_matches or data_queue_matches or onboard_storage_matches, orbit_matches))
        if unified_runtime_cues and (whole_matches or requested_domains >= 2):
            score += 90.0
            reasons.append("unified runtime + multi-domain whole-spacecraft request")
        if requested_domains >= 3:
            score += 20.0
            reasons.append("multi-domain unified whole-spacecraft request")
    elif capability_id == "subsystem.propulsion.unified_native.v1":
        if matches:
            score += 100.0
            reasons.append("explicit recommended propulsion unified-runtime cue")
        if unified_runtime_cues and propulsion_matches:
            score += 80.0
            reasons.append("unified runtime + propulsion request")
    elif capability_id == "subsystem.eps.unified_native.v1":
        if matches:
            score += 100.0
            reasons.append("explicit recommended EPS unified-runtime cue")
        if unified_runtime_cues and eps_matches:
            score += 80.0
            reasons.append("unified runtime + EPS request")
    elif capability_id == "subsystem.comm_data.unified_native.v1":
        if matches:
            score += 100.0
            reasons.append("explicit recommended Comm/Data unified-runtime cue")
        if unified_runtime_cues and (comm_matches or data_queue_matches or onboard_storage_matches):
            score += 80.0
            reasons.append("unified runtime + Comm/Data request")
    elif capability_id == "whole_spacecraft.power_thermal_orbit_coupled.v1":
        if power_thermal_coupled_matches:
            score += 28.0
            reasons.append("HF-5 power-thermal-orbit coupling cue")
        if eps_matches and orbit_matches and thermal_matches and coupled_cues:
            score += 12.0
            reasons.append("combined EPS + orbit + thermal + coupling cue")
        if comm_matches or data_queue_matches or onboard_storage_matches or payload_matches:
            score -= 5.0
            reasons.append("comm/payload mission coupling route preferred when data flow is requested")
    elif capability_id == "whole_spacecraft.comm_payload_mission_coupled.v1":
        if comm_payload_coupled_matches:
            score += 12.0
            reasons.append("HF-6 comm-payload mission coupling cue")
        if comm_matches and (payload_matches or data_queue_matches or onboard_storage_matches) and coupled_cues:
            score += 16.0
            reasons.append("combined comm + payload/storage + mission coupling cue")
        if comm_matches and (payload_matches or data_queue_matches or onboard_storage_matches):
            score += 6.0
            reasons.append("comm + payload/storage mission cue")
        if orbit_matches and comm_matches and (payload_matches or onboard_storage_matches):
            score += 4.0
            reasons.append("ground-access downlink/storage mission cue")
        if thermal_matches and eps_matches and not (payload_matches or data_queue_matches or onboard_storage_matches):
            score -= 4.0
            reasons.append("power-thermal-orbit coupling route preferred")
    elif capability_id == "whole_spacecraft.orbit_adcs_fidelity.v1":
        if orbit_adcs_coupled_matches:
            score += 44.0
            reasons.append("INT-1 orbit+ADCS integration cue")
        if strong_orbit_adcs_request:
            score += 48.0
            reasons.append("combined orbit + ADCS frame/target integration request")
        if orbit_matches and (adcs_matches or adcs_fidelity_matches or adcs_closed_loop_matches):
            score += 18.0
            reasons.append("orbit and ADCS both requested")
        if propulsion_matches or strong_maneuver_proxy_request:
            score -= 18.0
            reasons.append("maneuver/orbit/attitude route preferred for propulsion burn coupling")
        if thermal_matches or comm_matches or power_thermal_coupled_matches or comm_payload_coupled_matches:
            score -= 8.0
            reasons.append("other Route-B coupled capability better matches non-ADCS integration")
    elif capability_id == "whole_spacecraft.maneuver_orbit_attitude.v1":
        if maneuver_coupled_matches:
            score += 14.0
            reasons.append("HF-7 maneuver/orbit/attitude coupling cue")
        if strong_maneuver_proxy_request:
            score += 45.0
            reasons.append("explicit finite-burn/thruster/delta-v proxy request selects HF-7 maneuver coupling")
        if propulsion_matches and orbit_matches and (coupled_cues or maneuver_coupled_matches):
            score += 12.0
            reasons.append("combined propulsion + orbit + coupling cue")
        if comm_matches or thermal_matches or comm_payload_coupled_matches:
            score -= 4.0
            reasons.append("another Route-B coupled capability better matches non-propulsion coupling")
        if propulsion_matches and maneuver_coupled_matches:
            score += 6.0
            reasons.append("propulsion finite-burn cue wins over generic whole-spacecraft cue")
    elif capability_id == "whole_spacecraft.basic_power_thermal_orbit.v1":
        if whole_power_thermal_matches or (whole_matches and thermal_matches):
            score += 8.0
            reasons.append("whole-spacecraft power-thermal-orbit cue")
        if eps_matches and orbit_matches and thermal_matches:
            score += 7.0
            reasons.append("combined EPS + orbit + thermal cue")
        if coupled_cues or power_thermal_coupled_matches:
            score -= 8.0
            reasons.append("HF-5 coupled route preferred")
        if data_queue_matches or onboard_storage_matches:
            score -= 6.0
            reasons.append("standalone data queue/storage route preferred")
    elif capability_id == "whole_spacecraft.basic_power_attitude_orbit.v1":
        if whole_power_attitude_matches or (whole_matches and adcs_matches):
            score += 8.0
            reasons.append("whole-spacecraft power-attitude-orbit cue")
        if eps_matches and orbit_matches and adcs_matches:
            score += 7.0
            reasons.append("combined EPS + orbit + ADCS cue")
        if thermal_matches:
            score -= 3.0
            reasons.append("power-thermal-orbit route preferred when thermal is requested")
        if data_queue_matches or onboard_storage_matches:
            score -= 6.0
            reasons.append("standalone data queue/storage route preferred")
    elif capability_id == "whole_spacecraft.basic_power_orbit.v1":
        if whole_matches:
            score += 6.0
            reasons.append("whole-spacecraft cue")
        if eps_matches and orbit_matches:
            score += 5.0
            reasons.append("combined EPS + orbit cue")
        if adcs_matches:
            score -= 4.0
            reasons.append("power-attitude-orbit route preferred when ADCS is requested")
        if thermal_matches:
            score -= 4.0
            reasons.append("power-thermal-orbit route preferred when thermal is requested")
        if comm_matches:
            score -= 3.0
            reasons.append("communication subsystem route preferred when comm/downlink is requested")
        if coupled_cues or power_thermal_coupled_matches or comm_payload_coupled_matches:
            score -= 5.0
            reasons.append("Route-B coupled capability preferred")
        if data_queue_matches or onboard_storage_matches:
            score -= 6.0
            reasons.append("standalone data queue/storage route preferred")
    elif capability_id == "subsystem.eps.basic.v1":
        if eps_matches:
            score += 3.0
        if whole_matches and orbit_matches:
            score -= 2.0
            reasons.append("whole-spacecraft route preferred over EPS-only")
    elif capability_id == "orbit_environment.orbit_fidelity.v1":
        fidelity_matches = _contains_any(text, CAPABILITY_KEYWORDS.get("orbit_environment.orbit_fidelity.v1", ()))
        medium_matches = _contains_any(text, CAPABILITY_KEYWORDS.get("orbit_environment.medium_fidelity.v1", ()))
        explicit_orbit_fidelity = bool(orbit_matches) or bool(fidelity_matches) or bool(_contains_any(text, ("orbit_environment", "orbit trace", "轨道环境", "轨道仿真", "地面站可见性")))
        if fidelity_matches and explicit_orbit_fidelity:
            score += 24.0
            reasons.append("active ORB-1 orbit-fidelity cue")
        elif orbit_matches:
            score += 12.0
            reasons.append("generic orbit request defaults to active ORB-1 orbit-fidelity capability")
        if medium_matches:
            score += 6.0
            reasons.append("HF-3 orbit cue migrated to ORB-1 replacement")
        if comm_matches and (payload_matches or data_queue_matches or onboard_storage_matches):
            score -= 12.0
            reasons.append("comm/payload mission route preferred over orbit-only environment")
        elif comm_matches:
            score -= 14.0
            reasons.append("comm/downlink route preferred over orbit-only environment")
    elif capability_id == "orbit_environment.medium_fidelity.v1":
        medium_matches = _contains_any(text, CAPABILITY_KEYWORDS.get("orbit_environment.medium_fidelity.v1", ()))
        explicit_orbit_medium = bool(orbit_matches) or bool(_contains_any(text, ("orbit_environment", "orbit trace", "轨道环境", "轨道仿真", "太阳向量", "日食几何", "本影", "半影", "ground-station visibility")))
        if medium_matches and explicit_orbit_medium:
            score += 18.0
            reasons.append("active Route-B orbit/environment cue")
        elif orbit_matches:
            score += 8.0
            reasons.append("generic orbit request defaults to active Route-B orbit/environment capability")
        if orbit_matches and medium_matches:
            score += 6.0
            reasons.append("orbit/environment + medium-fidelity cue")
        if eps_matches and not (medium_matches or explicit_orbit_medium):
            score -= 2.0
            reasons.append("EPS route preferred unless orbit/environment is explicit")
        if comm_matches and (payload_matches or data_queue_matches or onboard_storage_matches):
            score -= 12.0
            reasons.append("comm/payload mission route preferred over orbit-only environment")
        elif comm_matches:
            score -= 16.0
            reasons.append("comm/downlink route preferred over orbit-only environment")
    elif capability_id == "orbit_environment.leo_simple.v1":
        medium_matches = _contains_any(text, CAPABILITY_KEYWORDS.get("orbit_environment.medium_fidelity.v1", ()))
        if orbit_matches:
            score += 3.0
        if medium_matches:
            score -= 8.0
            reasons.append("HF-3 medium-fidelity orbit route preferred")
        if eps_matches and orbit_matches and not medium_matches:
            score -= 2.0
            reasons.append("whole-spacecraft route preferred for EPS + orbit")
    elif capability_id == "component.solar_panel.v1":
        # Solar component should beat EPS only when the request is about the
        # panel/array itself and not battery+load energy balance.
        if solar_matches and not eps_matches:
            score += 3.0
        if eps_matches and (battery_matches or "负载" in text or "load" in text):
            score -= 2.0
            reasons.append("EPS route preferred for battery/load behavior")
    elif capability_id == "subsystem.adcs_fidelity.v1":
        if adcs_matches or adcs_closed_loop_matches or adcs_fidelity_matches:
            score += 40
        if adcs_fidelity_matches:
            score += 30
        if rw_matches:
            score += 8
        if any(k in text for k in ("environmental torque", "gravity gradient", "magnetic torque", "sensor dropout", "star tracker", "gyro bias", "wheel momentum", "环境扰动", "飞轮动量", "星敏", "陀螺")):
            score += 25
        if strong_maneuver_proxy_request:
            score -= 55
            reasons.append("finite-burn/thruster/delta-v proxy route preferred over ADCS-only despite attitude-disturbance wording")
        if strong_orbit_adcs_request or orbit_adcs_coupled_matches:
            score -= 30
            reasons.append("INT-1 orbit+ADCS integration route preferred over ADCS-only")
    elif capability_id == "subsystem.adcs_closed_loop.basic.v1":
        if adcs_closed_loop_matches:
            score += 10.0
            reasons.append("basic closed-loop ADCS cue")
        if adcs_matches:
            score += 6.0
            reasons.append("generic ADCS request defaults to active closed-loop ADCS capability")
        if adcs_matches and adcs_closed_loop_matches:
            score += 4.0
            reasons.append("ADCS + closed-loop cue")
        if rw_matches and (adcs_closed_loop_matches or adcs_matches):
            score += 2.0
            reasons.append("reaction-wheel ADCS cue")
    elif capability_id == "subsystem.adcs.basic_rw_pointing.v1":
        if adcs_matches:
            score += 4.0
            reasons.append("ADCS subsystem cue")
        if adcs_closed_loop_matches:
            score -= 5.0
            reasons.append("HF-4 closed-loop ADCS route preferred")
        if rw_matches and adcs_matches:
            score += 2.0
            reasons.append("reaction-wheel pointing cue")
    elif capability_id == "subsystem.thermal.basic_lumped.v1":
        if thermal_matches:
            score += 5.0
            reasons.append("thermal subsystem cue")
        if whole_matches or (eps_matches and orbit_matches):
            score -= 2.0
            reasons.append("whole-spacecraft power-thermal-orbit route preferred for coupled requests")
        if power_thermal_coupled_matches or (eps_matches and orbit_matches and coupled_cues):
            score -= 5.0
            reasons.append("HF-5 coupled whole-spacecraft route preferred")
    elif capability_id == "subsystem.comm.basic_ground_pass.v1":
        if comm_matches:
            score += 6.0
            reasons.append("communication/downlink subsystem cue")
        if orbit_matches and comm_matches:
            score += 2.0
            reasons.append("ground-pass orbit access cue")
        if data_queue_matches or onboard_storage_matches:
            score -= 2.0
            reasons.append("standalone storage/queue component route preferred")
        if comm_payload_coupled_matches or ((payload_matches or onboard_storage_matches or data_queue_matches) and coupled_cues):
            score -= 5.0
            reasons.append("HF-6 mission-coupled comm/payload route preferred")
    elif capability_id == "subsystem.propulsion.source_native.v1":
        if propulsion_matches:
            score += 8.0
            reasons.append("propulsion/fuel source-native subsystem cue")
        if _contains_any(text, ("delta-v", "delta v", "变轨", "轨道机动", "连续推力", "多脉冲")):
            score -= 2.0
            reasons.append("request may exceed source-native propulsion profile boundary")
    elif capability_id == "component.data_queue.v1":
        if data_queue_matches:
            score += 6.0
            reasons.append("data-queue component cue")
        if comm_matches and ("ground" in text or "地面站" in text or "downlink" in text or "下行" in text):
            score -= 2.0
            reasons.append("comm subsystem route preferred for ground-pass link behavior")
    elif capability_id == "component.onboard_storage.v1":
        if onboard_storage_matches:
            score += 6.0
            reasons.append("onboard-storage component cue")
        if comm_payload_coupled_matches or (comm_matches and payload_matches and onboard_storage_matches):
            score -= 6.0
            reasons.append("HF-6 comm/payload mission coupling route preferred")
        if data_queue_matches and not onboard_storage_matches:
            score -= 1.0
    elif capability_id == "component.link_budget.v1":
        if _contains_any(text, CAPABILITY_KEYWORDS.get("component.link_budget.v1", ())):
            score += 10.0
            reasons.append("standalone link-budget component cue")
        if _contains_any(text, ("standalone", "stand-alone", "独立", "不要建模完整过境", "do not model a full ground pass")):
            score += 4.0
            reasons.append("standalone/no-full-ground-pass cue")
    elif capability_id in {"component.antenna.v1", "component.transmitter.v1"}:
        if _contains_any(text, CAPABILITY_KEYWORDS.get("component.link_budget.v1", ())) and _contains_any(text, ("standalone", "stand-alone", "do not model a full ground pass")):
            score -= 4.0
            reasons.append("standalone link-budget route preferred over antenna/transmitter components")
        if matches:
            score += 5.0
            reasons.append("component-specific source-native cue")
    elif capability_id.startswith("component.") and capability_id in CAPABILITY_KEYWORDS:
        if matches:
            score += 5.0
            reasons.append("component-specific source-native cue")

    elif capability_id == "component.reaction_wheel.v1":
        if rw_matches and adcs_matches:
            score -= 2.0
            reasons.append("ADCS subsystem route preferred for pointing behavior")

    return CapabilityRouteCandidate(capability_id=capability_id, score=score, matched_keywords=matches, reasons=tuple(reasons))


def _is_negated_keyword_use(request_text: str, keyword: str) -> bool:
    """Return True when an unsupported keyword is used as a negated boundary cue.

    Real user requests often say things like "不要做完整高保真" or
    "do not claim high fidelity".  Those phrases should strengthen guardrails,
    not cause the planner to reject a supported medium-fidelity/proxy route.
    """

    lower = request_text.lower()
    kw = keyword.lower()
    start = lower.find(kw)
    if start < 0:
        return False
    prefix = lower[max(0, start - 18):start]
    suffix = lower[start:start + len(kw) + 20]
    negators = (
        "不要", "不用", "不做", "不要做", "不要假装", "不要声称", "不要宣称",
        "不需要", "避免", "先不要", "只要", "not ", "do not", "don't", "without",
        "must not", "do not claim", "not full", "not high",
    )
    if any(token in prefix for token in negators):
        return True
    if kw in ("高保真推进", "完整高保真", "full high-fidelity", "high-fidelity") and any(token in suffix for token in ("不要", "不要假装", "不要声称", "不要宣称")):
        return True
    return False


def _filter_negated_matches(request_text: str, matches: Sequence[str]) -> tuple[str, ...]:
    return tuple(m for m in matches if not _is_negated_keyword_use(request_text, m))


def _detect_unsupported(request_text: str, selected_capability_id: str | None) -> tuple[UnsupportedRequirement, ...]:
    out: list[UnsupportedRequirement] = []
    for domain, keywords in UNSUPPORTED_DOMAIN_KEYWORDS.items():
        raw_matches = _contains_any(request_text, keywords)
        matches = _filter_negated_matches(request_text, raw_matches)
        if not matches:
            continue
        exceptions = SUPPORTED_UNSUPPORTED_EXCEPTIONS.get(domain, ())
        if selected_capability_id in exceptions:
            advanced_override = _filter_negated_matches(
                request_text,
                _contains_any(
                    request_text,
                    (
                        "高保真推进",
                        "完整推进",
                        "连续推力",
                        "多脉冲优化",
                        "精确变轨",
                        "轨道机动规划",
                        "high-fidelity propulsion",
                        "continuous thrust",
                        "multi-burn optimization",
                        "precise maneuver planning",
                    ),
                ),
            )
            if not advanced_override:
                continue
        # Basic ground-pass communication is supported by P10-A. Only advanced
        # RF/contact-planning features remain unsupported here.
        reason_code = (
            str(ReasonCode.ARBITRARY_CODE_TOOL_FORBIDDEN)
            if domain == "arbitrary_code_execution"
            else (
                str(ReasonCode.HIGH_FIDELITY_CLAIM_BLOCKED)
                if domain in {"high_fidelity_whole_spacecraft", "high_fidelity_orbit"}
                else str(ReasonCode.OUT_OF_SCOPE_REQUEST)
            )
        )
        out.append(UnsupportedRequirement(
            domain=domain,
            matched_keywords=matches,
            message=_domain_message(domain),
            reason_code=reason_code,
        ))
    return tuple(out)


def _has_number_near(text: str, words: Sequence[str]) -> bool:
    lower = text.lower()
    for word in words:
        w = re.escape(word.lower())
        if re.search(rf"{w}[^0-9]{{0,30}}[0-9]", lower):
            return True
        if re.search(rf"[0-9][^\n。；;,，]{{0,30}}{w}", lower):
            return True
    return False


def _missing_parameter_policy(selected_capability_id: str | None, request_text: str) -> tuple[MissingParameter, ...]:
    if selected_capability_id is None:
        return ()
    missing: list[MissingParameter] = []
    if not _has_number_near(request_text, ("duration", "simulate", "仿真", "持续", "时长")):
        missing.append(MissingParameter("simulation.duration_s", "TaskSpec.simulation", "use capability example/default", unit="s"))
    if not _has_number_near(request_text, ("sample", "采样", "步长", "间隔")):
        missing.append(MissingParameter("simulation.sample_s", "TaskSpec.simulation", "use capability example/default", unit="s"))

    if selected_capability_id == "whole_spacecraft.composite_digital_twin.v1":
        checks = [
            ("parameters.battery_capacity_wh", ("battery capacity", "电池容量", "容量"), 160.0, "Wh"),
            ("parameters.initial_soc", ("initial soc", "初始 soc", "初始电量", "荷电"), 0.62, "ratio"),
            ("parameters.solar_power_w", ("solar power", "太阳阵列功率", "太阳功率"), 95.0, "W"),
            ("parameters.payload_power_w", ("payload power", "载荷功率"), 38.0, "W"),
            ("parameters.instrument_baud_bps", ("payload data rate", "载荷数据速率", "数据生成速率"), 2500000.0, "bit/s"),
            ("parameters.transmitter_baud_bps", ("downlink rate", "下行速率", "通信速率"), 1500000.0, "bit/s"),
            ("parameters.thermal_step_s", ("thermal step", "热控步长", "热步长"), 10.0, "s"),
        ]
        for name, words, default, unit in checks:
            if not _has_number_near(request_text, words):
                missing.append(MissingParameter(name, "TaskSpec.parameters", "use V37-B composite adapter default", default=default, unit=unit))
    elif selected_capability_id == "whole_spacecraft.power_thermal_orbit_coupled.v1":
        checks = [
            ("spacecraft.eps.battery_capacity_wh", ("battery capacity", "电池容量", "容量"), 160.0, "Wh"),
            ("spacecraft.eps.initial_soc", ("initial soc", "初始 soc", "初始电量", "荷电"), 0.68, "ratio"),
            ("spacecraft.eps.solar_power_w", ("solar power", "太阳阵列功率", "太阳功率"), 145.0, "W"),
            ("spacecraft.eps.base_load_w", ("base load", "基础负载", "平台负载"), 42.0, "W"),
            ("spacecraft.thermal.heater_setpoint_c", ("heater setpoint", "加热器设定", "加热器阈值"), 4.0, "degC"),
            ("spacecraft.thermal.battery_derating_c", ("battery derating", "温度降额", "电池降额"), 0.0, "degC"),
            ("orbit_environment.altitude_m", ("altitude", "高度", "轨道高度"), 500000.0, "m"),
        ]
        for name, words, default, unit in checks:
            if not _has_number_near(request_text, words):
                missing.append(MissingParameter(name, "TaskSpec", "use HF-5 adapter default/example value", default=default, unit=unit))
    elif selected_capability_id == "whole_spacecraft.comm_payload_mission_coupled.v1":
        checks = [
            ("parameters.payload_generated_bps", ("payload data", "generated bps", "数据生成", "载荷数据"), 2400.0, "bit/s"),
            ("parameters.downlink_rate_bps", ("downlink rate", "下行速率", "链路速率"), 2500000.0, "bit/s"),
            ("parameters.storage_capacity_bits", ("storage capacity", "存储容量", "缓存容量"), 24000000.0, "bit"),
            ("parameters.payload_power_w", ("payload power", "载荷功率"), 28.0, "W"),
            ("parameters.transmitter_power_w", ("tx power", "发射功率", "发射机功率"), 8.0, "W"),
            ("parameters.initial_soc", ("initial soc", "初始 soc", "初始电量"), 0.72, "ratio"),
            ("parameters.ground_station", ("ground station", "地面站", "站点"), {"latitude_deg": 0.0, "longitude_deg": 0.0}, "object"),
        ]
        for name, words, default, unit in checks:
            if not _has_number_near(request_text, words):
                missing.append(MissingParameter(name, "TaskSpec", "use HF-6 adapter default/example value", default=default, unit=unit))
    elif selected_capability_id == "whole_spacecraft.maneuver_orbit_attitude.v1":
        checks = [
            ("parameters.dry_mass_kg", ("dry mass", "干质量"), 120.0, "kg"),
            ("parameters.initial_propellant_kg", ("initial propellant", "propellant", "初始推进剂", "推进剂余量"), 8.0, "kg"),
            ("parameters.thrust_n", ("thrust", "推力"), 0.45, "N"),
            ("parameters.specific_impulse_s", ("specific impulse", "isp", "比冲"), 220.0, "s"),
            ("parameters.burn_start_s", ("burn start", "点火开始", "机动开始"), 600.0, "s"),
            ("parameters.burn_duration_s", ("burn duration", "点火持续", "机动持续"), 180.0, "s"),
            ("orbit_environment.altitude_m", ("altitude", "高度", "轨道高度"), 500000.0, "m"),
        ]
        for name, words, default, unit in checks:
            if not _has_number_near(request_text, words):
                missing.append(MissingParameter(name, "TaskSpec", "use HF-7 adapter default/example value", default=default, unit=unit))
    elif selected_capability_id == "whole_spacecraft.basic_power_thermal_orbit.v1":
        checks = [
            ("spacecraft.eps.battery_capacity_wh", ("battery capacity", "电池容量", "容量"), 160.0, "Wh"),
            ("spacecraft.eps.initial_soc", ("initial soc", "初始 soc", "初始电量", "荷电"), 0.65, "ratio"),
            ("spacecraft.eps.solar_power_w", ("solar power", "太阳阵列功率", "太阳功率"), 120.0, "W"),
            ("spacecraft.eps.payload_power_w", ("payload power", "载荷功率", "载荷负载"), 30.0, "W"),
            ("orbit_environment.altitude_m", ("altitude", "高度", "轨道高度"), 500000.0, "m"),
            ("spacecraft.thermal.solar_heat_w", ("solar heat", "太阳热", "热输入"), 45.0, "W"),
            ("spacecraft.thermal.radiator_area_m2", ("radiator area", "散热器面积", "散热面积"), 0.35, "m^2"),
            ("spacecraft.thermal.heater_setpoint_c", ("heater setpoint", "加热器设定", "加热器阈值"), 5.0, "degC"),
        ]
        for name, words, default, unit in checks:
            if not _has_number_near(request_text, words):
                missing.append(MissingParameter(name, "TaskSpec", "use adapter default/example value", default=default, unit=unit))
    elif selected_capability_id == "whole_spacecraft.basic_power_attitude_orbit.v1":
        checks = [
            ("spacecraft.eps.battery_capacity_wh", ("battery capacity", "电池容量", "容量"), 160.0, "Wh"),
            ("spacecraft.eps.initial_soc", ("initial soc", "初始 soc", "初始电量", "荷电"), 0.70, "ratio"),
            ("spacecraft.eps.solar_power_w", ("solar power", "太阳阵列功率", "太阳功率"), 150.0, "W"),
            ("spacecraft.eps.payload_power_w", ("payload power", "载荷功率", "载荷负载"), 25.0, "W"),
            ("orbit_environment.altitude_m", ("altitude", "高度", "轨道高度"), 500000.0, "m"),
            ("spacecraft.adcs.initial_pointing_error_deg", ("pointing error", "姿态误差", "指向误差", "初始误差"), 8.0, "deg"),
            ("spacecraft.adcs.control_kp_nm_per_rad", ("control gain", "控制增益", "kp"), 0.08, "N*m/rad"),
        ]
        for name, words, default, unit in checks:
            if not _has_number_near(request_text, words):
                missing.append(MissingParameter(name, "TaskSpec", "use adapter default/example value", default=default, unit=unit))
    elif selected_capability_id == "whole_spacecraft.basic_power_orbit.v1":
        checks = [
            ("spacecraft.eps.battery_capacity_wh", ("battery capacity", "电池容量", "容量"), 160.0, "Wh"),
            ("spacecraft.eps.initial_soc", ("initial soc", "初始 soc", "初始电量", "荷电"), 0.62, "ratio"),
            ("spacecraft.eps.solar_power_w", ("solar power", "太阳阵列功率", "太阳功率"), 95.0, "W"),
            ("spacecraft.eps.payload_power_w", ("payload power", "载荷功率", "载荷负载"), 30.0, "W"),
            ("orbit_environment.altitude_m", ("altitude", "高度", "轨道高度"), 500000.0, "m"),
        ]
        for name, words, default, unit in checks:
            if not _has_number_near(request_text, words):
                missing.append(MissingParameter(name, "TaskSpec", "use adapter default/example value", default=default, unit=unit))
    elif selected_capability_id == "subsystem.adcs_fidelity.v1":
        checks = [
            ("parameters.initial_attitude_error_deg", ("attitude error", "pointing error", "姿态误差", "指向误差", "初始误差"), 12.0, "deg"),
            ("parameters.control_kp_nm_per_rad", ("control gain", "控制增益", "kp"), 0.10, "N*m/rad"),
            ("parameters.control_kd_nm_per_rad_s", ("damping", "kd", "阻尼"), 0.85, "N*m/(rad/s)"),
            ("parameters.max_wheel_torque_nm", ("torque limit", "力矩上限", "最大力矩", "扭矩上限"), 0.05, "N*m"),
        ]
        for name, words, default, unit in checks:
            if not _has_number_near(request_text, words):
                missing.append(MissingParameter(name, "TaskSpec.parameters", "use adapter default/example value", default=default, unit=unit))
    elif selected_capability_id == "subsystem.adcs.basic_rw_pointing.v1":
        checks = [
            ("parameters.initial_pointing_error_deg", ("pointing error", "姿态误差", "指向误差", "初始误差"), 8.0, "deg"),
            ("parameters.control_kp_nm_per_rad", ("control gain", "控制增益", "kp"), 0.08, "N*m/rad"),
            ("parameters.max_rw_torque_nm", ("torque limit", "力矩上限", "最大力矩", "扭矩上限"), 0.03, "N*m"),
        ]
        for name, words, default, unit in checks:
            if not _has_number_near(request_text, words):
                missing.append(MissingParameter(name, "TaskSpec.parameters", "use adapter default/example value", default=default, unit=unit))
    elif selected_capability_id == "subsystem.thermal.basic_lumped.v1":
        checks = [
            ("parameters.internal_power_w", ("internal power", "内部功耗", "功耗", "热耗散"), 28.0, "W"),
            ("parameters.solar_heat_w", ("solar heat", "太阳热", "热输入"), 45.0, "W"),
            ("parameters.radiator_area_m2", ("radiator area", "散热器面积", "散热面积"), 0.35, "m^2"),
            ("parameters.heater_setpoint_c", ("heater setpoint", "加热器设定", "加热器阈值"), 5.0, "degC"),
        ]
        for name, words, default, unit in checks:
            if not _has_number_near(request_text, words):
                missing.append(MissingParameter(name, "TaskSpec.parameters", "use adapter default/example value", default=default, unit=unit))
    elif selected_capability_id == "subsystem.comm.basic_ground_pass.v1":
        checks = [
            ("parameters.ground_station", ("ground station", "地面站", "站点"), {"latitude_deg": 0.0, "longitude_deg": 0.0}, "object"),
            ("parameters.generated_bps", ("generated bps", "data generation", "数据生成", "生成速率"), 1500.0, "bit/s"),
            ("parameters.raw_rate_bps", ("raw rate", "downlink rate", "下行速率", "链路速率"), 2000000.0, "bit/s"),
            ("parameters.tx_power_w", ("tx power", "transmit power", "发射功率", "发射机功率"), 5.0, "W"),
            ("parameters.storage_capacity_bits", ("storage", "backlog", "缓存", "存储容量"), 12000000.0, "bit"),
        ]
        for name, words, default, unit in checks:
            if not _has_number_near(request_text, words):
                missing.append(MissingParameter(name, "TaskSpec.parameters", "use adapter default/example value", default=default, unit=unit))
    elif selected_capability_id == "component.data_queue.v1":
        checks = [
            ("parameters.capacity_bits", ("capacity", "容量", "队列容量", "缓存容量"), 1000000.0, "bit"),
            ("parameters.generated_bps", ("generated bps", "data generation", "数据生成", "生成速率"), 1000.0, "bit/s"),
            ("parameters.downlink_bps", ("downlink", "readout", "读取", "下行", "输出速率"), 0.0, "bit/s"),
        ]
        for name, words, default, unit in checks:
            if not _has_number_near(request_text, words):
                missing.append(MissingParameter(name, "TaskSpec.parameters", "use adapter default/example value", default=default, unit=unit))
    elif selected_capability_id == "component.onboard_storage.v1":
        checks = [
            ("parameters.capacity_bits", ("capacity", "容量", "存储容量"), 20000000.0, "bit"),
            ("parameters.generated_bps", ("generated bps", "data generation", "数据生成", "生成速率"), 1000.0, "bit/s"),
            ("parameters.downlink_bps", ("downlink", "下行", "读取", "输出速率"), 0.0, "bit/s"),
            ("parameters.high_watermark", ("high watermark", "高水位", "告警阈值"), 0.9, "ratio"),
        ]
        for name, words, default, unit in checks:
            if not _has_number_near(request_text, words):
                missing.append(MissingParameter(name, "TaskSpec.parameters", "use adapter default/example value", default=default, unit=unit))
    elif selected_capability_id == "subsystem.propulsion.source_native.v1":
        checks = [
            ("parameters.fuel_capacity_kg", ("fuel capacity", "propellant capacity", "tank capacity", "燃料箱容量", "推进剂容量", "燃料容量"), 2.0, "kg"),
            ("parameters.initial_fuel_kg", ("initial fuel", "initial propellant", "propellant remaining", "fuel remaining", "初始燃料", "初始推进剂", "剩余燃料", "推进剂余量"), 1.0, "kg"),
            ("parameters.min_soc_for_burn", ("min soc", "最低soc", "最低荷电", "点火最低电量"), 0.3, "ratio"),
        ]
        for name, words, default, unit in checks:
            if not _has_number_near(request_text, words):
                missing.append(MissingParameter(name, "TaskSpec.parameters", "use adapter default/example value", default=default, unit=unit))
    return tuple(missing)


def _assumptions_for(selected_capability_id: str | None, missing: Sequence[MissingParameter]) -> tuple[str, ...]:
    assumptions = []
    if selected_capability_id == "whole_spacecraft.composite_digital_twin.v1":
        assumptions.append("Use the existing unified Basilisk SimBaseClass graph rather than composing a second parallel model or synthetic state bus.")
        assumptions.append("Enable orbit/environment, spacecraft dynamics, ADCS, EPS, thermal, communication/data, payload, and propulsion using the registered V37-B defaults and effect routes unless explicitly overridden.")
        assumptions.append("Treat native and proxy cross-subsystem links according to the runtime coupling matrix; do not upgrade proxy evidence into a high-fidelity claim.")
        assumptions.append("Interpret mission_success_score only as a deterministic gate fraction, never as a calibrated probability.")
    if selected_capability_id == "whole_spacecraft.power_thermal_orbit_coupled.v1":
        assumptions.append("Use the HF-5 route-B power-thermal-orbit coupled model: eclipse drives solar generation, electrical loads drive heat generation, heater demand feeds back into EPS load, and battery capacity derating is temperature dependent.")
        assumptions.append("Treat orbit/environment, EPS, and lumped thermal behavior as medium-fidelity/proxy model layers; this is not a full spacecraft high-fidelity thermal or power network.")
    if selected_capability_id == "whole_spacecraft.comm_payload_mission_coupled.v1":
        assumptions.append("Use the HF-6 route-B comm/payload mission-coupled model: payload data generation, onboard storage, ground-station access, downlink, and EPS load are propagated on one time grid.")
        assumptions.append("Treat RF link behavior, payload planning, and attitude pointing constraints as simplified/proxy layers; multi-station scheduling and high-fidelity RF propagation are out of scope.")
    if selected_capability_id == "whole_spacecraft.orbit_adcs_fidelity.v1":
        assumptions.append("Use the INT-1 orbit+ADCS integration gate: ORB-1 orbit fidelity and ADCS-1 fidelity share one time grid with ECI/LVLH/sun target metadata and HF-8 validation gates.")
        assumptions.append("Treat orbit-to-ADCS coupling as trace/frame/target metadata integration, not a full 6-DOF flight-grade orbit-attitude dynamics solver.")
    if selected_capability_id == "whole_spacecraft.maneuver_orbit_attitude.v1":
        assumptions.append("Use the HF-7 route-B maneuver/orbit/attitude coupled model: a finite burn depletes propellant, creates a delta-v proxy, perturbs the HF-3 orbit velocity trace, and produces a compact attitude-disturbance proxy.")
        assumptions.append("Treat propulsion and attitude coupling as medium-fidelity/proxy layers; full 6-DOF force/torque integration, slosh, plume, and valve/tank transients are out of scope.")
        assumptions.append("Run HF-8 physical validation gates for fuel monotonicity, orbit altitude sanity, energy non-negativity, and pointing convergence warnings.")
    if selected_capability_id == "whole_spacecraft.basic_power_thermal_orbit.v1":
        assumptions.append("Use the minimal power-thermal-orbit whole-spacecraft capability; ADCS/comm/propulsion remain out of scope.")
        assumptions.append("Use one shared parent time grid for orbit, thermal, and EPS child capabilities.")
        assumptions.append("Map orbit shadow into thermal/EPS, and thermal heater demand into the EPS heater load profile.")
    if selected_capability_id == "whole_spacecraft.basic_power_attitude_orbit.v1":
        assumptions.append("Use the minimal power-attitude-orbit whole-spacecraft capability; thermal/comm/propulsion remain out of scope.")
        assumptions.append("Use one shared parent time grid for orbit, ADCS, and EPS child capabilities.")
        assumptions.append("Map reaction-wheel power demand into the EPS load profile.")
    if selected_capability_id == "whole_spacecraft.basic_power_orbit.v1":
        assumptions.append("Use the minimal power-orbit whole-spacecraft capability; ADCS/thermal/comm/propulsion remain out of scope.")
        assumptions.append("Use one shared parent time grid for orbit and EPS child capabilities.")
    if selected_capability_id == "subsystem.adcs_fidelity.v1":
        assumptions.append("Use the HF-4 basic closed-loop ADCS model with quaternion attitude state, PD control, reaction-wheel proxy, and simple gyro/sensor metadata; full FSW and calibrated high-fidelity sensors remain out of scope.")
    if selected_capability_id == "subsystem.adcs.basic_rw_pointing.v1":
        assumptions.append("Use a basic single-axis reaction-wheel pointing model; advanced FSW, sensors, and magnetorquers are out of scope.")
    if selected_capability_id == "subsystem.thermal.basic_lumped.v1":
        assumptions.append("Use a basic lumped bus/battery thermal model; high-fidelity thermal networks and finite-element analysis are out of scope.")
    if selected_capability_id == "subsystem.comm.basic_ground_pass.v1":
        assumptions.append("Use a basic ground-pass communication/data model; high-fidelity RF propagation, multi-station scheduling, antenna pointing, and EPS coupling are out of scope.")
        assumptions.append("Use orbit_environment.leo_simple.v1 to provide deterministic ground-station access and slant range.")
    if selected_capability_id == "component.data_queue.v1":
        assumptions.append("Use the source-native components.data_queue.builder profile functions; no demo runner or RF link model is used.")
    if selected_capability_id == "component.onboard_storage.v1":
        assumptions.append("Use the source-native components.onboard_storage.builder accounting functions; no Basilisk message graph is generated.")
    if selected_capability_id == "subsystem.propulsion.source_native.v1":
        assumptions.append("Use the source-native propulsion subsystem profile model for propellant use and tank pressure; no maneuver planning or high-fidelity force-coupled orbit propagation is generated.")
    if missing:
        assumptions.append("Unspecified numeric inputs are filled from capability examples/defaults and recorded in generated artifacts.")
    return tuple(assumptions)


def plan_capability_for_request(
    request_text: str,
    *,
    allowed_capabilities: Sequence[str] | None = None,
    min_score: float = 1.0,
) -> CapabilityPlanResult:
    """Plan a capability route for a natural-language request."""

    allowed = tuple(allowed_capabilities) if allowed_capabilities is not None else tuple(c.capability_id for c in list_capabilities() if c.is_active and c.exposed_to_agent)
    candidates = tuple(sorted((_score_capability(cid, request_text) for cid in allowed), key=lambda c: (-c.score, c.capability_id)))
    explicit_ids = explicit_capability_ids_in_text(request_text, allowed_capabilities=allowed)
    selected = explicit_ids[0] if len(explicit_ids) == 1 else (candidates[0].capability_id if candidates and candidates[0].score >= min_score else None)
    capability_token = re.search(
        r"\bcapability(?:_id)?\s*=\s*([A-Za-z0-9_.:-]+)",
        request_text,
        flags=re.IGNORECASE,
    )
    capability_token_value = (
        capability_token.group(1).rstrip(".:")
        if capability_token
        else None
    )
    unknown_explicit_capability = (
        capability_token_value
        if capability_token_value and capability_token_value not in set(allowed)
        else None
    )
    composite_matches = _detect_composite_full_spacecraft_request(request_text)
    cross_domain_gate_matches = _detect_cross_domain_gate_request(request_text)
    composite_required_matches = tuple(dict.fromkeys((*composite_matches, *cross_domain_gate_matches)))
    if unknown_explicit_capability:
        unsupported = (
            UnsupportedRequirement(
                domain="capability_routing",
                matched_keywords=(unknown_explicit_capability,),
                message=f"Unknown or unexposed capability_id {unknown_explicit_capability!r}.",
                reason_code=str(ReasonCode.CAPABILITY_UNKNOWN),
            ),
        )
        selected = None
    elif len(explicit_ids) > 1:
        unsupported = (
            UnsupportedRequirement(
                domain="capability_routing",
                matched_keywords=explicit_ids,
                message="Multiple exact capability IDs were provided; select exactly one capability_id.",
                reason_code="MULTIPLE_EXPLICIT_CAPABILITY_IDS",
            ),
        )
        selected = None
    elif explicit_ids:
        unsupported = _detect_unsupported(request_text, selected)
        effect_token = re.search(
            r"\beffect\s*=\s*([A-Za-z0-9_.:-]+)",
            request_text,
            flags=re.IGNORECASE,
        )
        effect_token_value = effect_token.group(1).rstrip(".:") if effect_token else None
        if effect_token and selected:
            declared_effects = {
                effect.effect_id
                for effect in get_capability(selected).operator_contract.effects
            }
            if effect_token_value not in declared_effects:
                unsupported += (
                    UnsupportedRequirement(
                        domain="effect_routing",
                        matched_keywords=(str(effect_token_value),),
                        message=(
                            f"Effect {effect_token_value!r} is not declared by "
                            f"capability {selected!r}."
                        ),
                        reason_code=str(ReasonCode.UNSUPPORTED_EFFECT),
                    ),
                )
    elif composite_required_matches and "whole_spacecraft.composite_digital_twin.v1" in allowed:
        selected = "whole_spacecraft.composite_digital_twin.v1"
        unsupported = _detect_unsupported(request_text, selected)
    elif composite_required_matches:
        unsupported = (
            UnsupportedRequirement(
                domain="composite_full_spacecraft",
                matched_keywords=composite_required_matches,
                message=(
                    "COMPOSITE_CAPABILITY_REQUIRED: the active exposure profile does not include the "
                    "registered orbit+ADCS+EPS+thermal+comm+payload composite capability."
                ),
                reason_code="COMPOSITE_CAPABILITY_REQUIRED",
            ),
        ) + _detect_unsupported(request_text, None)
        selected = None
    else:
        unsupported = _detect_unsupported(request_text, selected)
    missing = _missing_parameter_policy(selected, request_text)
    selected_task_type = _task_type_for_capability(selected) if selected else None

    boundary_warnings: list[str] = []
    if unsupported:
        boundary_warnings.extend(item.message for item in unsupported)
    if selected == "whole_spacecraft.composite_digital_twin.v1":
        boundary_warnings.append(
            "Selected whole_spacecraft.composite_digital_twin.v1 runs the existing unified Basilisk graph for orbit/environment, spacecraft dynamics, ADCS, EPS, thermal, communication/data, payload, and optional propulsion. It is an engineering prototype with native and proxy couplings, not a flight-data-calibrated or certification-grade digital twin."
        )
        boundary_warnings.append(
            "mission_success_score is a deterministic gate fraction, not a calibrated mission-success probability."
        )
    if selected == "whole_spacecraft.power_thermal_orbit_coupled.v1":
        boundary_warnings.append("Selected whole_spacecraft.power_thermal_orbit_coupled.v1 covers HF-5 route-B medium-fidelity coupling: eclipse->solar/EPS, load->thermal heat, heater->EPS, and temperature derating; it is not full high-fidelity power/thermal/orbit analysis.")
    if selected == "whole_spacecraft.comm_payload_mission_coupled.v1":
        boundary_warnings.append("Selected whole_spacecraft.comm_payload_mission_coupled.v1 covers HF-6 route-B mission coupling for payload data/storage/downlink/EPS load; it is not multi-station scheduling, adaptive RF, or high-fidelity payload planning.")
    if selected == "whole_spacecraft.orbit_adcs_fidelity.v1":
        boundary_warnings.append("Selected whole_spacecraft.orbit_adcs_fidelity.v1 covers INT-1 trace-level orbit+ADCS integration with frame/target metadata and validation gates; it is not a full 6-DOF flight-grade orbit-attitude solver.")
    if selected == "whole_spacecraft.maneuver_orbit_attitude.v1":
        boundary_warnings.append("Selected whole_spacecraft.maneuver_orbit_attitude.v1 covers HF-7 finite-burn delta-v, propellant depletion, and attitude-disturbance proxies with HF-8 gates; it is not full high-fidelity 6-DOF maneuver simulation.")
    if selected == "whole_spacecraft.basic_power_thermal_orbit.v1":
        boundary_warnings.append("Selected whole_spacecraft.basic_power_thermal_orbit.v1 covers orbit/environment + basic EPS + lumped thermal coupling only.")
    if selected == "whole_spacecraft.basic_power_attitude_orbit.v1":
        boundary_warnings.append("Selected whole_spacecraft.basic_power_attitude_orbit.v1 covers orbit/environment + basic ADCS pointing + EPS power coupling only.")
    if selected == "whole_spacecraft.basic_power_orbit.v1":
        boundary_warnings.append("Selected whole_spacecraft.basic_power_orbit.v1 covers only orbit/environment + EPS power coupling.")
    if selected == "subsystem.adcs_fidelity.v1":
        boundary_warnings.append("Selected subsystem.adcs_fidelity.v1 covers ADCS engineering-prototype fidelity: environmental-torque/sensor/actuator/controller proxies, not full FSW or calibrated high-fidelity sensor physics.")
    if selected == "subsystem.adcs.basic_rw_pointing.v1":
        boundary_warnings.append("Selected subsystem.adcs.basic_rw_pointing.v1 covers only basic single-axis reaction-wheel pointing, not high-fidelity ADCS/FSW.")
    if selected == "subsystem.thermal.basic_lumped.v1":
        boundary_warnings.append("Selected subsystem.thermal.basic_lumped.v1 covers only basic lumped thermal dynamics, not high-fidelity thermal networks or full spacecraft thermal coupling.")
    if selected == "subsystem.comm.basic_ground_pass.v1":
        boundary_warnings.append("Selected subsystem.comm.basic_ground_pass.v1 covers only basic ground-pass access, compact link budget, and one-queue data backlog.")
    if selected == "component.data_queue.v1":
        boundary_warnings.append("Selected component.data_queue.v1 covers only standalone source-native queue accounting, not RF access or scheduling.")
    if selected == "component.onboard_storage.v1":
        boundary_warnings.append("Selected component.onboard_storage.v1 covers only standalone source-native onboard storage accounting, not payload/comm coupling.")
    if selected == "subsystem.propulsion.source_native.v1":
        boundary_warnings.append("Selected subsystem.propulsion.source_native.v1 covers source-native propellant-use/tank-pressure profiles, not high-fidelity maneuver planning or orbit-attitude force coupling.")

    supported = selected is not None and not unsupported
    if unsupported:
        domains = ", ".join(item.domain for item in unsupported)
        if any(item.reason_code == "COMPOSITE_CAPABILITY_REQUIRED" for item in unsupported):
            explanation = "COMPOSITE_CAPABILITY_REQUIRED: no active single whole_spacecraft capability covers the requested full subsystem set."
        else:
            explanation = f"Best route is {selected}, but the request also asks for unsupported domain(s): {domains}."
        action = "explain_boundary"
    elif not selected:
        explanation = "No allowed capability matched the request with sufficient confidence."
        action = "clarify_or_decline"
    else:
        explanation = (
            f"Selected exact capability_id {selected}."
            if explicit_ids
            else f"Selected {selected} based on highest keyword/intent score."
        )
        action = "generate_task_spec"

    return CapabilityPlanResult(
        request=request_text,
        supported=supported,
        selected_capability_id=selected,
        selected_task_type=selected_task_type,
        candidates=candidates,
        unsupported_requirements=unsupported,
        missing_parameters=missing,
        assumptions=_assumptions_for(selected, missing),
        boundary_warnings=tuple(boundary_warnings),
        allowed_capabilities=allowed,
        recommended_action=action,
        explanation=explanation,
    )


__all__ = [
    "CapabilityPlanResult",
    "CapabilityRouteCandidate",
    "MissingParameter",
    "UnsupportedRequirement",
    "plan_capability_for_request",
]
