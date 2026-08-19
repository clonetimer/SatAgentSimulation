"""Generate contract-grounded natural-language end-to-end acceptance cases."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Sequence

from .form_schema import capability_form_schema
from .lmstudio_acceptance import AcceptanceCase


Variant = Literal["zh", "en", "alias"]
MODES = ("nominal", "fault", "degradation")


def _load(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _event_text(event: dict, *, variant: Variant, scenario_name: str) -> str:
    effect = str(event.get("effect") or "")
    start = float(event.get("start_s", 0.0))
    end = event.get("end_s")
    parameters = event.get("parameters") if isinstance(event.get("parameters"), dict) else {}
    parameter_text = f" with parameters {json.dumps(parameters, ensure_ascii=False)}" if parameters else ""
    if variant == "en":
        window = f"from {start:g}s" + (f" to {float(end):g}s" if end is not None else "")
        return f"inject event effect={effect} {window}{parameter_text}"
    if variant == "alias":
        window = f"，从{start:g}秒开始" + (f"，到{float(end):g}秒恢复" if end is not None else "")
        return f"模拟“{scenario_name}”{window}"
    window = f"，在{start:g}秒注入" + (f"，{float(end):g}秒结束" if end is not None else "")
    zh_parameters = f"，参数为 {json.dumps(parameters, ensure_ascii=False)}" if parameters else ""
    return f"使用事件 effect={effect}{window}{zh_parameters}"


def _request(
    *,
    capability_id: str,
    object_name: str,
    scenario_name: str,
    mode: str,
    event: dict | None,
    variant: Variant,
) -> str:
    if variant == "en":
        prefix = (
            f"Create a 12-second internal engineering simulation for {object_name}. "
            f"Use capability_id={capability_id} and set mode={mode}. "
        )
        body = "Run nominally with no faults or degradations." if event is None else _event_text(
            event, variant=variant, scenario_name=scenario_name
        )
        return prefix + body + " Export a deterministic Python simulation script."
    if variant == "alias":
        body = "正常运行，不注入故障或退化" if event is None else _event_text(
            event, variant=variant, scenario_name=scenario_name
        )
        return (
            f"为{object_name}创建12秒内部工程仿真，采用能力 {capability_id}，"
            f"{body}，并导出确定性Python仿真脚本。"
        )
    body = "按正常模式运行，不注入故障或退化" if event is None else _event_text(
        event, variant=variant, scenario_name=scenario_name
    )
    return (
        f"为{object_name}创建12秒内部工程仿真，明确使用 capability_id={capability_id}，"
        f"模式为{mode}，{body}，并导出确定性Python仿真脚本。"
    )


def generate_nl_e2e_cases(
    *,
    scope_path: str | Path = "configs/acceptance/object_scope.json",
    scenario_path: str | Path = "configs/acceptance/scenario_baseline.json",
    variants: Sequence[Variant] = ("zh", "en", "alias"),
    modes: Sequence[str] = MODES,
) -> tuple[AcceptanceCase, ...]:
    scope = _load(scope_path)
    baseline = _load(scenario_path)
    scenario_by_id = {
        str(item["object_id"]): item for item in baseline.get("objects", [])
    }
    cases: list[AcceptanceCase] = []
    for obj in scope.get("objects", []):
        object_id = str(obj["object_id"])
        capability_id = str(obj["primary_capability_id"])
        object_name = str(obj.get("name_zh") or object_id)
        scenarios = (scenario_by_id.get(object_id) or {}).get("scenarios") or {}
        form = capability_form_schema(capability_id)
        catalog = form.get("event_catalog") or {}
        for mode in modes:
            scenario_rows = scenarios.get(mode) or []
            scenario = scenario_rows[0] if scenario_rows else {}
            scenario_name = str(scenario.get("name_zh") or scenario.get("scenario_id") or mode)
            request_scenario_name = scenario_name
            event = None
            if mode != "nominal":
                plural = "faults" if mode == "fault" else "degradations"
                rows = catalog.get(plural) or []
                if not rows:
                    raise ValueError(f"{capability_id} has no {mode} event catalog")
                event = dict(rows[0]["default_event"])
                event_label = str(rows[0].get("label") or scenario_name)
                request_scenario_name = (
                    f"{event_label}（{'故障' if mode == 'fault' else '退化'}）"
                )
                event["start_s"] = 0.0 if mode == "degradation" and event.get("end_s") is None else 2.0
                if event.get("end_s") is not None:
                    event["end_s"] = 8.0
            for variant in variants:
                effect = str(event.get("effect")) if event is not None else ""
                cases.append(AcceptanceCase(
                    case_id=f"{object_id.replace('.', '_')}__{mode}__{variant}",
                    request_text=_request(
                        capability_id=capability_id,
                        object_name=object_name,
                        scenario_name=request_scenario_name,
                        mode=mode,
                        event=event,
                        variant=variant,
                    ),
                    expected_capability_ids=(capability_id,),
                    expected_mode=mode,
                    expected_effects=(effect,) if effect else (),
                ))
    return tuple(cases)


__all__ = ["MODES", "Variant", "generate_nl_e2e_cases"]
