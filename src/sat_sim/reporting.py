"""Generate a self-contained, print-friendly HTML report for a sealed run."""
from __future__ import annotations

import csv
import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from sat_sim.form_schema import EFFECT_LABELS_ZH, output_label

REPORT_SCHEMA_VERSION = "release.run-report.v1"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _escape(value: Any) -> str:
    return html.escape("—" if value is None else str(value), quote=True)


def _format_value(value: Any) -> str:
    if isinstance(value, float):
        if abs(value) >= 100000 or (value != 0 and abs(value) < 0.0001):
            return f"{value:.6e}"
        return f"{value:.6f}".rstrip("0").rstrip(".")
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _table(rows: Iterable[tuple[Any, Any]], *, empty: str = "暂无数据") -> str:
    rendered = []
    for key, value in rows:
        rendered.append(f"<tr><th>{_escape(key)}</th><td>{_escape(_format_value(value))}</td></tr>")
    return f'<table class="kv"><tbody>{"".join(rendered)}</tbody></table>' if rendered else f'<p class="empty">{_escape(empty)}</p>'


def _status_zh(value: Any) -> Any:
    mapping = {
        "PASS": "通过", "FAIL": "未通过", "SUCCEEDED": "已完成", "FAILED": "失败",
        "INCONCLUSIVE": "结论不足", "NOT_CONFIGURED": "未配置", "complete": "已完成",
        "true": "是", "false": "否", True: "是", False: "否",
        "medium": "中等保真度", "basic": "基础保真度", "high": "高保真度",
        "analysis_only": "仅分析用途", "nominal": "正常", "fault": "故障", "degradation": "退化", "constraint": "运行约束", "mixed": "故障与退化组合",
    }
    return mapping.get(value, mapping.get(str(value), value))


def _metric_rows(payload: Mapping[str, Any], max_rows: int = 80) -> list[tuple[str, Any]]:
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), Mapping) else payload
    internal_prefixes = (
        "adapter_metadata.", "config.", "adcs1_fidelity.config.", "known_physics_limits.",
        "validation_preview.", "events.",
    )
    internal_exact = {
        "schema_version", "task_id", "case_id", "capability_id", "target_level", "target_name",
        "trace_rows", "model_family", "reason_high_fidelity_still_blocked", "can_claim_high_fidelity",
    }
    rows: list[tuple[str, Any]] = []
    for key, value in metrics.items():
        raw = str(key)
        if raw in internal_exact or raw.startswith(internal_prefixes):
            continue
        label = output_label(raw)
        if label == "可观测量":
            label = raw
        rows.append((label, _status_zh(value)))
    rows.sort(key=lambda item: item[0])
    return rows[:max_rows]


def _claim_label(value: Any) -> str:
    raw = str(value)
    mapping = {
        "capability_plan_compilable": "能力计划可编译",
        "claim_level:analysis_only": "声明等级：仅分析用途",
        "simulation_execution_completed": "仿真执行已完成",
        "task_requirements_satisfied": "任务要求已满足",
        "taskspec_schema_valid": "TaskSpec结构校验通过",
    }
    return mapping.get(raw, raw)


def _svg_chart(csv_path: Path, series: Sequence[str], *, width: int = 900, height: int = 260) -> str:
    if not csv_path.is_file() or not series:
        return '<p class="empty">当前运行没有可绘制的时序数据。</p>'
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        raw = list(reader)
    if not raw:
        return '<p class="empty">当前运行没有可绘制的时序数据。</p>'
    time_key = "time_s" if "time_s" in raw[0] else "t_s" if "t_s" in raw[0] else None
    if not time_key:
        return '<p class="empty">遥测中缺少时间字段。</p>'
    step = max(1, len(raw) // 400)
    rows = raw[::step]
    try:
        times = [float(row[time_key]) for row in rows]
    except Exception:
        return '<p class="empty">时间字段无法解析。</p>'
    if not times:
        return '<p class="empty">当前运行没有可绘制的时序数据。</p>'
    palette = ["#087f67", "#176fc1", "#b06b00", "#b23b54", "#6a55b8", "#4e7a32"]
    margin = 38
    inner_w = width - margin * 2
    inner_h = height - margin * 2
    t_min, t_max = min(times), max(times)
    if t_max == t_min:
        t_max = t_min + 1.0
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="遥测曲线">',
             f'<rect width="{width}" height="{height}" rx="12" fill="#fbfdff" stroke="#d5e0e7"/>',
             f'<line x1="{margin}" y1="{height-margin}" x2="{width-margin}" y2="{height-margin}" stroke="#879aa6"/>',
             f'<line x1="{margin}" y1="{margin}" x2="{margin}" y2="{height-margin}" stroke="#879aa6"/>']
    legend = []
    rendered_count = 0
    for index, field in enumerate(series[:6]):
        values: list[float] = []
        valid = True
        for row in rows:
            try:
                values.append(float(row[field]))
            except Exception:
                valid = False
                break
        if not valid or not values:
            continue
        y_min, y_max = min(values), max(values)
        if y_max == y_min:
            y_max = y_min + 1.0
        points = []
        for t_value, y_value in zip(times, values):
            x = margin + ((t_value - t_min) / (t_max - t_min)) * inner_w
            y = margin + (1.0 - (y_value - y_min) / (y_max - y_min)) * inner_h
            points.append(f"{x:.2f},{y:.2f}")
        color = palette[index % len(palette)]
        parts.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="1.8"/>')
        legend.append(f'<span><i style="background:{color}"></i>{_escape(field)}</span>')
        rendered_count += 1
    parts.append(f'<text x="{margin}" y="{height-10}" font-size="11" fill="#607482">t={t_min:g}s</text>')
    parts.append(f'<text x="{width-margin-70}" y="{height-10}" font-size="11" fill="#607482">t={t_max:g}s</text>')
    parts.append("</svg>")
    if not rendered_count:
        return '<p class="empty">所选曲线字段无法解析为数值。</p>'
    return "".join(parts) + f'<div class="legend">{"".join(legend)}</div>'


