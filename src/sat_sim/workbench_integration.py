"""Workbench and Agent tool surfaces for fault episodes and experiments.

This module intentionally exposes deterministic, schema-backed operations that
can be used by both the Web workbench and the natural-language Agent.  It does
not execute arbitrary Python and it does not ask the LLM to invent fault models,
dispersion paths, or metric fields.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from sat_sim.assertions import normalize_assertions
from sat_sim.experiment_manager import expand_sweep, sweep_parameter_options
from sat_sim.experiments.dispersion_registry import dispersion_parameter_options
from sat_sim.experiments.monte_carlo_engine import expand_monte_carlo
from sat_sim.fault_environment.fault_catalog import list_fault_contracts
from sat_sim.task_models import canonicalize_task_spec

_SCHEMA_VERSION = "workbench-integration.v1"


def _capability_name(task_or_capability: Mapping[str, Any] | str | None) -> str:
    if isinstance(task_or_capability, str):
        return task_or_capability
    if not isinstance(task_or_capability, Mapping):
        return ""
    return str(
        task_or_capability.get("model", {}).get("capability_id")
        or task_or_capability.get("capability_id")
        or ""
    )


def _contract_matches_capability(contract: Mapping[str, Any], capability_id: str) -> bool:
    """Conservatively filter known event models for a selected capability.

    The fault catalog is intentionally global.  For UI/Agent use we group the
    most relevant contracts so users do not see unrelated event types.  If a
    capability is unknown, the caller can still request the full catalog.
    """
    if not capability_id:
        return True
    effect = str(contract.get("effect") or "")
    target = str(contract.get("target_kind") or "")
    cap = capability_id.lower()
    if "adcs" in cap:
        return effect.startswith("adcs_") or target in {"reaction_wheel", "gyro", "spacecraft"}
    if "reaction_wheel" in cap:
        return (not effect.startswith("adcs_")) and target == "reaction_wheel"
    if "mtb" in cap or "magnet" in cap:
        return target in {"magnetorquer", "spacecraft"}
    return True


def list_workbench_fault_models(
    *,
    capability_id: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """Return UI/Agent-ready fault, degradation and constraint models."""
    rows: list[dict[str, Any]] = []
    for contract in list_fault_contracts():
        if category and str(contract.get("category")) != str(category):
            continue
        if not _contract_matches_capability(contract, str(capability_id or "")):
            continue
        hints = contract.get("parameter_hints") or {}
        rows.append(
            {
                "effect": contract.get("effect"),
                "category": contract.get("category"),
                "display_name": contract.get("display_name_zh") or contract.get("effect"),
                "target_kind": contract.get("target_kind"),
                "trigger_semantics": contract.get("trigger_semantics"),
                "expected_observables": list(contract.get("expected_observables") or []),
                "parameter_hints": dict(hints),
                "ui_group": {
                    "fault": "故障注入",
                    "degradation": "性能退化",
                    "constraint": "运行约束/状态",
                }.get(str(contract.get("category")), "其他事件"),
            }
        )
    return {
        "ok": True,
        "schema_version": _SCHEMA_VERSION,
        "capability_id": capability_id,
        "category": category,
        "count": len(rows),
        "fault_models": rows,
    }


def experiment_design_options(base_task_spec: Mapping[str, Any]) -> dict[str, Any]:
    """Return schema-backed sweep/Monte Carlo options for UI and Agent."""
    canonical = canonicalize_task_spec(base_task_spec)
    sweep_options = sweep_parameter_options(canonical)
    dispersion_options = dispersion_parameter_options(canonical)
    return {
        "ok": True,
        "schema_version": _SCHEMA_VERSION,
        "capability_id": _capability_name(canonical),
        "sweep_options": sweep_options,
        "dispersion_options": dispersion_options,
        "agent_tools": [
            "create_sweep_plan",
            "create_monte_carlo_plan",
            "validate_experiment_plan",
        ],
        "notes": [
            "参数来自当前能力 Schema，不接受模型或用户手写未登记路径。",
            "数组、对象和复杂事件组合需要专用编辑器；本接口只暴露标量/枚举参数。",
        ],
    }


def preview_experiment_plan(
    *,
    base_task_spec: Mapping[str, Any],
    experiment_type: str = "sweep",
    sweep: Mapping[str, list[Any]] | None = None,
    sampling_plan: Mapping[str, Any] | None = None,
    assertions: list[dict[str, Any]] | None = None,
    preview_limit: int = 8,
) -> dict[str, Any]:
    """Validate and preview an experiment without persisting it."""
    canonical = canonicalize_task_spec(base_task_spec)
    normalized_type = str(experiment_type or "sweep").lower()
    if normalized_type in {"montecarlo", "mc"}:
        normalized_type = "monte_carlo"
    plan = dict(sampling_plan or {})
    if normalized_type == "monte_carlo":
        variants = expand_monte_carlo(
            canonical,
            plan.get("dispersions") or {},
            sample_count=int(plan.get("sample_count") or 16),
            seed=int(plan.get("seed") or 1),
        )
    elif normalized_type == "sweep":
        variants = expand_sweep(canonical, sweep or {})
    else:
        raise ValueError(f"unsupported experiment_type: {experiment_type}")
    assertions_payload = normalize_assertions(assertions or [])
    preview = [
        {
            "variant_index": int(item.get("variant_index", index)),
            "parameters": copy.deepcopy(item.get("parameters") or {}),
            "task_id": item.get("task_spec", {}).get("task", {}).get("id"),
            "task_name": item.get("task_spec", {}).get("task", {}).get("name"),
        }
        for index, item in enumerate(variants[: max(0, int(preview_limit))])
    ]
    return {
        "ok": True,
        "schema_version": _SCHEMA_VERSION,
        "experiment_type": normalized_type,
        "variant_count": len(variants),
        "preview_count": len(preview),
        "preview": preview,
        "assertions": assertions_payload,
        "capability_id": _capability_name(canonical),
        "run_bundle_policy": "one_variant_one_run_bundle",
        "monte_carlo_compatibility": "taskspec_sampling_plan; basilisk_mc_adapter_available",
    }


def agent_tool_surface() -> dict[str, Any]:
    """Stable tool list exposed to the natural-language Agent."""
    return {
        "ok": True,
        "schema_version": _SCHEMA_VERSION,
        "tools": [
            {
                "name": "list_fault_models",
                "description": "列出当前能力可用的故障、退化和运行约束模型。",
                "deterministic": True,
            },
            {
                "name": "list_dispersions",
                "description": "列出当前基础任务可用于枚举扫描或 Monte Carlo 的参数。",
                "deterministic": True,
            },
            {
                "name": "create_sweep_plan",
                "description": "基于 Schema 注册路径创建枚举扫描计划。",
                "deterministic": True,
            },
            {
                "name": "create_monte_carlo_plan",
                "description": "基于 dispersion registry 创建 Monte Carlo 随机采样计划。",
                "deterministic": True,
            },
            {
                "name": "validate_experiment_plan",
                "description": "预览实验变体数量、参数和值，不持久化、不运行。",
                "deterministic": True,
            },
        ],
        "forbidden": [
            "invent_fault_model",
            "invent_dispersion_path",
            "write_arbitrary_python",
            "execute_arbitrary_python",
        ],
    }


__all__ = [
    "agent_tool_surface",
    "experiment_design_options",
    "list_workbench_fault_models",
    "preview_experiment_plan",
]
