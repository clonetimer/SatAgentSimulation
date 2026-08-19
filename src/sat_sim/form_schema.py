"""Schema-driven form contracts for capability-backed TaskSpec creation.

The form schema is a presentation contract, not a second source of truth. All
support, effect, output and default information is derived from the Capability
Registry and the canonical TaskSpec model. Submitted forms still pass through
the deterministic validators before planning or execution.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Mapping

from .capability_registry import active_capability_ids, get_capability
from .task_models import CANONICAL_TASK_SPEC_VERSION, CanonicalTaskSpec
from .workbench_catalog import workbench_presentation_catalog

FORM_SCHEMA_VERSION = "sat-sim.capability-form-schema.v2"


CORE_FIELD_METADATA: dict[str, dict[str, str]] = {
    "task.id": {
        "label": "任务标识",
        "description": "任务的机器可读唯一标识，用于生成运行目录和追踪记录。仅使用字母、数字、下划线、点或短横线。",
    },
    "task.name": {
        "label": "任务名称",
        "description": "面向用户显示的任务名称，可使用中文；运行中心将优先显示该名称。",
    },
    "simulation.duration_s": {
        "label": "仿真时长",
        "description": "本次仿真的总持续时间。所有事件起止时间都必须落在该时间范围内。",
    },
    "simulation.sample_s": {
        "label": "输出采样间隔",
        "description": "保存遥测、曲线和结果数据的时间间隔。该值必须大于或等于积分步长，且不能超过仿真时长。",
    },
    "simulation.step_s": {
        "label": "积分步长",
        "description": "动力学或离散模型内部推进的时间步长。步长越小通常越精细，但运行时间和数据量可能增加；不得大于输出采样间隔。",
    },
}

TOKEN_LABELS = {
    "initial": "初始", "final": "最终", "max": "最大", "min": "最小", "nominal": "标称",
    "effective": "有效", "num": "数量", "count": "数量", "duration": "持续时间", "start": "开始",
    "end": "结束", "period": "周期", "sample": "采样", "step": "步长", "profile": "时序配置",
    "wheel": "轮", "wheels": "轮", "reaction": "反作用", "inertia": "转动惯量", "motor": "电机",
    "torque": "力矩", "speed": "转速", "speeds": "转速", "damping": "阻尼", "friction": "摩擦",
    "battery": "电池", "capacity": "容量", "charge": "充电", "discharge": "放电", "efficiency": "效率",
    "soc": "荷电状态", "solar": "太阳阵列", "panel": "帆板", "deployment": "展开", "shadow": "阴影",
    "sun": "太阳", "vector": "矢量", "tracking": "跟踪", "slew": "转动", "rate": "速率",
    "power": "功率", "bus": "平台母线", "payload": "载荷", "adcs": "姿态控制", "comm": "通信",
    "heater": "加热器", "load": "负载", "pdu": "配电单元", "shed": "卸载", "order": "顺序",
    "thermal": "热", "temp": "温度", "temperature": "温度", "ambient": "环境", "sink": "热沉",
    "radiator": "散热器", "area": "面积", "emissivity": "发射率", "conductance": "热导",
    "heat": "热量", "internal": "内部", "node": "节点", "setpoint": "设定值", "hysteresis": "回差",
    "deadband": "死区", "generated": "生成", "downlink": "下行", "queue": "队列", "storage": "存储",
    "backlog": "积压", "raw": "原始", "tx": "发射", "rx": "接收", "gain": "增益", "loss": "损耗",
    "freq": "频率", "noise": "噪声", "slant": "斜距", "range": "距离", "required": "要求",
    "ground": "地面", "station": "站", "altitude": "高度", "inclination": "倾角", "raan": "升交点赤经",
    "arg": "参数", "perigee": "近地点", "true": "真", "anomaly": "近点角", "eccentricity": "偏心率",
    "semi": "半", "major": "长轴", "axis": "轴", "attitude": "姿态", "error": "误差",
    "gyro": "陀螺", "bias": "偏置", "std": "标准差", "star": "星敏感器", "sensor": "传感器",
    "target": "目标", "mode": "模式", "allocation": "分配", "environment": "环境", "torques": "力矩",
    "dropout": "中断", "fuel": "推进剂", "burn": "点火", "requested": "请求", "allows": "允许",
    "observation": "观测", "standby": "待机", "data": "数据", "quality": "质量", "cloud": "云量",
    "pointing": "指向", "mass": "质量", "pressure": "压力", "thrust": "推力", "specific": "比",
    "impulse": "冲量", "fraction": "比例", "enable": "启用", "enabled": "启用", "factor": "系数",
    "threshold": "阈值", "timeout": "超时", "limit": "限制", "coefficient": "系数", "constant": "常数",
}


# Output labels are presentation metadata. Technical field names remain visible
# separately so the UI can be fully Chinese without hiding the data contract.
TOKEN_LABELS.update({
    "qoi": "质量指标", "time": "时间", "index": "序号", "task": "任务", "case": "工况",
    "status": "状态", "active": "激活", "flag": "标志", "measured": "测量", "residual": "残差",
    "applied": "实际施加", "command": "指令", "angular": "角", "omega": "角速度", "momentum": "动量矩",
    "rotational": "转动", "energy": "能量", "saturation": "饱和", "dropout": "中断", "cumulative": "累计",
    "downlinked": "已下行", "dropped": "已丢弃", "stored": "已存储", "overflow": "溢出", "fill": "填充",
    "high": "高", "watermark": "水位", "margin": "裕度", "served": "已供给", "unserved": "未供给",
    "remaining": "剩余", "used": "已用", "total": "总", "mean": "平均", "delta": "变化量",
    "radius": "轨道半径", "position": "位置", "velocity": "速度", "force": "力", "central": "中心",
    "gravity": "引力", "drag": "大气阻力", "srp": "太阳光压", "j2": "J2项", "magnetic": "磁场",
    "field": "场", "norm": "模", "elevation": "仰角", "access": "可见", "view": "视场内",
    "frame": "坐标系", "aligned": "已对齐", "conservation": "守恒", "balance": "平衡", "relative": "相对",
    "removed": "移除", "basis": "依据", "structural": "结构", "physical": "物理", "numerical": "数值",
    "mission": "任务", "success": "成功", "score": "评分", "runtime": "运行时", "triggered": "已触发",
    "recovery": "恢复", "mutation": "参数修改", "execution": "执行", "count": "数量", "reference": "参考",
    "blueprint": "蓝图", "benchmark": "基准验证", "graph": "连接图", "gate": "门禁", "metadata": "元数据",
    "native": "原生", "proxy": "代理", "coupling": "耦合", "bridge": "桥接", "exercised": "已执行",
    "state": "状态", "health": "健康", "cold": "低温", "hot": "高温", "full": "已满",
    "initial": "初始", "final": "最终", "max": "最大", "min": "最小", "abs": "绝对值",
    "settled": "稳定", "transition": "切换", "duty": "占空", "fraction": "比例", "error": "误差",
    "control": "控制", "pointing": "指向", "attitude": "姿态", "rate": "角速度", "sensor": "传感器",
    "gyro": "陀螺", "reaction": "反作用", "wheel": "轮", "rw": "反作用轮", "fsw": "飞行软件",
    "environment": "环境", "power": "功率", "battery": "电池", "solar": "太阳阵列", "loads": "负载",
    "thermal": "热控", "heater": "加热器", "radiator": "散热器", "temperature": "温度", "temp": "温度",
    "comm": "通信", "data": "数据", "storage": "存储", "queue": "队列", "link": "链路", "tx": "发射机",
    "ground": "地面站", "payload": "载荷", "propulsion": "推进", "propellant": "推进剂", "thrust": "推力",
    "burn": "点火", "orbit": "轨道", "altitude": "高度", "raan": "升交点赤经", "spacecraft": "航天器",
    "label": "状态标签", "effect": "效果", "target": "目标", "requested": "请求", "effective": "有效",
    "nominal": "标称", "efficiency": "效率", "capacity": "容量", "storage": "储能", "shunt": "分流",
    "dissipated": "耗散", "generated": "已生成", "incidence": "入射", "shadow": "阴影", "deployment": "展开",
    "panel": "帆板", "array": "阵列", "normal": "法向", "vector": "矢量", "eclipse": "食",
    "umbra": "本影", "penumbra": "半影", "specific": "比", "step": "单步", "internal": "内部",
    "integral": "积分", "pid": "PID", "electrical": "电能", "heat": "热量", "reject": "散热",
    "area": "面积", "degradation": "退化", "fault": "故障", "allows": "允许", "raw": "原始",
    "ebn0": "每比特能量噪声比", "range": "距离", "speed": "速度", "torque": "力矩",
    "damping": "阻尼", "inertia": "转动惯量", "bn": "本体系B相对惯性参考系N", "b": "本体系B", "n": "惯性参考系N",
    "x": "X轴", "y": "Y轴", "z": "Z轴", "rad": "弧度", "rpm": "转每分钟", "deg": "度",
    "bps": "比特率", "bits": "比特", "w": "功率", "wh": "能量", "j": "能量", "kg": "质量",
    "m": "距离", "s": "时间", "nm": "力矩", "nms": "动量矩", "m2": "面积", "t": "磁感应强度",
    "instrument": "仪器", "off": "关闭", "feed": "供给", "failure": "失效", "performance": "性能",
    "generation": "生成", "distribution": "配电", "heating": "加热", "rejection": "排热", "or": "或",
    "throughput": "吞吐量", "surface": "表面", "contamination": "污染", "open": "开路",
    "circuit": "回路", "gimbal": "万向节", "stuck": "卡滞", "growth": "增长", "decay": "衰减",
    "leak": "泄漏", "tracking": "跟踪", "gover": "地面站", "tdrift": "时钟漂移",
    "jamming": "干扰", "erosion": "衰减", "bad": "坏", "block": "块", "sensitivity": "灵敏度",
    "pixel": "像元", "responsivity": "响应度", "channel": "通道", "trip": "跳闸",
    "contact": "触点", "resistance": "电阻", "overload": "过载", "blinding": "致盲",
    "centroid": "质心", "false": "虚假", "eclipse": "食", "runaway": "失控",
    "valve": "阀门", "closed": "关闭", "isp": "比冲", "amplifier": "放大器", "output": "输出",
})


EFFECT_LABELS_ZH: dict[str, tuple[str, str]] = {
    "rw_jamming": ("反作用轮卡滞", "指定反作用轮在事件生效期间停止响应力矩，并将对应轮速约束为零。"),
    "reaction_wheel_speed_limit": ("反作用轮达到速度限制", "运行约束事件：指定反作用轮达到或采用更低的速度上限，不表示部件发生故障。"),
    "rw_speed_saturation": ("反作用轮达到速度限制（旧标识兼容）", "旧版兼容标识；保存时会规范化为 reaction_wheel_speed_limit。"),
    "rw_motor_failure": ("反作用轮电机失效", "降低指定反作用轮的有效输出力矩，严重度为1时近似完全失效。"),
    "friction_increase_pct": ("反作用轮摩擦增大", "增大反作用轮等效阻尼，使相同力矩下的转速响应减弱。"),
    "bearing_wear_factor": ("反作用轮轴承磨损", "降低反作用轮有效力矩和最高转速，并增加等效阻尼。"),
    "gyro_bias_step": ("陀螺偏置突变", "在事件生效期间向陀螺测量叠加确定性偏置。"),
    "gyro_noise_increase": ("陀螺噪声增大", "按严重度提高陀螺测量噪声幅度。"),
    "adcs_rw_jamming": ("ADCS反作用轮卡滞", "在闭环姿态控制中使指定控制轴反作用轮卡滞。"),
    "adcs_reaction_wheel_speed_limit": ("ADCS反作用轮达到速度限制", "运行约束事件：闭环控制中的指定反作用轮达到或采用更低的速度上限，不计为故障。"),
    "adcs_rw_speed_saturation": ("ADCS反作用轮达到速度限制（旧标识兼容）", "旧版兼容标识；保存时会规范化为 adcs_reaction_wheel_speed_limit。"),
    "adcs_rw_motor_failure": ("ADCS反作用轮电机失效", "在闭环姿态控制中使指定控制轴的可用反作用轮力矩清零。"),
    "adcs_rw_torque_authority_loss": ("ADCS反作用轮力矩权限下降", "按剩余比例降低指定反作用轮的有效最大力矩；与完全电机失效分开建模。"),
    "adcs_gyro_bias_step": ("ADCS陀螺偏置突变", "在闭环控制测量链中叠加陀螺偏置。"),
    "adcs_gyro_noise_increase": ("ADCS陀螺噪声增大", "在闭环控制测量链中提高陀螺噪声幅度。"),
    "adcs_rw_friction_increase": ("ADCS反作用轮摩擦退化", "增加反作用轮等效摩擦阻尼，影响轮速与姿态控制响应。"),
}


EFFECT_LABELS_ZH.update({
    "mtb_coil_open": ("磁力矩器线圈开路", "二值故障：指定轴线圈不再产生磁矩。"),
    "mtb_communication_loss": ("磁力矩器指令通信中断", "二值故障：事件期间磁力矩器不再接收新的磁矩指令。"),
    "mtb_coil_short": ("磁力矩器线圈短路", "降低指定轴可用磁矩上限，模拟线圈短路后的输出能力下降。"),
    "mtb_dipole_capacity_loss": ("磁力矩器磁矩能力退化", "按剩余能力比例降低磁力矩器可用磁矩上限。"),
    "mtb_response_lag_increase": ("磁力矩器响应迟滞增加", "放大磁力矩器一阶响应时间常数。"),
    "radiator_rejection_loss": ("散热器排热能力丧失", "按剩余能力比例降低散热器最大排热功率。"),
    "radiator_surface_contamination": ("散热器表面污染", "降低散热器有效发射率，影响辐射排热能力。"),
    "radiator_emissivity_decay": ("散热器发射率退化", "随退化事件降低散热器有效发射率。"),
    "radiator_area_degradation": ("散热器有效面积退化", "按面积损失比例降低有效辐射面积。"),
})


EVENT_FIELD_SCHEMAS: dict[str, dict[str, Any]] = {
    "capacity_loss_pct": {
        "magnitude_applicable": False,
        "instructions": "使用容量损失百分比描述电池退化；该能力按仿真开始时的构建参数生效。",
        "parameters": [{"name": "value_pct", "label": "容量损失", "type": "number", "unit": "%", "minimum": 0.0, "maximum": 100.0, "default": 30.0, "description": "有效容量相对标称容量的损失百分比。"}],
    },
    "internal_resistance_increase_pct": {
        "magnitude_applicable": False,
        "instructions": "使用等效内阻增加百分比描述电池效率退化；该能力按仿真开始时的构建参数生效。",
        "parameters": [{"name": "value_pct", "label": "内阻增加", "type": "number", "unit": "%", "minimum": 0.0, "default": 30.0, "description": "等效内阻相对标称值的增加百分比。"}],
    },
    "efficiency_loss_pct": {
        "magnitude_applicable": False,
        "instructions": "使用效率损失百分比描述太阳阵列退化；该能力按仿真开始时的构建参数生效。",
        "parameters": [{"name": "value_pct", "label": "效率损失", "type": "number", "unit": "%", "minimum": 0.0, "maximum": 100.0, "default": 20.0, "description": "太阳阵列转换效率损失百分比。"}],
    },
    "radiation_damage_factor": {
        "magnitude_applicable": False,
        "instructions": "使用0到1的辐射损伤因子描述太阳阵列退化；0表示无损伤，1表示最大损伤。",
        "parameters": [{"name": "factor", "label": "辐射损伤因子", "type": "number", "unit": "ratio", "minimum": 0.0, "maximum": 1.0, "default": 0.2, "description": "用于降低太阳阵列有效输出的损伤因子。"}],
    },
    "pdu_efficiency_loss_pct": {
        "magnitude_applicable": False,
        "instructions": "使用效率损失百分比描述配电单元退化；该能力按仿真开始时的构建参数生效。",
        "parameters": [{"name": "value_pct", "label": "PDU效率损失", "type": "number", "unit": "%", "minimum": 0.0, "maximum": 100.0, "default": 10.0, "description": "配电效率相对标称值的损失百分比。"}],
    },
    "radiator_degradation_factor": {
        "magnitude_applicable": False,
        "instructions": "填写散热能力保留比例；1表示无退化，0表示无有效辐射散热能力。",
        "parameters": [{"name": "factor", "label": "散热能力保留比例", "type": "number", "unit": "ratio", "minimum": 0.0, "maximum": 1.0, "default": 0.8, "description": "乘到散热器辐射热流上的有效比例。"}],
    },
    "comm_tx_power_loss_pct": {
        "magnitude_applicable": False,
        "instructions": "填写发射功率损失百分比；该基础通信能力按仿真开始时的参数生效。",
        "parameters": [{"name": "value_pct", "label": "发射功率损失", "type": "number", "unit": "%", "minimum": 0.0, "maximum": 100.0, "default": 30.0, "description": "有效发射功率的损失百分比。"}],
    },
    "comm_tx_gain_loss_db": {
        "magnitude_applicable": False,
        "instructions": "填写发射天线增益损失，运行时从标称增益中扣除。",
        "parameters": [{"name": "loss_db", "label": "发射增益损失", "type": "number", "unit": "dB", "minimum": 0.0, "default": 3.0, "description": "相对标称发射增益的损失。"}],
    },
    "comm_misc_loss_increase_db": {
        "magnitude_applicable": False,
        "instructions": "填写额外链路杂散损耗，运行时叠加到标称损耗。",
        "parameters": [{"name": "increase_db", "label": "附加杂散损耗", "type": "number", "unit": "dB", "minimum": 0.0, "default": 3.0, "description": "在链路预算中增加的损耗。"}],
    },
    "comm_storage_capacity_loss_pct": {
        "magnitude_applicable": False,
        "instructions": "填写星上通信数据存储容量损失百分比。",
        "parameters": [{"name": "value_pct", "label": "存储容量损失", "type": "number", "unit": "%", "minimum": 0.0, "maximum": 100.0, "default": 25.0, "description": "有效数据存储容量相对标称值的损失百分比。"}],
    },
    "rw_jamming": {
        "magnitude_applicable": False,
        "instructions": "二值故障：事件生效即表示目标轮完全卡滞，不需要填写幅值。请选择轮号并设置时间窗。",
        "parameters": [
            {"name": "wheel_index", "label": "目标轮号", "type": "integer", "minimum": 0, "maximum": 7, "default": 0, "description": "从0开始编号；0表示1号反作用轮。"},
        ],
    },
    "adcs_rw_jamming": {
        "magnitude_applicable": False,
        "instructions": "二值故障：事件生效即表示目标轮完全卡滞。幅值字段不适用，旧任务中的幅值会被忽略。",
        "parameters": [
            {"name": "wheel_index", "label": "目标轮号", "type": "integer", "minimum": 0, "maximum": 3, "default": 0, "description": "从0开始编号；构型为三正交轮时可选0~2，四轮构型可选0~3。"},
        ],
    },
    "rw_motor_failure": {
        "magnitude_applicable": False,
        "instructions": "二值故障：事件生效即表示目标轮电机完全失效。部分力矩能力下降应建模为退化，而不是故障幅值。",
        "parameters": [{"name": "wheel_index", "label": "目标轮号", "type": "integer", "minimum": 0, "maximum": 7, "default": 0, "description": "从0开始编号。"}],
    },
    "adcs_rw_motor_failure": {
        "magnitude_applicable": False,
        "instructions": "二值故障：目标轮电机在事件时间窗内不再提供驱动力矩。",
        "parameters": [{"name": "wheel_index", "label": "目标轮号", "type": "integer", "minimum": 0, "maximum": 3, "default": 0, "description": "从0开始编号。"}],
    },
    "adcs_rw_torque_authority_loss": {
        "magnitude_applicable": False,
        "instructions": "性能退化：使用0到1的剩余力矩比例描述目标轮仍可提供的力矩权限；0表示无可用力矩，1表示标称能力。",
        "parameters": [
            {"name": "wheel_index", "label": "目标轮号", "type": "integer", "minimum": 0, "maximum": 3, "default": 0, "description": "从0开始编号。"},
            {"name": "torque_scale", "label": "剩余力矩比例", "type": "number", "unit": "ratio", "minimum": 0.0, "maximum": 1.0, "default": 0.5, "description": "事件生效期间有效最大力矩相对标称值的比例。"},
        ],
    },
    "adcs_gyro_bias_step": {
        "magnitude_applicable": False,
        "instructions": "使用偏置增量描述故障强度，不使用通用0~1幅值。可填写单值或三轴数组。",
        "parameters": [{"name": "bias_deg_s", "label": "偏置增量", "type": "vector3_or_number", "unit": "deg/s", "default": [0.1, 0.0, 0.0], "description": "叠加到陀螺测量值的偏置增量。"}],
    },
    "adcs_gyro_noise_increase": {
        "magnitude_applicable": False,
        "instructions": "使用噪声倍数描述退化程度。例如10表示噪声标准差放大到标称值的10倍。",
        "parameters": [{"name": "noise_multiplier", "label": "噪声放大倍数", "type": "number", "minimum": 1.0, "default": 10.0, "description": "必须不小于1。"}],
    },
    "adcs_rw_friction_increase": {
        "magnitude_applicable": False,
        "instructions": "使用等效摩擦力矩描述退化，不使用通用0~1幅值。",
        "parameters": [
            {"name": "wheel_index", "label": "目标轮号", "type": "integer", "minimum": 0, "maximum": 3, "default": 0, "description": "从0开始编号。"},
            {"name": "drag_torque_nm", "label": "附加等效摩擦力矩", "type": "number", "unit": "N*m", "minimum": 0.0, "default": 0.002, "description": "事件生效后施加到目标轮、方向与轮速相反的等效阻力矩。"},
        ],
    },
    "adcs_reaction_wheel_speed_limit": {
        "magnitude_applicable": False,
        "instructions": "运行约束：直接填写新的绝对转速上限，不使用故障幅值。",
        "parameters": [
            {"name": "wheel_index", "label": "目标轮号", "type": "integer", "minimum": 0, "maximum": 3, "default": 0, "description": "从0开始编号。"},
            {"name": "max_speed_rad_s", "label": "约束后的最高转速", "type": "number", "unit": "rad/s", "minimum": 0.001, "default": 300.0, "description": "事件生效期间目标轮允许的最大绝对转速。"},
        ],
    },
}

# Unit tokens are presentation metadata, not Chinese title words.  Only a
# recognized trailing combination is stripped; the symbolic unit is shown in
# the dedicated unit line/column.
_UNIT_SUFFIX_PATTERNS: tuple[tuple[str, ...], ...] = tuple(sorted({
    ("rad", "s"), ("deg", "s"), ("m", "s"), ("m", "s", "2"),
    ("kg", "m", "2"), ("n", "m"), ("n", "m", "s"), ("nm",), ("nms",),
    ("w",), ("wh",), ("j",), ("kg",), ("m",), ("s",), ("deg",), ("rad",),
    ("rpm",), ("bps",), ("bits",), ("pa",), ("hz",), ("t",), ("m2",),
}, key=len, reverse=True))

def _strip_unit_suffix(parts: list[str]) -> list[str]:
    """Remove symbolic unit tokens without translating them into title words.

    Vector fields usually end in an axis index, e.g. ``gyro_true_rad_s_1``.
    The previous implementation only recognized a unit at the absolute end and
    therefore rendered ``rad`` + ``s`` as “弧度时间”.
    """
    axis_tail: list[str] = []
    core = list(parts)
    if core and core[-1] in {"0", "1", "2", "3"}:
        axis_tail = [core.pop()]
    lowered = [part.lower() for part in core]
    for suffix in _UNIT_SUFFIX_PATTERNS:
        if len(lowered) > len(suffix) and tuple(lowered[-len(suffix):]) == suffix:
            return core[:-len(suffix)] + axis_tail
    return core + axis_tail

OUTPUT_LABEL_OVERRIDES: dict[str, str] = {
    "time_s": "仿真时间", "t_s": "仿真时间", "times": "仿真时间", "utc": "UTC时间",
    "sample_index": "采样序号", "task_id": "任务标识", "case_id": "工况标识",
    "status": "运行状态", "mode": "运行模式", "domain": "数据领域", "qoi": "质量指标",
    "duration_s": "仿真时长", "sample_s": "输出采样间隔", "fidelity_level": "模型保真度等级",
    "mission_success_score": "任务成功评分", "physical_validation_status": "物理校验状态",
    "qoi.*": "全部质量指标", "adcs.attitude.*": "姿态全部曲线", "adcs.control.*": "控制全部曲线",
    "adcs.sensor.*": "传感器全部曲线", "adcs.fsw.*": "飞行软件全部曲线", "adcs.rw.*": "反作用轮全部曲线",
    "environment.force_torque.*": "环境力与力矩全部曲线", "reactionWheelStateEffector.*": "反作用轮状态全部曲线",
    "spacecraft.scStateOutMsg.*": "航天器状态全部曲线",
    "qoi.adcs.initial_pointing_error_deg": "初始姿态指向误差",
    "qoi.adcs.final_pointing_error_deg": "最终姿态指向误差",
    "qoi.adcs.max_pointing_error_deg": "最大姿态指向误差",
    "qoi.adcs.min_pointing_error_deg": "最小姿态指向误差",
    "qoi.adcs.settled_time_s": "姿态收敛时间",
    "qoi.adcs.convergence_ratio": "姿态误差收敛比例",
    "qoi.adcs.quaternion_norm_max_error": "四元数归一化最大误差",
    "qoi.adcs.saturation_count": "执行机构饱和次数",
    "qoi.adcs.max_abs_rate_rad_s": "最大机体角速度",
    "qoi.adcs.max_abs_wheel_speed_rad_s": "最大反作用轮转速",
    "qoi.adcs.max_rw_power_w": "反作用轮最大功耗",
    "qoi.adcs.environment.total_torque_norm_nm": "环境扰动力矩合量",
    "qoi.adcs.sensor.dropout_count": "姿态传感器中断次数",
    "qoi.adcs.rw.max_abs_momentum_nms": "反作用轮最大角动量",
    "qoi.adcs.control.mode_trace_available": "控制模式记录可用",
    "adcs.attitude.q_bn_0": "姿态四元数标量分量 q₀（B相对N）",
    "adcs.attitude.q_bn_1": "姿态四元数X分量 q₁（B相对N）",
    "adcs.attitude.q_bn_2": "姿态四元数Y分量 q₂（B相对N）",
    "adcs.attitude.q_bn_3": "姿态四元数Z分量 q₃（B相对N）",
    "adcs.attitude.quaternion_norm": "姿态四元数模长",
    "adcs.attitude.quaternion_norm_error": "姿态四元数归一化误差",
    "adcs.control.wheel_configuration": "反作用轮构型",
    "adcs.control.wheel_allocation_method": "反作用轮力矩分配算法",
    "adcs.pointing.error_deg": "姿态指向误差",
    "adcs.rate.omega_bn_b_rad_s_0": "机体X轴真实角速度",
    "adcs.rate.omega_bn_b_rad_s_1": "机体Y轴真实角速度",
    "adcs.rate.omega_bn_b_rad_s_2": "机体Z轴真实角速度",
    "adcs.sensor.gyro_true_rad_s_0": "陀螺X轴真实角速度",
    "adcs.sensor.gyro_true_rad_s_1": "陀螺Y轴真实角速度",
    "adcs.sensor.gyro_true_rad_s_2": "陀螺Z轴真实角速度",
    "adcs.sensor.gyro_bias_rad_s_0": "陀螺X轴偏置",
    "adcs.sensor.gyro_bias_rad_s_1": "陀螺Y轴偏置",
    "adcs.sensor.gyro_bias_rad_s_2": "陀螺Z轴偏置",
    "adcs.rw.max_abs_speed_rad_s": "反作用轮最大绝对转速",
    "adcs.sensor.gyro_measured_rad_s_0": "陀螺X轴测量角速度",
    "adcs.sensor.gyro_measured_rad_s_1": "陀螺Y轴测量角速度",
    "adcs.sensor.gyro_measured_rad_s_2": "陀螺Z轴测量角速度",
    "adcs.sensor.gyro_noise_rad_s_0": "陀螺X轴随机噪声",
    "adcs.sensor.gyro_noise_rad_s_1": "陀螺Y轴随机噪声",
    "adcs.sensor.gyro_noise_rad_s_2": "陀螺Z轴随机噪声",
    "adcs.sensor.gyro_residual_rad_s_0": "陀螺X轴测量残差",
    "adcs.sensor.gyro_residual_rad_s_1": "陀螺Y轴测量残差",
    "adcs.sensor.gyro_residual_rad_s_2": "陀螺Z轴测量残差",
    "adcs.control.applied_torque_nm_0": "X轴实际控制力矩",
    "adcs.control.applied_torque_nm_1": "Y轴实际控制力矩",
    "adcs.control.applied_torque_nm_2": "Z轴实际控制力矩",
    "adcs.rw.speed_rad_s_0": "1号反作用轮转速",
    "adcs.rw.speed_rad_s_1": "2号反作用轮转速",
    "adcs.rw.speed_rad_s_2": "3号反作用轮转速",
    "adcs.rw.speed_rad_s_3": "4号反作用轮转速",
    "adcs.rw.max_abs_momentum_nms": "反作用轮最大角动量",
    "adcs.environment.total_torque_norm_nm": "环境扰动力矩合量",
}

PLOT_METADATA_FIELDS = {
    "time_s", "t_s", "times", "utc", "sample_index", "task_id", "case_id", "domain", "target_level",
    "target_name", "mode", "status", "qoi", "mission_status", "physical_validation_status", "physics_status",
    "structural_status", "numerical_status", "basilisk_status", "mission_success_score_basis",
}

def output_label(name: str) -> str:
    raw = str(name).strip()
    if raw in OUTPUT_LABEL_OVERRIDES:
        return OUTPUT_LABEL_OVERRIDES[raw]
    wildcard = raw.endswith(".*")
    cleaned = raw[:-2] if wildcard else raw
    parts = [part for part in re.split(r"[_\-.]+", cleaned) if part]
    parts = _strip_unit_suffix(parts)
    labels: list[str] = []
    axis = {"0": "X轴", "1": "Y轴", "2": "Z轴"}
    for part in parts:
        lower = part.lower()
        if lower in axis:
            labels.append(axis[lower])
            continue
        translated = TOKEN_LABELS.get(lower)
        if translated:
            labels.append(translated)
        elif re.fullmatch(r"\d+dof\d*", lower):
            labels.append("六自由度")
        elif lower in {"adcs", "eps", "pid", "utc", "j2", "bsk"}:
            labels.append({"adcs": "姿态控制", "eps": "能源", "pid": "PID", "utc": "UTC", "j2": "J2项", "bsk": "Basilisk"}[lower])
        # Unknown technical tokens stay in the code line, not in the Chinese title.
    label = "".join(labels) or "可观测量"
    if wildcard:
        label += "全部字段"
    return label

def _is_plottable_trace(name: str, payload: Mapping[str, Any]) -> bool:
    raw = str(name)
    leaf = raw.rsplit(".", 1)[-1]
    if raw in PLOT_METADATA_FIELDS or leaf in PLOT_METADATA_FIELDS:
        return False
    dtype = str(payload.get("dtype") or payload.get("type") or "").lower()
    if dtype in {"string", "str", "object", "mapping", "array[string]", "bool", "boolean"}:
        return False
    if raw.startswith("label."):
        return False
    if raw.startswith("bsk.") or raw.endswith("active_effects"):
        return False
    return True

UNIT_HINTS = {
    "s": "秒", "rad/s": "弧度每秒", "deg/s": "度每秒", "deg": "度", "N*m": "牛·米",
    "N*m*s": "牛·米·秒", "kg*m^2": "千克·平方米", "W": "瓦", "Wh": "瓦时", "K": "开尔文",
    "degC": "摄氏度", "bit": "比特", "bit/s": "比特每秒", "m": "米", "m^2": "平方米",
    "kg": "千克", "ratio": "0至1比例", "count": "个数", "bool": "布尔值", "Hz": "赫兹",
    "dB": "分贝", "dBi": "dBi", "J/K": "焦耳每开尔文", "W/K": "瓦每开尔文",
}

PARAMETER_LABELS = {
    "initial_attitude_error_deg": "初始姿态误差",
    "initial_rate_deg_s": "初始角速度",
    "target_mode": "姿态目标模式",
    "environment_torques": "环境扰动力矩配置",
    "gyro_bias_deg_s": "陀螺偏置",
    "gyro_noise_std_deg_s": "陀螺噪声标准差",
    "star_tracker_noise_deg": "星敏感器噪声",
    "sun_sensor_noise_deg": "太阳敏感器噪声",
    "sensor_dropout": "传感器中断窗口",
    "wheel_configuration": "反作用轮构型",
    "wheel_allocation_method": "反作用轮力矩分配算法",
    "wheel_allocation": "反作用轮旧版配置（兼容）",
    "num_wheels": "反作用轮数量",
    "wheel_inertia_kg_m2": "反作用轮转动惯量",
    "max_motor_torque_nm": "最大电机力矩",
    "max_speed_rad_s": "最大允许转速",
    "damping_nms": "黏性阻尼系数",
    "initial_wheel_speeds_rad_s": "初始轮速",
    "command_torque_nm": "指令力矩",
    "torque_profile_nm": "指令力矩时序",
}

ENUM_VALUE_LABELS: dict[str, dict[str, str]] = {
    "target_mode": {
        "inertial": "惯性定向", "nadir": "对地定向", "sun": "对日定向", "detumble": "消旋模式",
    },
    "wheel_configuration": {
        "orthogonal_3": "三正交轮构型",
        "four_skew": "四轮斜装构型（45°十字）",
        "pyramid_4": "四轮金字塔构型（54.7356°）",
    },
    "wheel_allocation_method": {
        "weighted_pseudoinverse": "加权伪逆最小范数分配",
    },
    "wheel_allocation": {
        "three_axis_plus_null_space_proxy": "旧版三轴代理（自动迁移）",
    },
}


EVENT_FIELD_SCHEMAS.update({
    "mtb_coil_open": {
        "magnitude_applicable": False,
        "instructions": "二值故障：选择发生开路的磁力矩器轴，不填写通用幅值。",
        "parameters": [{"name": "axis_index", "label": "目标轴号", "type": "integer", "minimum": 0, "maximum": 2, "default": 0, "description": "0、1、2分别对应本体系X、Y、Z轴磁力矩器。"}],
    },
    "mtb_communication_loss": {
        "magnitude_applicable": False,
        "instructions": "二值故障：事件时间窗内保持上一时刻指令或按安全策略归零。",
        "parameters": [],
    },
    "mtb_coil_short": {
        "magnitude_applicable": False,
        "instructions": "使用剩余磁矩比例描述线圈短路后的输出能力。",
        "parameters": [
            {"name": "axis_index", "label": "目标轴号", "type": "integer", "minimum": 0, "maximum": 2, "default": 0, "description": "0、1、2分别对应本体系X、Y、Z轴。"},
            {"name": "remaining_dipole_ratio", "label": "剩余磁矩比例", "type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.3, "unit": "ratio", "description": "短路后仍可输出的磁矩比例；0表示完全无输出，1表示无损失。"},
        ],
    },
    "mtb_dipole_capacity_loss": {
        "magnitude_applicable": False,
        "instructions": "使用剩余能力比例描述磁矩输出能力退化。",
        "parameters": [{"name": "remaining_capacity_ratio", "label": "剩余磁矩能力比例", "type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.7, "unit": "ratio", "description": "1表示无退化，0表示完全丧失磁矩能力。"}],
    },
    "mtb_response_lag_increase": {
        "magnitude_applicable": False,
        "instructions": "通过时间常数放大倍数描述响应变慢程度。",
        "parameters": [{"name": "lag_multiplier", "label": "响应时间常数倍数", "type": "number", "minimum": 1.0, "maximum": 100.0, "default": 2.0, "unit": "ratio", "description": "1表示无退化，数值越大响应越慢。"}],
    },
    "radiator_rejection_loss": {
        "magnitude_applicable": False,
        "instructions": "通过剩余排热能力比例描述散热器能力丧失。",
        "parameters": [{"name": "remaining_capacity_ratio", "label": "剩余排热能力比例", "type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.5, "unit": "ratio", "description": "1表示标称能力，0表示完全失去排热能力。"}],
    },
    "radiator_surface_contamination": {
        "magnitude_applicable": False,
        "instructions": "通过发射率损失比例描述表面污染。",
        "parameters": [{"name": "emissivity_loss_ratio", "label": "发射率损失比例", "type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.2, "unit": "ratio", "description": "0表示无损失，1表示有效发射率降为零。"}],
    },
    "radiator_emissivity_decay": {
        "magnitude_applicable": False,
        "instructions": "通过发射率损失比例描述渐进退化。",
        "parameters": [{"name": "emissivity_loss_ratio", "label": "发射率损失比例", "type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.1, "unit": "ratio", "description": "0表示无退化，1表示有效发射率降为零。"}],
    },
    "radiator_area_degradation": {
        "magnitude_applicable": False,
        "instructions": "通过有效面积损失比例描述散热器可用面积退化。",
        "parameters": [{"name": "area_loss_ratio", "label": "有效面积损失比例", "type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.2, "unit": "ratio", "description": "0表示面积无损失，1表示有效面积降为零。"}],
    },
})


COMMON_PARAMETER_DESCRIPTIONS = {
    "initial_attitude_error_deg": "仿真开始时航天器相对目标姿态的角度误差。数值越大，控制器需要更长时间或更大力矩完成收敛。",
    "initial_rate_deg_s": "仿真开始时机体三轴角速度，可填写单个数值或三元素数组 [x, y, z]。",
    "target_mode": "姿态控制目标模式：惯性定向、对地定向、对日定向或消旋。",
    "gyro_bias_deg_s": "陀螺三轴固定偏置，可填写单值或三元素数组；该偏置会叠加到真实角速度测量上。",
    "gyro_noise_std_deg_s": "陀螺测量噪声标准差。默认值较小，建议结合“陀螺噪声/残差”曲线观察。",
    "star_tracker_noise_deg": "星敏感器姿态测量噪声幅度，用于传感器代理模型。",
    "sun_sensor_noise_deg": "太阳敏感器测量噪声幅度，用于对日矢量测量代理。",
    "wheel_configuration": "反作用轮安装构型。三正交轮使用3个轮；四轮斜装和四轮金字塔使用4个轮，并通过显式体坐标系轴矩阵参与力矩分配。",
    "wheel_allocation_method": "由期望本体控制力矩求解各轮电机力矩的算法。当前实现为加权伪逆最小范数分配。",
    "wheel_allocation": "旧版兼容字段；新任务请使用反作用轮构型和力矩分配算法。",
    "environment_torques": "环境扰动力矩配置对象，可启用重力梯度、磁、气动和太阳光压等代理项。高级用户可用 JSON 编辑。",
    "sensor_dropout": "传感器中断窗口配置对象，例如 {\"start_s\": 20, \"duration_s\": 5}。高级用户可用 JSON 编辑。",
    "num_wheels": "反作用轮数量。标量参数会复制到所有轮，也可对各轮分别填写数组参数。",
    "wheel_inertia_kg_m2": "单个反作用轮转动惯量；可填写一个数值应用到全部轮，或填写与轮数一致的数组。",
    "max_motor_torque_nm": "反作用轮电机可输出的最大绝对力矩；可填单值或逐轮数组。",
    "max_speed_rad_s": "反作用轮允许的最大绝对转速；超过限制时会触发饱和。",
    "damping_nms": "反作用轮黏性阻尼系数；可填单值或逐轮数组。",
    "initial_wheel_speeds_rad_s": "各反作用轮的初始角速度，单位为 rad/s；可填单值或与轮数一致的数组。",
    "command_torque_nm": "施加到反作用轮的指令力矩；正负号决定加速方向，可填单值或逐轮数组。",
    "torque_profile_nm": "按采样步给出的力矩时序。填写 JSON 数组，例如 [0.01, 0.02, 0]；多轮时每个元素也可为数组。留空表示使用固定指令力矩。",
}

ADVANCED_TOKENS = {
    "profile", "matrix", "coefficient", "conductance", "emissivity", "inertia", "noise", "bias",
    "allocation", "environment", "dropout", "raan", "perigee", "anomaly", "eccentricity", "solver",
    "hysteresis", "deadband", "tracking", "specific", "impulse", "registry", "override",
}

SUBSYSTEM_PARAMETER_GROUPS: dict[str, tuple[dict[str, Any], ...]] = {
    "subsystem.adcs_fidelity.v1": (
        {"id": "spacecraft", "title": "航天器本体与初始状态", "description": "惯量、初始姿态、初始角速度和目标姿态。", "prefixes": ("inertia_", "spacecraft_", "initial_attitude", "initial_rate", "initial_quaternion", "initial_error", "target_quaternion", "target_reference", "target_mode")},
        {"id": "controller", "title": "姿态控制器", "description": "控制增益、指向要求和控制模式相关参数。", "prefixes": ("control_", "pointing_requirement")},
        {"id": "reaction_wheel", "title": "反作用轮组件", "description": "轮组数量、惯量、力矩/转速限制、初始轮速、功耗和分配策略。", "prefixes": ("num_reaction", "reaction_wheel", "wheel_", "max_wheel", "max_rw", "rw_", "initial_wheel")},
        {"id": "gyro", "title": "惯性测量单元 / 陀螺", "description": "陀螺偏置、测量噪声及传感器中断窗口。", "prefixes": ("gyro_", "sensor_dropout")},
        {"id": "star_tracker", "title": "星敏感器", "description": "星敏感器可用状态与测量噪声。", "prefixes": ("star_tracker",)},
        {"id": "sun_sensor", "title": "太阳敏感器", "description": "太阳敏感器可用状态与测量噪声。", "prefixes": ("sun_sensor",)},
        {"id": "environment", "title": "环境扰动力矩", "description": "重力梯度、磁、气动、太阳光压及外部扰动力矩配置。", "prefixes": ("environment_", "disturbance_", "external_disturbance")},
    ),
    "subsystem.eps.basic.v1": (
        {"id": "battery", "title": "蓄电池组件", "description": "容量、初始荷电状态和温度约束。", "prefixes": ("battery_", "initial_soc")},
        {"id": "solar_panel", "title": "太阳电池阵组件", "description": "阵列功率、效率、展开、入射方向、跟踪和阴影/食配置。", "prefixes": ("solar_", "sun_vector", "shadow_", "eclipse_")},
        {"id": "loads", "title": "功率负载组件", "description": "平台、载荷、姿控、通信和热控负载及负载时序。", "prefixes": ("bus_load", "payload_load", "adcs_load", "comm_load", "heater_load", "load_profile")},
        {"id": "pdu", "title": "配电单元组件", "description": "母线限值、配电效率和负载卸载策略。", "prefixes": ("pdu_", "enable_load_shedding", "shed_order", "payload_min_soc", "comm_min_soc", "heater_min_soc", "adcs_min_soc")},
    ),
    "subsystem.eps.source_native.v1": (
        {"id": "battery", "title": "蓄电池组件", "description": "电池容量和初始荷电状态。", "prefixes": ("battery_", "initial_soc")},
        {"id": "solar_panel", "title": "太阳电池阵组件", "description": "发电功率和阴影系数。", "prefixes": ("solar_", "shadow_")},
        {"id": "loads", "title": "功率负载组件", "description": "平台及各分系统功率负载。", "prefixes": ("bus_power", "payload_power", "comm_power", "adcs_power", "thermal_power")},
        {"id": "pdu", "title": "配电单元组件", "description": "配电母线最大功率。", "prefixes": ("pdu_",)},
    ),
    "subsystem.thermal.basic_lumped.v1": (
        {"id": "thermal_node", "title": "热节点组件", "description": "平台和电池节点的初始温度、热容、内热源和耦合热导。", "prefixes": ("initial_bus", "initial_battery", "bus_thermal", "battery_thermal", "internal_power", "battery_internal", "thermal_conductance")},
        {"id": "heater", "title": "加热器组件", "description": "加热功率、设定温度和回差。", "prefixes": ("heater_",)},
        {"id": "radiator", "title": "散热器组件", "description": "散热面积、发射率、退化和热沉温度。", "prefixes": ("radiator_", "sink_temp")},
        {"id": "environment", "title": "轨道热环境", "description": "太阳热输入、阴影和食段时序。", "prefixes": ("solar_heat", "shadow_", "eclipse_")},
        {"id": "limits", "title": "温度限值", "description": "平台和电池节点的冷热阈值。", "prefixes": ("bus_min", "bus_max", "battery_min", "battery_max")},
    ),
    "subsystem.thermal.source_native.v1": (
        {"id": "thermal_node", "title": "热节点组件", "description": "电池初始温度和分系统发热。", "prefixes": ("initial_battery", "payload_power", "eps_power")},
        {"id": "heater", "title": "加热器组件", "description": "加热器启用状态。", "prefixes": ("heater_",)},
        {"id": "environment", "title": "轨道热环境", "description": "阴影系数。", "prefixes": ("shadow_",)},
    ),
    "subsystem.comm.basic_ground_pass.v1": (
        {"id": "data", "title": "数据队列与星上存储", "description": "数据生成、初始积压、存储容量和下行请求。", "prefixes": ("generated_", "initial_backlog", "storage_capacity", "downlink_requested")},
        {"id": "transmitter", "title": "发射机组件", "description": "发射功率、待机功耗、原始码率和最大下行码率。", "prefixes": ("tx_power", "transmitter_", "raw_rate", "max_downlink")},
        {"id": "link", "title": "天线与链路预算", "description": "收发增益、链路损耗、频率、噪声温度和 Eb/N0 要求。", "prefixes": ("tx_gain", "rx_gain", "misc_loss", "freq_", "noise_temp", "downlink_eff", "required_ebn0")},
        {"id": "ground_station", "title": "地面站与轨道可见性", "description": "地面站配置及轨道高度、倾角和初始相位。", "prefixes": ("ground_station", "altitude_", "inclination_", "raan_", "arg_lat")},
        {"id": "eps", "title": "电源许可接口", "description": "电源分系统对下行任务的许可时序。", "prefixes": ("eps_allows",)},
    ),
    "subsystem.comm_data.source_native.v1": (
        {"id": "data", "title": "数据队列与星上存储", "description": "队列容量、初始积压和数据生成速率。", "prefixes": ("queue_", "initial_queue", "generated_")},
        {"id": "transmitter", "title": "发射机与下行", "description": "最大下行码率和可见性覆盖。", "prefixes": ("max_downlink", "access_override")},
        {"id": "eps", "title": "电源许可接口", "description": "电源分系统对下行任务的许可。", "prefixes": ("eps_allows",)},
    ),
    "subsystem.propulsion.source_native.v1": (
        {"id": "fuel_tank", "title": "燃料贮箱组件", "description": "推进剂容量和初始推进剂质量。", "prefixes": ("fuel_", "initial_fuel")},
        {"id": "thruster", "title": "推力器组件", "description": "点火请求和执行条件。", "prefixes": ("burn_requested",)},
        {"id": "eps", "title": "电源许可接口", "description": "点火所需最低 SOC、当前 SOC 和电源许可。", "prefixes": ("min_soc", "battery_soc", "eps_allows")},
    ),
    "subsystem.payload.source_native.v1": (
        {"id": "payload", "title": "载荷仪器组件", "description": "观测/待机功率和数据生成速率。", "prefixes": ("observation_power", "standby_power", "data_rate")},
        {"id": "pointing", "title": "姿态指向接口", "description": "载荷允许的最大指向误差和当前指向误差。", "prefixes": ("max_pointing", "pointing_error")},
        {"id": "permissions", "title": "电源与热控许可接口", "description": "电源和热控分系统对载荷工作的许可。", "prefixes": ("eps_allows", "thermal_allows")},
    ),
}

ADCS_COMMON_PARAMETERS = {
    "initial_attitude_error_deg", "initial_rate_deg_s", "target_mode", "gyro_bias_deg_s",
    "gyro_noise_std_deg_s", "star_tracker_noise_deg", "sun_sensor_noise_deg",
}


def _mapping(value: Any) -> dict[str, Any]:
    return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _canonical_level(level: str) -> str:
    value = str(level or "").strip().lower()
    return {"orbit": "orbit_environment", "environment": "orbit_environment", "integrated": "orbit_environment"}.get(value, value)


def _widget(field_type: str, payload: Mapping[str, Any]) -> str:
    explicit = payload.get("widget")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    if payload.get("enum"):
        return "select"
    normalized = field_type.lower()
    if normalized in {"boolean", "bool"}:
        return "checkbox"
    if normalized == "number":
        return "number"
    if normalized == "integer":
        return "integer"
    if normalized in {"number_or_array[number]", "vector3_or_number"}:
        return normalized
    if normalized.startswith("array") or normalized == "array":
        return "array"
    if normalized == "object":
        return "object_editor"
    return "text"


def _human_label(name: str) -> str:
    raw = str(name).strip()
    parts = [part for part in re.split(r"[_\-.]+", raw) if part]
    translated = [TOKEN_LABELS.get(part.lower(), part) for part in parts]
    label = "".join(translated)
    return label or raw


def _fallback_description(name: str, payload: Mapping[str, Any], label: str) -> str:
    if name in COMMON_PARAMETER_DESCRIPTIONS:
        return COMMON_PARAMETER_DESCRIPTIONS[name]
    field_type = str(payload.get("type") or "string")
    unit = str(payload.get("unit") or "").strip()
    type_hint = {
        "number": "数值参数",
        "integer": "整数参数",
        "boolean": "开关参数",
        "bool": "开关参数",
        "string": "文本或枚举参数",
        "object": "结构化配置对象",
        "array[number]": "数值数组",
        "array[bool]": "布尔数组",
        "array[string]": "文本数组",
        "array[array[number]]": "二维数值数组",
        "number_or_array[number]": "单个数值或逐对象数值数组",
        "vector3_or_number": "单个数值或三轴向量",
        "array[number_or_array[number]]": "按时间排列的数值或数组序列",
    }.get(field_type, "配置参数")
    text = f"{label}，属于{type_hint}。"
    if unit:
        text += f"单位为 {unit}（{UNIT_HINTS.get(unit, '以字段单位为准')}）。"
    if "default" in payload:
        text += f"默认值为 {payload.get('default')!r}。"
    bounds: list[str] = []
    if payload.get("min") is not None:
        bounds.append(f"不小于 {payload.get('min')}")
    if payload.get("max") is not None:
        bounds.append(f"不大于 {payload.get('max')}")
    if bounds:
        text += "取值应" + "且".join(bounds) + "。"
    if field_type.startswith("array") or "array" in field_type or field_type == "object":
        text += "页面支持 JSON 格式；简单一维数组也可使用逗号分隔输入。"
    return text


def _field_importance(capability_id: str, name: str) -> str:
    if capability_id == "subsystem.adcs_fidelity.v1":
        return "common" if name in ADCS_COMMON_PARAMETERS else "advanced"
    tokens = set(name.lower().split("_"))
    return "advanced" if tokens & ADVANCED_TOKENS else "common"


def _field(
    path: str,
    payload: Mapping[str, Any],
    *,
    label: str | None = None,
    description: str | None = None,
    importance: str = "common",
) -> dict[str, Any]:
    field_type = str(payload.get("type") or "string")
    metadata = CORE_FIELD_METADATA.get(path, {})
    field_name = path.rsplit(".", 1)[-1]
    resolved_label = label or metadata.get("label") or _human_label(field_name)
    resolved_description = description or str(payload.get("description") or metadata.get("description") or "").strip()
    if not resolved_description:
        resolved_description = _fallback_description(field_name, payload, resolved_label)
    out: dict[str, Any] = {
        "path": path,
        "label": resolved_label,
        "type": field_type,
        "widget": _widget(field_type, payload),
        "required": bool(payload.get("required", False)),
        "description": resolved_description,
        "importance": importance,
        "input_examples": [],
    }
    for source, target in (
        ("default", "default"), ("unit", "unit"), ("min", "minimum"), ("max", "maximum"), ("enum", "enum"),
        ("enum_labels", "enum_labels"), ("editor_schema", "editor_schema"),
        ("fallback_path", "fallback_path"), ("profile_kind", "profile_kind"),
    ):
        if source in payload:
            out[target] = copy.deepcopy(payload[source])
    if out["widget"] in {"array", "number_or_array[number]", "vector3_or_number"}:
        out["input_examples"] = ["1.0", "[1.0, 2.0, 3.0]"]
    if out["widget"] == "object_editor":
        out["input_examples"] = ["{}", '{"start_s": 20, "duration_s": 5}']
    if out.get("enum") and not out.get("enum_labels"):
        out["enum_labels"] = {str(value): ENUM_VALUE_LABELS.get(field_name, {}).get(str(value), str(value)) for value in out["enum"]}
    return out


def _parameter_group(capability_id: str, name: str) -> dict[str, str]:
    for group in SUBSYSTEM_PARAMETER_GROUPS.get(capability_id, ()): 
        if any(str(name).startswith(prefix) for prefix in group.get("prefixes", ())):
            return {"group_id": str(group["id"]), "group_title": str(group["title"]), "group_description": str(group.get("description") or "")}
    return {"group_id": "other", "group_title": "其他模型参数", "group_description": "当前能力合同支持的其他参数。"}


def _parameter_fields(contract: Any) -> list[dict[str, Any]]:
    capability_id = contract.capability_id
    fields: list[dict[str, Any]] = []
    for name, payload in _mapping(contract.data.get("parameters")).items():
        if not isinstance(payload, Mapping):
            continue
        field = _field(
            f"parameters.values.{name}",
            payload,
            label=PARAMETER_LABELS.get(str(name), _human_label(str(name))),
            importance=_field_importance(capability_id, str(name)),
        )
        field.update(_parameter_group(capability_id, str(name)))
        fields.append(field)
    return fields


def _effect_templates(contract: Any, kind: str) -> list[dict[str, Any]]:
    templates: list[dict[str, Any]] = []
    for effect in contract.operator_contract.effects:
        if effect.kind != kind:
            continue
        label, detail = EFFECT_LABELS_ZH.get(
            effect.effect_id,
            (output_label(effect.effect_id), f"作用对象：{effect.owner}；运行时验证方式：{effect.verification}。"),
        )
        event_schema = copy.deepcopy(EVENT_FIELD_SCHEMAS.get(effect.effect_id, {}))
        parameter_fields = list(event_schema.get("parameters") or [])
        parameter_defaults = {
            str(field["name"]): copy.deepcopy(field.get("default"))
            for field in parameter_fields if field.get("name") and "default" in field
        }
        templates.append({
            "effect": effect.effect_id,
            "label": label,
            "kind": effect.kind,
            "owner": effect.owner,
            "implementation": effect.implementation,
            "evidence_fields": list(effect.evidence_fields),
            "description": detail,
            "instructions": str(event_schema.get("instructions") or detail),
            "magnitude_applicable": bool(event_schema.get("magnitude_applicable", True)),
            "parameter_fields": parameter_fields,
            "default_event": {
                "id": f"{effect.effect_id}_1", "event_type": kind, "target": effect.owner,
                "effect": effect.effect_id, "label": label, "start_s": 0.0, "implementation": "auto",
                "delivery": "runtime_constraint" if kind == "constraint" else "modifier", "parameters": parameter_defaults,
                **({"magnitude": copy.deepcopy(event_schema.get("default_magnitude", 1.0 if kind == "fault" else 0.2))}
                   if bool(event_schema.get("magnitude_applicable", True)) and kind in {"fault", "degradation"} else {}),
            },
        })
    return templates


def _default_qoi(contract: Any) -> list[str]:
    observability = contract.operator_contract.observability
    preferred = [
        "mission_success_score", "energy_conservation_status", "data_conservation_status",
        "proxy_coupling_runtime_status",
    ]
    available = set(observability.qoi)
    selected = [name for name in preferred if name in available]
    return selected or list(observability.qoi[:8])


def _output_description(name: str, unit: str | None, *, destination: str) -> str:
    lower = name.lower()
    known = {
        "time_s": "仿真时间轴。",
        "adcs.pointing.error_deg": "当前姿态相对目标姿态的最短旋转角误差。",
        "adcs.sensor.gyro_measured_rad_s_0": "陀螺 X 轴测量值，包含真实角速度、固定偏置和配置的测量噪声。",
        "adcs.sensor.gyro_measured_rad_s_1": "陀螺 Y 轴测量值，包含真实角速度、固定偏置和配置的测量噪声。",
        "adcs.sensor.gyro_measured_rad_s_2": "陀螺 Z 轴测量值，包含真实角速度、固定偏置和配置的测量噪声。",
        "adcs.sensor.gyro_noise_rad_s_0": "陀螺 X 轴本次采样叠加的确定性噪声分量。",
        "adcs.sensor.gyro_noise_rad_s_1": "陀螺 Y 轴本次采样叠加的确定性噪声分量。",
        "adcs.sensor.gyro_noise_rad_s_2": "陀螺 Z 轴本次采样叠加的确定性噪声分量。",
    }
    if lower in known:
        text = known[lower]
    elif "pointing" in lower and "error" in lower:
        text = "姿态或指向误差，用于判断控制收敛程度。"
    elif "soc" in lower:
        text = "电池荷电状态，通常范围为 0 至 1。"
    elif "speed" in lower:
        text = "转速时序或转速统计量。"
    elif "torque" in lower:
        text = "控制、扰动或执行机构力矩。"
    elif "temperature" in lower or "temp" in lower:
        text = "温度时序或温度统计量。"
    elif "power" in lower:
        text = "功率时序或功率统计量。"
    elif "data" in lower or "queue" in lower or "storage" in lower:
        text = "数据生成、积压、存储或下行相关量。"
    elif name.endswith(".*"):
        text = "通配输出：匹配该命名空间下的全部可观测字段。"
    else:
        text = f"{output_label(name)}，用于{destination}。"
    if unit:
        text += f"单位：{unit}。"
    return text


OUTPUT_GROUPS: tuple[tuple[str, str, str], ...] = (
    ("attitude.", "adcs_attitude", "姿态状态"),
    ("control.", "adcs_control", "姿态控制器"),
    ("rw.", "adcs_rw", "反作用轮"),
    ("adcs.attitude.", "adcs_attitude", "姿态状态"),
    ("adcs.pointing.", "adcs_pointing", "姿态指向"),
    ("adcs.rate.", "adcs_rate", "机体角速度"),
    ("adcs.sensor.", "adcs_sensor", "姿态传感器"),
    ("adcs.control.", "adcs_control", "姿态控制器"),
    ("adcs.rw.", "adcs_rw", "反作用轮"),
    ("adcs.power.", "adcs_power", "姿态控制功耗"),
    ("adcs.environment.", "adcs_environment", "姿态环境扰动"),
    ("adcs.cmg.", "adcs_cmg", "控制力矩陀螺"),
    ("adcs.imu.", "adcs_imu", "惯性测量单元"),
    ("adcs.magnetometer.", "adcs_magnetometer", "磁强计"),
    ("adcs.mtb.", "adcs_mtb", "磁力矩器"),
    ("adcs.reaction_wheel.", "adcs_rw", "反作用轮"),
    ("adcs.star_tracker.", "adcs_star_tracker", "星敏感器"),
    ("adcs.sun_sensor.", "adcs_sun_sensor", "太阳敏感器"),
    ("adcs.event.", "adcs_event", "姿态事件"),
    ("adcs.pointing_error", "adcs_pointing", "姿态指向"),
    ("adcs.angular_rate", "adcs_rate", "机体角速度"),
    ("adcs.disturbance", "adcs_environment", "姿态环境扰动"),
    ("adcs.", "adcs_state", "姿态控制综合"),
    ("eps.battery.", "eps_battery", "电池"),
    ("eps.solar.", "eps_solar", "太阳阵列"),
    ("eps.loads.", "eps_loads", "用电负载"),
    ("eps.pdu.", "eps_pdu", "配电单元"),
    ("eps.power.", "eps_power", "能源平衡"),
    ("eps.source_native.", "eps_native", "能源分系统综合"),
    ("eps.", "eps_native", "能源分系统综合"),
    ("thermal.node.", "thermal_nodes", "热节点"),
    ("thermal.heat.", "thermal_heat", "热流与热源"),
    ("thermal.heater.", "thermal_heater", "加热器"),
    ("thermal.radiator.", "thermal_radiator", "散热器"),
    ("thermal.coupling.", "thermal_coupling", "热耦合"),
    ("thermal.source_native.", "thermal_native", "热控分系统综合"),
    ("thermal.control.", "thermal_control", "热控控制器"),
    ("thermal.energy_balance.", "thermal_balance", "热平衡"),
    ("thermal.", "thermal_native", "热控分系统综合"),
    ("ground.", "comm_ground", "地面站可见性"),
    ("comm.access.", "comm_access", "地面站可见性"),
    ("comm.link.", "comm_link", "通信链路"),
    ("comm.transmitter.", "comm_tx", "发射机"),
    ("comm.tx.", "comm_tx", "发射机"),
    ("comm.downlink", "comm_downlink", "下行链路"),
    ("comm.power", "comm_power", "通信功耗"),
    ("comm.data.", "comm_data", "通信数据队列"),
    ("comm.data_queue.", "comm_data_queue", "数据队列"),
    ("comm.storage.", "comm_storage", "星上存储"),
    ("comm.", "comm_native", "通信分系统综合"),
    ("comm_data.", "comm_native", "通信数据分系统综合"),
    ("storage.", "comm_storage", "数据存储"),
    ("data.", "comm_storage", "数据存储"),
    ("coupled.data.", "coupled_data", "数据耦合校核"),
    ("payload.", "payload", "载荷"),
    ("comm_data.source_native.", "comm_native", "通信数据分系统综合"),
    ("payload.source_native.", "payload", "载荷"),
    ("propulsion.source_native.", "propulsion", "推进分系统"),
    ("propulsion.", "propulsion", "推进与点火"),
    ("orbit.", "orbit", "轨道状态"),
    ("environment.", "environment", "空间环境"),
    ("spacecraft.attitude.", "whole_adcs", "整星姿态"),
    ("spacecraft.power.", "whole_eps", "整星能源"),
    ("spacecraft.thermal.", "whole_thermal", "整星热控"),
    ("spacecraft.", "whole_spacecraft", "整星综合"),
    ("coupled.", "coupled", "跨分系统耦合"),
    ("int1.", "coupled", "跨分系统耦合"),
    ("event.", "runtime_event", "运行事件"),
    ("battery_", "whole_eps", "整星能源"),
    ("net_power_", "whole_eps", "整星能源"),
    ("data_", "whole_data", "整星数据"),
    ("thermal_", "whole_thermal", "整星热控"),
    ("attitude_", "whole_adcs", "整星姿态"),
    ("adcs_control_", "whole_adcs", "整星姿态"),
    ("rate_error_", "whole_adcs", "整星姿态"),
    ("payload_", "whole_payload", "整星载荷"),
    ("downlink_", "whole_comm", "整星通信"),
    ("propellant_", "whole_propulsion", "整星推进"),
    ("tank_", "whole_propulsion", "整星推进"),
    ("orbit_", "orbit", "轨道状态"),
    ("eclipse_", "environment", "空间环境"),
    ("cumulative_", "whole_data", "整星数据"),
    ("instrument_", "whole_payload", "整星载荷"),
    ("ground_", "whole_comm", "整星通信"),
    ("rf_", "whole_comm", "整星通信"),
    ("transmitter_", "whole_comm", "整星通信"),
    ("heater_", "whole_thermal", "整星热控"),
    ("pdu_", "whole_eps", "整星能源"),
    ("power_", "whole_eps", "整星能源"),
    ("shunt_", "whole_eps", "整星能源"),
    ("fuel_", "whole_propulsion", "整星推进"),
    ("propulsion_", "whole_propulsion", "整星推进"),
)

def output_group(name: str) -> tuple[str, str]:
    raw = str(name)
    for prefix, group_id, label in OUTPUT_GROUPS:
        if raw.startswith(prefix):
            return group_id, label
    return "other", "其他可观测量"


_COMPONENT_OUTPUT_GROUPS = {
    "adcs_sensor", "adcs_rw", "adcs_cmg", "adcs_imu", "adcs_magnetometer", "adcs_mtb",
    "adcs_star_tracker", "adcs_sun_sensor",
    "eps_battery", "eps_solar", "eps_loads", "eps_pdu",
    "thermal_nodes", "thermal_heater", "thermal_radiator",
    "comm_ground", "comm_link", "comm_tx", "comm_storage", "comm_data_queue",
}

_WHOLE_SPACECRAFT_OUTPUT_GROUPS = {
    "orbit", "environment", "coupled_data",
    "whole_eps", "whole_data", "whole_thermal", "whole_adcs",
    "whole_payload", "whole_comm", "whole_propulsion", "whole_spacecraft",
}

_OUTPUT_SUBSYSTEM_PREFIXES: tuple[tuple[str, str, str], ...] = (
    ("attitude", "adcs", "姿态控制分系统"),
    ("control", "adcs", "姿态控制分系统"),
    ("rw", "adcs", "姿态控制分系统"),
    ("adcs", "adcs", "姿态控制分系统"),
    ("eps", "eps", "能源分系统"),
    ("thermal", "thermal", "热控分系统"),
    ("comm_data", "comm_data", "通信与数据分系统"),
    ("comm", "comm_data", "通信与数据分系统"),
    ("ground", "comm_data", "通信与数据分系统"),
    ("storage", "comm_data", "通信与数据分系统"),
    ("coupled_data", "comm_data", "通信与数据分系统"),
    ("coupled", "coupled", "跨分系统耦合"),
    ("payload", "payload", "载荷分系统"),
    ("propulsion", "propulsion", "推进分系统"),
)

_TRACE_PATTERN_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "adcs.body_rate_rad_s_*": tuple(f"adcs.body_rate_rad_s_{axis}" for axis in "xyz"),
    "adcs.rw.speed_rad_s_*": tuple(f"adcs.rw.speed_rad_s_{index}" for index in range(3)),
    "adcs.star_tracker.q*": tuple(f"adcs.star_tracker.q{index}" for index in range(4)),
    "adcs.magnetometer.tesla_*": tuple(f"adcs.magnetometer.tesla_{axis}" for axis in "xyz"),
    "adcs.sensor.gyro_measured_rad_s_*": tuple(f"adcs.sensor.gyro_measured_rad_s_{axis}" for axis in "xyz"),
    "adcs.sensor.gyro_bias_rad_s_*": tuple(f"adcs.sensor.gyro_bias_rad_s_{axis}" for axis in "xyz"),
    "adcs.sensor.gyro_noise_rad_s_*": tuple(f"adcs.sensor.gyro_noise_rad_s_{axis}" for axis in "xyz"),
    "adcs.rw.command_torque_nm_*": tuple(f"adcs.rw.command_torque_nm_{index}" for index in range(3)),
    "adcs.control.applied_torque_nm_*": tuple(f"adcs.control.applied_torque_nm_{index}" for index in range(3)),
    "adcs.rw.effective_drag_nms_*": tuple(f"adcs.rw.effective_drag_nms_{index}" for index in range(3)),
    "adcs.rw.effective_max_torque_nm_*": tuple(f"adcs.rw.effective_max_torque_nm_{index}" for index in range(3)),
    "adcs.rw.effective_torque_ratio_*": tuple(f"adcs.rw.effective_torque_ratio_{index}" for index in range(3)),
    "adcs.rw.effective_max_speed_rad_s_*": tuple(f"adcs.rw.effective_max_speed_rad_s_{index}" for index in range(3)),
    "propulsion.thrust_force_n_*": ("propulsion.thrust_force_n_0", "propulsion.thrust_force_n_1"),
    "thermal.*_temp_k": tuple(
        f"thermal.{node}_temp_k"
        for node in ("battery", "electronics", "payload", "adcs", "structure", "solar_panel", "comm", "propulsion")
    ),
    "thermal.*_safe": tuple(
        f"thermal.{node}_safe"
        for node in ("battery", "electronics", "payload", "adcs", "structure", "solar_panel", "comm", "propulsion")
    ),
    "thermal.*_heat_input_w": tuple(
        f"thermal.{node}_heat_input_w"
        for node in ("battery", "electronics", "payload", "adcs", "structure", "solar_panel", "comm", "propulsion")
    ),
}


def _explicit_output_rows(contract: Any, key: str) -> list[Any]:
    outputs = _mapping(contract.data.get("outputs"))
    aliases = (key, "trace_fields") if key == "trace" else (key, "qoi")
    for alias in aliases:
        value = outputs.get(alias)
        if isinstance(value, list) and value:
            return list(value)
    return []


def _expand_trace_name(name: str) -> tuple[str, ...]:
    if not any(token in name for token in ("*", "?", "[")):
        return (name,)
    return _TRACE_PATTERN_EXPANSIONS.get(name, ())


def output_hierarchy(
    name: str,
    *,
    group_id: str | None = None,
    group_label: str | None = None,
    target_level: str | None = None,
) -> dict[str, str]:
    """Return stable UI hierarchy metadata for one observable output."""

    resolved_group_id, resolved_group_label = output_group(name)
    group_id = str(group_id or resolved_group_id)
    group_label = str(group_label or resolved_group_label)
    raw_prefix = str(name).split(".", 1)[0].lower()
    subsystem_id = "whole_spacecraft"
    subsystem_label = "整星综合"
    for prefix, candidate_id, candidate_label in _OUTPUT_SUBSYSTEM_PREFIXES:
        if raw_prefix == prefix or str(name).lower().startswith(f"{prefix}."):
            subsystem_id = candidate_id
            subsystem_label = candidate_label
            break

    canonical_target_level = _canonical_level(str(target_level or ""))
    if group_id in _WHOLE_SPACECRAFT_OUTPUT_GROUPS:
        scope_id, scope_label = "whole_spacecraft", "整星"
        subsystem_id, subsystem_label = "whole_spacecraft", "整星综合"
    elif group_id in _COMPONENT_OUTPUT_GROUPS or canonical_target_level == "component":
        scope_id, scope_label = "component", "部件"
    else:
        scope_id, scope_label = "subsystem", "分系统"

    return {
        "scope_id": scope_id,
        "scope_label": scope_label,
        "subsystem_id": subsystem_id,
        "subsystem_label": subsystem_label,
        "node_id": group_id,
        "node_label": group_label,
    }


def _output_options(contract: Any, key: str, fallback: tuple[str, ...] | list[str]) -> list[dict[str, Any]]:
    rows = _explicit_output_rows(contract, key)
    by_name = {
        str(item.get("name")): item
        for item in rows
        if isinstance(item, Mapping) and item.get("name")
    }
    source_names = [
        str(item.get("name")) if isinstance(item, Mapping) else str(item)
        for item in rows
        if (isinstance(item, Mapping) and item.get("name")) or isinstance(item, str)
    ] or list(fallback)
    names: list[str] = []
    for source_name in source_names:
        expanded = _expand_trace_name(source_name) if key == "trace" else (source_name,)
        for name in expanded:
            if name not in names:
                names.append(name)
    destination = "结果摘要" if key == "summary" else "曲线和遥测"
    options: list[dict[str, Any]] = []
    for name in names:
        source_pattern = next(
            (pattern for pattern in source_names if name in _expand_trace_name(pattern)),
            name,
        ) if key == "trace" else name
        payload = by_name.get(name, by_name.get(source_pattern, {}))
        if key == "trace" and not _is_plottable_trace(name, payload):
            continue
        unit = payload.get("unit")
        group_id, group_label = output_group(name)
        hierarchy = output_hierarchy(
            name,
            group_id=group_id,
            group_label=group_label,
            target_level=contract.target_level,
        )
        options.append({
            "name": name,
            "label": output_label(name),
            "unit": unit,
            "dtype": payload.get("dtype"),
            "plottable": key == "trace",
            "group_id": group_id,
            "group_label": group_label,
            **hierarchy,
            "description": str(payload.get("description") or _output_description(name, str(unit) if unit else None, destination=destination)),
        })
    return options


def _default_plots(contract: Any) -> list[str]:
    observability = contract.operator_contract.observability
    return [item["name"] for item in _output_options(contract, "trace", observability.trace_fields)[:4]]


def _capability_display_name(capability_id: str, fallback: str) -> str:
    try:
        for item in workbench_presentation_catalog().get("objects", []):
            for variant in item.get("variants", []):
                if variant.get("capability_id") != capability_id:
                    continue
                object_name = str(item.get("name_zh") or fallback)
                variant_name = str(variant.get("name_zh") or "").strip()
                if len(item.get("variants", [])) > 1 and variant_name and variant_name != object_name:
                    return f"{object_name} · {variant_name}"
                return object_name
    except Exception:
        pass
    return fallback


def _simulation_defaults(contract: Any) -> dict[str, float]:
    """Resolve capability-owned simulation defaults without creating a second source of truth."""

    resolved = {"duration_s": 60.0, "sample_s": 10.0, "step_s": 10.0}
    parameters = _mapping(contract.data.get("simulation_parameters"))
    for form_key, contract_key in (
        ("duration_s", "duration_s"),
        ("sample_s", "sample_s"),
        ("step_s", "solver.step_s"),
    ):
        payload = parameters.get(contract_key)
        if not isinstance(payload, Mapping) or "default" not in payload:
            continue
        try:
            value = float(payload["default"])
        except (TypeError, ValueError):
            continue
        if value > 0.0:
            resolved[form_key] = value
    return resolved


def _default_form(contract: Any) -> dict[str, Any]:
    data = contract.data
    parameter_values = {
        name: copy.deepcopy(payload["default"])
        for name, payload in _mapping(data.get("parameters")).items()
        if isinstance(payload, Mapping) and "default" in payload
    }
    level = _canonical_level(contract.target_level)
    implementation = _mapping(data.get("implementation"))
    accepted_backends = implementation.get("accepted_task_backends")
    backend = "selective_unified_basilisk_assembly"
    if isinstance(accepted_backends, (list, tuple)):
        normalized_backends = [str(item).strip() for item in accepted_backends if str(item).strip()]
        if len(normalized_backends) == 1:
            backend = normalized_backends[0]
    simulation_defaults = _simulation_defaults(contract)
    task_id = contract.capability_id.replace(".", "_")
    technical_name = str(data.get("name") or contract.capability_id)
    display_name = _capability_display_name(contract.capability_id, technical_name)
    return {
        "capability_id": contract.capability_id,
        "task": {"id": task_id, "name": display_name, "description": "", "tags": ["schema_form"]},
        "simulation": {
            "level": level,
            **({"subsystem": contract.target_name} if level == "subsystem" else {}),
            "duration_s": simulation_defaults["duration_s"],
            "sample_s": simulation_defaults["sample_s"],
            "step_s": simulation_defaults["step_s"],
            "backend": backend, "random_seed": 0,
        },
        "parameters": {"profile": "demo", "values": parameter_values},
        "events": {"faults": [], "degradations": [], "constraints": []},
        "outputs": {
            "output_root": f"runs/{task_id}", "trace_format": "csv",
            "qoi": _default_qoi(contract), "plots": _default_plots(contract),
            "telemetry_streams": [], "fmea": {"enabled": False, "formats": ["csv", "json"]},
            "include_summary": True, "include_trace": True, "include_labels": True, "include_manifest": True,
        },
        "assurance": {
            "fidelity_level": "declared_by_capability", "claim_level": "analysis_only",
            "validation_profile": "default", "parameter_profile": "demo", "allow_proxy": False,
        },
        "model": {"capability_id": contract.capability_id, "target": {"level": level, "name": contract.target_name, "mode": "nominal"}},
    }


def _parameter_sections(contract: Any, fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parameter_fields = [item for item in fields if item["path"].startswith("parameters.")]
    if contract.target_level != "subsystem" or contract.capability_id not in SUBSYSTEM_PARAMETER_GROUPS:
        return [{"id": "parameters", "title": "模型参数", "description": "常用参数默认显示，专业参数可按需展开。", "field_paths": [item["path"] for item in parameter_fields]}]
    sections: list[dict[str, Any]] = []
    seen: set[str] = set()
    for field in parameter_fields:
        group_id = str(field.get("group_id") or "other")
        if group_id in seen:
            continue
        seen.add(group_id)
        grouped = [item["path"] for item in parameter_fields if str(item.get("group_id") or "other") == group_id]
        sections.append({
            "id": f"parameters_{group_id}",
            "title": str(field.get("group_title") or "模型参数"),
            "description": str(field.get("group_description") or "常用参数默认显示，专业参数可按需展开。"),
            "field_paths": grouped,
            "parameter_group": group_id,
        })
    return sections


def capability_form_schema(capability_id: str) -> dict[str, Any]:
    """Return a registry-derived form contract for one capability."""

    contract = get_capability(capability_id)
    data = contract.data
    observability = contract.operator_contract.observability
    simulation_defaults = _simulation_defaults(contract)
    fields = [
        _field("task.id", {"type": "string", "required": True}, importance="common"),
        _field("task.name", {"type": "string", "required": False}, importance="common"),
        _field("simulation.duration_s", {"type": "number", "required": True, "min": 1e-9, "unit": "s", "default": simulation_defaults["duration_s"]}, importance="common"),
        _field("simulation.sample_s", {"type": "number", "required": True, "min": 1e-9, "unit": "s", "default": simulation_defaults["sample_s"]}, importance="common"),
        _field("simulation.step_s", {"type": "number", "required": False, "min": 1e-9, "unit": "s", "default": simulation_defaults["step_s"]}, importance="advanced"),
        *_parameter_fields(contract),
    ]
    summary_options = _output_options(contract, "summary", observability.qoi)
    trace_options = _output_options(contract, "trace", observability.trace_fields)
    explicit_trace_rows = _explicit_output_rows(contract, "trace")
    trace_source_names = [
        str(item.get("name")) if isinstance(item, Mapping) else str(item)
        for item in explicit_trace_rows
        if (isinstance(item, Mapping) and item.get("name")) or isinstance(item, str)
    ] or list(observability.trace_fields)
    unresolved_trace_patterns = [
        name
        for name in trace_source_names
        if any(token in name for token in ("*", "?", "[")) and not _expand_trace_name(name)
    ]
    return {
        "schema_version": FORM_SCHEMA_VERSION,
        "task_spec_schema_version": CANONICAL_TASK_SPEC_VERSION,
        "capability": {
            "capability_id": contract.capability_id, "name": data.get("name"), "summary": data.get("summary"),
            "level": _canonical_level(contract.target_level), "target": contract.target_name, "trust_level": contract.trust_level,
            "lifecycle": contract.lifecycle, "limitations": list(contract.operator_contract.assurance.limitations),
        },
        "sections": [
            {"id": "identity", "title": "任务信息", "description": "定义任务的显示名称和机器标识。", "field_paths": ["task.id", "task.name"]},
            {"id": "simulation", "title": "仿真时间配置", "description": "控制仿真总时长、结果采样和内部推进步长。", "field_paths": ["simulation.duration_s", "simulation.sample_s", "simulation.step_s"]},
            *_parameter_sections(contract, fields),
            {"id": "events", "title": "故障、退化与运行约束", "repeatable": True},
            {"id": "outputs", "title": "输出与曲线", "repeatable": True},
            {"id": "assurance", "title": "可信度与声明边界"},
        ],
        "fields": fields,
        "event_catalog": {
            "faults": _effect_templates(contract, "fault"),
            "degradations": _effect_templates(contract, "degradation"),
            "constraints": _effect_templates(contract, "constraint"),
            "timed_degradation_supported": bool(_mapping(data.get("modes")).get("degradation", {}).get("timed_events_supported", True)),
        },
        "outputs": {
            "summary_qoi": [item["name"] for item in summary_options],
            "trace_fields": [item["name"] for item in trace_options],
            "summary_options": summary_options, "trace_options": trace_options,
            "unresolved_trace_patterns": unresolved_trace_patterns,
            "time_axis": {"name": "time_s", "label": "仿真时间", "unit": "s", "description": "运行结果自动使用 time_s 作为曲线横轴，不需要在纵轴曲线中勾选。"},
            "metadata_fields": ["task_id", "case_id", "sample_index", "utc"],
            "coverage_note": (
                "曲线只展示当前运行器可逐项验证的具体字段；有限维通配输出已展开为轴、轮号或节点叶子，无法可靠展开的模式不会作为可勾选参量。"
            ),
            "produces": list(observability.produces), "default_qoi": _default_qoi(contract),
            "multi_rate_telemetry": {
                "supported": True,
                "contract_path": "outputs.telemetry_streams",
                "policy": "record at simulation.sample_s and export slower integer-multiple streams without interpolation",
                "formats": ["csv", "jsonl"],
            },
            "fmea_export": {
                "supported": True,
                "contract_path": "outputs.fmea",
                "formats": ["csv", "json"],
                "risk_policy": "RPN is computed only from explicit severity/occurrence/detectability ratings",
            },
        },
        "cross_field_rules": [
            {
                "rule_id": "time_grid_order",
                "expression": "simulation.step_s <= simulation.sample_s <= simulation.duration_s",
                "message": "积分步长不得大于输出采样间隔，输出采样间隔不得大于仿真时长。",
                "paths": ["simulation.step_s", "simulation.sample_s", "simulation.duration_s"],
            }
        ],
        "canonical_taskspec_schema": CanonicalTaskSpec.model_json_schema(),
        "default_form": _default_form(contract),
        "submission_contract": {
            "input_kind": "form", "endpoint": "/tasks/parse", "registry_is_authoritative": True,
            "arbitrary_python_allowed": False,
        },
    }


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def task_spec_to_form_data(capability_id: str, task_spec: Mapping[str, Any]) -> dict[str, Any]:
    """Project a canonical TaskSpec back into the authoritative capability form."""

    if not isinstance(task_spec, Mapping):
        raise TypeError("task_spec must be a mapping")
    declared = str(_mapping(task_spec.get("model")).get("capability_id") or "")
    if declared and declared != capability_id:
        raise ValueError(f"TaskSpec capability_id {declared!r} does not match requested form {capability_id!r}")
    schema = capability_form_schema(capability_id)
    form_data = copy.deepcopy(schema["default_form"])
    allowed = ("task", "simulation", "parameters", "events", "outputs", "assurance", "model")
    for key in allowed:
        value = task_spec.get(key)
        if isinstance(value, Mapping):
            _deep_merge(form_data[key], value)
    form_data["capability_id"] = capability_id
    form_data["model"]["capability_id"] = capability_id
    events = form_data.setdefault("events", {})
    for key in ("faults", "degradations", "constraints"):
        value = events.get(key)
        events[key] = copy.deepcopy(value) if isinstance(value, list) else []
    outputs = form_data.setdefault("outputs", {})
    for key in ("qoi", "plots"):
        value = outputs.get(key)
        outputs[key] = list(value) if isinstance(value, (list, tuple)) else []
    return {
        "schema_version": FORM_SCHEMA_VERSION,
        "capability_id": capability_id,
        "form_data": form_data,
        "projected_field_paths": [item["path"] for item in schema["fields"]],
        "projection_is_lossless": False,
        "projection_note": "The form contains registered editable fields; canonical-only provenance and model internals remain in the TaskSpec editor.",
    }


def capability_form_catalog(*, include_compatibility: bool = False, include_explicit: bool = False) -> dict[str, Any]:
    """Return form metadata for all active Agent capabilities."""

    items: list[dict[str, Any]] = []
    for capability_id in active_capability_ids():
        contract = get_capability(capability_id)
        items.append({
            "capability_id": capability_id, "name": contract.data.get("name"), "level": _canonical_level(contract.target_level),
            "target": contract.target_name, "trust_level": contract.trust_level,
            "form_schema_path": f"/forms/capabilities/{capability_id}",
            "supports_faults": any(item.kind == "fault" for item in contract.operator_contract.effects),
            "supports_degradations": any(item.kind == "degradation" for item in contract.operator_contract.effects),
        })
    return {
        "schema_version": FORM_SCHEMA_VERSION, "count": len(items), "capabilities": items,
        "presentation": workbench_presentation_catalog(include_compatibility=include_compatibility, include_explicit=include_explicit),
    }


__all__ = ["FORM_SCHEMA_VERSION", "capability_form_schema", "capability_form_catalog", "task_spec_to_form_data", "output_label", "output_hierarchy"]