def generate_run_report(bundle_root: str | Path) -> dict[str, Any]:
    root = Path(bundle_root).resolve()
    spec = _read_json(root / "input" / "task_spec.json")
    run_record = _read_json(root / "run_record.json")
    validation = _read_json(root / "validation" / "validation_outcome.json")
    claim = _read_json(root / "validation" / "claim_report.json")
    metrics = _read_json(root / "results" / "metrics.json")
    events = _read_json(root / "results" / "events.json")
    assertions = _read_json(root / "results" / "assertions.json")
    plots = _read_json(root / "results" / "plot_manifest.json")
    environment = _read_json(root / "runtime" / "environment.json")
    dependency_doc = _read_json(root / "runtime" / "dependency_versions.json")
    dependencies = dependency_doc.get("dependencies") if isinstance(dependency_doc.get("dependencies"), Mapping) else dependency_doc
    task = spec.get("task") if isinstance(spec.get("task"), Mapping) else {}
    model = spec.get("model") if isinstance(spec.get("model"), Mapping) else {}
    simulation = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
    declared_events = events.get("declared") if isinstance(events.get("declared"), list) else []
    assertion_rows = assertions.get("results") if isinstance(assertions.get("results"), list) else []
    series = [item.get("field") for item in plots.get("series", []) if isinstance(item, Mapping) and item.get("field")]
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    event_html = "".join(
        f'<tr><td>{_escape("故障" if item.get("kind") == "fault" else "退化" if item.get("kind") == "degradation" else "运行约束" if item.get("kind") == "constraint" else item.get("kind"))}</td><td>{_escape(EFFECT_LABELS_ZH.get(str(item.get("effect_id") or item.get("type") or item.get("fault_type") or item.get("degradation_type") or item.get("constraint_type")), (str(item.get("effect_id") or item.get("type") or item.get("fault_type") or item.get("degradation_type") or item.get("constraint_type")), ""))[0])}</td><td>{_escape(item.get("target"))}</td><td>{_escape(item.get("start_time_s", 0))}</td><td>{_escape(item.get("end_time_s", "—"))}</td></tr>'
        for item in declared_events if isinstance(item, Mapping)
    ) or '<tr><td colspan="5" class="empty">未声明故障、退化或运行约束事件</td></tr>'
    assertion_html = "".join(
        f'<tr><td>{_escape(item.get("metric"))}</td><td>{_escape(item.get("operator"))}</td><td>{_escape(item.get("expected"))}</td><td>{_escape(item.get("actual"))}</td><td class="status {str(item.get("status", "")).lower()}">{_escape(_status_zh(item.get("status")))}</td></tr>'
        for item in assertion_rows if isinstance(item, Mapping)
    ) or '<tr><td colspan="5" class="empty">未配置自动验收断言</td></tr>'

    html_text = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_escape(task.get("name") or root.name)} · 仿真报告</title>
