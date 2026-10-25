"""Explicit, fail-closed offline whole-Spacecraft → live native configuration review.

Only changes to parameters intentionally edited away from the registered
offline default are eligible. Cross-mode defaults are deliberately NOT asserted
equal. This helper never launches or changes a Basilisk session.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sat_sim.capability_registry import get_capability
from .continuous_initial_config import ContinuousInitialConfiguration

WHOLE_ID = "whole_spacecraft.unified_native.v1"


def preview_finite_config_for_live(form_data: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(form_data, Mapping):
        raise ValueError("OFFLINE_CONFIG_OBJECT_REQUIRED")
    declared = str(form_data.get("capability_id") or "")
    model = form_data.get("model")
    model_id = str(model.get("capability_id") or "") if isinstance(model, Mapping) else ""
    if declared != WHOLE_ID and model_id != WHOLE_ID:
        raise ValueError("LIVE_CONFIG_REQUIRES_OFFLINE_NATIVE_WHOLE")
    if declared and model_id and declared != model_id:
        raise ValueError("LIVE_CONFIG_CAPABILITY_CONFLICT")
    if isinstance(model, Mapping):
        target = model.get("target")
        if isinstance(target, Mapping) and target.get("mode", "nominal") != "nominal":
            raise ValueError("LIVE_CONFIG_NOMINAL_ONLY")

    contract = get_capability(WHOLE_ID)
    registered = contract.data["parameters"]
    config = ContinuousInitialConfiguration()
    groups: dict[str, type] = {
        name: type(getattr(config, name))
        for name in ("orbit", "adcs", "eps", "payload", "comm_data", "propulsion", "thermal")
    }
    owner = {
        key: name for name, cls in groups.items()
        for key in cls.model_fields
    }
    params = form_data.get("parameters", {})
    if not isinstance(params, Mapping):
        raise ValueError("LIVE_CONFIG_INVALID_PARAMETERS")
    values = params.get("values", {})
    if not isinstance(values, Mapping):
        raise ValueError("LIVE_CONFIG_INVALID_VALUES")

    events = form_data.get("events") or {}
    blockers: list[dict[str, str]] = []
    if not isinstance(events, Mapping):
        blockers.append({"path": "events", "reason": "无法解析离线事件配置"})
    else:
        for event_type in ("faults", "degradations", "constraints"):
            if events.get(event_type):
                blockers.append({"path": "events." + event_type,
                                 "reason": "离线故障／退化／约束不能转成实时运行前参数；须使用实时命令或独立流程"})
        for event_type in events:
            if event_type not in {"faults", "degradations", "constraints"} and events[event_type]:
                blockers.append({"path": "events." + event_type, "reason": "未登记的离线事件字段"})
    assurance = form_data.get("assurance")
    if isinstance(assurance, Mapping) and assurance.get("allow_proxy"):
        blockers.append({"path": "assurance.allow_proxy", "reason": "不允许将含代理授权的离线配置复用为原生实时配置"})

    selected: dict[str, dict[str, Any]] = {}
    changed: list[str] = []
    defaults_differ: list[str] = []
    live_defaults = config.model_dump()
    for key, setting in registered.items():
        if key in owner and isinstance(setting, Mapping) and "default" in setting:
            src_default = setting["default"]
            live_default = live_defaults[owner[key]][key]
            if src_default != live_default:
                defaults_differ.append(key)

    for key, value in values.items():
        if key not in registered:
            blockers.append({"path": "parameters.values." + key,
                             "reason": "离线能力没有登记这个参数"})
            continue
        setting = registered[key]
        if not isinstance(setting, Mapping) or "default" not in setting or value != setting["default"]:
            changed.append(key)
            if key not in owner:
                blockers.append({"path": "parameters.values." + key,
                                 "reason": "实时原生初始配置不支持此离线参数，禁止静默丢弃"})
            else:
                selected.setdefault(owner[key], {})[key] = value

    if not blockers:
        try:
            validated = ContinuousInitialConfiguration.model_validate(selected)
            native_values = validated.native_values()
        except ValueError as exc:
            blockers.append({"path": "initial_configuration",
                             "reason": "与实时原生参数约束不兼容：" + str(exc)[:350]})
            native_values = {}
    else:
        native_values = {}

    return {
        "schema_version": "sat-sim.offline-live-config-preview.v1",
        "source_capability_id": WHOLE_ID,
        "eligible": not blockers and bool(native_values),
        "native_values": native_values,
        "grouped_values": selected if not blockers else {},
        "changed_offline_parameters": sorted(changed),
        "applied_parameters": sorted(native_values),
        "blocked": blockers,
        "cross_mode_default_differences": sorted(defaults_differ),
        "notes": [
            "仅迁移相对离线能力注册默认值被修改的参数；其他实时参数保留原生实时默认值。",
            "离线与实时默认值可能不同；不承诺迁移后与离线运行轨迹物理相同。",
            "不会导入故障事件、退化、输出、仿真时长、运行模式或 FSW 遥控状态。",
            "仅生成运行前预览；创建实时会话需要另行确认，导入本身不启动仿真。",
        ],
    }