<style>
:root{{--ink:#162a35;--muted:#617682;--line:#d8e2e8;--accent:#087f67;--accent2:#176fc1;--bg:#f2f6f8}}
*{{box-sizing:border-box}}body{{margin:0;font-family:"Microsoft YaHei","Noto Sans SC",system-ui,sans-serif;color:var(--ink);background:var(--bg);line-height:1.6}}
.page{{max-width:1120px;margin:24px auto;background:white;border:1px solid var(--line);border-radius:16px;box-shadow:0 18px 50px rgba(31,61,76,.12);overflow:hidden}}
.hero{{padding:34px 38px;background:linear-gradient(135deg,#0d3a47,#087f67);color:white}}.hero h1{{margin:5px 0 6px;font-size:28px}}.hero p{{margin:0;opacity:.82}}
.content{{padding:28px 38px 40px}}section{{margin:0 0 28px}}h2{{font-size:18px;border-left:4px solid var(--accent);padding-left:10px;margin:0 0 12px}}h3{{font-size:14px;margin:14px 0 8px}}
.summary{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:-20px 28px 28px;position:relative}}.card{{background:white;border:1px solid var(--line);border-radius:10px;padding:14px;box-shadow:0 8px 22px rgba(31,61,76,.08)}}.card span{{display:block;color:var(--muted);font-size:12px}}.card strong{{display:block;margin-top:5px;font-size:15px;overflow-wrap:anywhere}}
table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{border:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}}th{{background:#f4f8fa}}table.kv th{{width:30%}}.empty{{color:var(--muted);text-align:center}}.status.pass{{color:#087f67;font-weight:700}}.status.fail{{color:#b12e3e;font-weight:700}}
.legend{{display:flex;flex-wrap:wrap;gap:12px;margin-top:7px;font-size:11px;color:var(--muted)}}.legend span{{display:flex;align-items:center;gap:5px}}.legend i{{width:12px;height:3px;border-radius:2px}}
.boundary{{border:1px solid #e2c98f;background:#fff9e8;border-radius:10px;padding:12px 14px;color:#735516;font-size:12px}}footer{{color:var(--muted);font-size:11px;padding-top:16px;border-top:1px solid var(--line)}}
@media(max-width:760px){{.summary{{grid-template-columns:1fr 1fr}}.content,.hero{{padding-left:18px;padding-right:18px}}}}
@media print{{body{{background:white}}.page{{max-width:none;margin:0;border:0;box-shadow:none}}.hero{{print-color-adjust:exact;-webkit-print-color-adjust:exact}}@page{{size:A4;margin:13mm}}}}
</style></head><body><article class="page">
<header class="hero"><div>卫星仿真平台 · 自动报告</div><h1>{_escape(task.get("name") or root.name)}</h1><p>Run ID：{_escape(root.name)} · 生成时间：{_escape(generated_at)}</p></header>
<div class="summary">
<div class="card"><span>运行状态</span><strong>{_escape(_status_zh(run_record.get("status")))}</strong></div>
<div class="card"><span>验证结果</span><strong>{_escape(_status_zh(validation.get("result")))}</strong></div>
<div class="card"><span>主要能力</span><strong>{_escape(model.get("capability_id") or simulation.get("capability_id") or "—")}</strong></div>
<div class="card"><span>自动验收</span><strong>{_escape(_status_zh(assertions.get("status", "NOT_CONFIGURED")))}</strong></div>
</div><div class="content">
<section><h2>一、任务与运行配置</h2>{_table([("任务 ID", task.get("task_id")),("任务名称",task.get("name")),("仿真时长/s",simulation.get("duration_s")),("运行状态",_status_zh(run_record.get("status"))),("终止原因",run_record.get("terminal_reason_code")),("验证原因",validation.get("reason_code"))])}</section>
<section><h2>二、核心指标</h2>{_table(_metric_rows(metrics))}</section>
<section><h2>三、故障、退化与运行约束事件</h2><table><thead><tr><th>类别</th><th>效果</th><th>目标</th><th>开始/s</th><th>结束/s</th></tr></thead><tbody>{event_html}</tbody></table></section>
<section><h2>四、自动验收</h2><table><thead><tr><th>指标</th><th>运算符</th><th>期望</th><th>实际</th><th>结果</th></tr></thead><tbody>{assertion_html}</tbody></table></section>
<section><h2>五、时序曲线</h2>{_svg_chart(root / "results" / "telemetry.csv", series)}</section>
<section><h2>六、环境与可复现性</h2><h3>运行环境</h3>{_table([("Python",(environment.get("python") or {}).get("version")),("操作系统",(environment.get("platform") or {}).get("platform")),("主能力",environment.get("primary_capability_id")),("Basilisk",dependencies.get("Basilisk") or dependencies.get("bsk") or "未安装/未记录"),("包版本",dependencies.get("satellite-simulation-platform") or "未记录")])}<h3>允许声明</h3>{_table(((index, _claim_label(value)) for index, value in enumerate(claim.get("allowed_claims") or [], 1)),empty="未声明")}</section>
<section><h2>七、模型边界</h2><div class="boundary">本报告只证明该 Run Bundle 在声明的软件、参数和能力边界内执行。除非另有标定证据，不应将结果解释为飞行级、认证级或已完成飞行数据相关性的数字孪生结论。</div></section>
<footer>报告 Schema：{REPORT_SCHEMA_VERSION} · Run Bundle：{_escape(root.name)} · 可使用浏览器“打印/另存为 PDF”生成 PDF 版本。</footer>
</div></article></body></html>'''
    output = root / "results" / "report.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html_text, encoding="utf-8")
    metadata = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_id": root.name,
        "generated_at": generated_at,
        "html": "results/report.html",
        "print_to_pdf_supported": True,
        "metric_count": len(_metric_rows(metrics, max_rows=10000)),
        "event_count": len(declared_events),
        "assertion_count": len(assertion_rows),
    }
    (root / "results" / "report_manifest.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata


__all__ = ["REPORT_SCHEMA_VERSION", "generate_run_report"]
