"""Build independent evaluation inputs from verified, sealed Run Bundles."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from .run_bundle import verify_run_bundle

MAX_EVALUATION_SAMPLES = 250_000
MAX_EVALUATION_CELLS = 1_000_000
MAX_EVALUATION_TELEMETRY_BYTES = 256 * 1024 * 1024
FORMULA_LABELS = {
    "sample_mean.v1": "平均值",
    "sample_rms.v1": "均方根",
    "max_abs.v1": "最大绝对值",
    "final.v1": "末值",
}


class EvaluationRunError(ValueError):
    def __init__(self, reason_code: str, status_code: int = 409) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.status_code = status_code


def _json(path: Path) -> dict[str, Any] | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def inspect_evaluation_run(root: Path, run_id: str) -> dict[str, Any]:
    """Only intact, sealed, successful and validated finite runs are eligible."""
    root = root.resolve()
    if not root.is_dir() or root.name != run_id:
        raise EvaluationRunError("EVALUATION_RUN_NOT_FOUND", 404)
    seal = root / "SEALED.json"
    if seal.is_symlink() or not seal.is_file():
        raise EvaluationRunError("EVALUATION_RUN_NOT_SEALED")
    integrity = verify_run_bundle(root)
    if not integrity.get("ok"):
        raise EvaluationRunError("EVALUATION_RUN_INTEGRITY_FAILED")
    record = _json(root / "run_record.json") or {}
    prepared = _json(root / "runtime" / "prepared_run.json") or {}
    validation = _json(root / "validation" / "validation_outcome.json") or {}
    if record.get("run_id") != run_id or str(record.get("status", "")).upper() != "SUCCEEDED":
        raise EvaluationRunError("EVALUATION_RUN_NOT_ELIGIBLE")
    validation_result = str(validation.get("result") or record.get("validation_result") or "").upper()
    if validation_result != "PASS":
        raise EvaluationRunError("EVALUATION_RUN_NOT_ELIGIBLE")
    capability_id = prepared.get("primary_capability_id") or record.get("primary_capability_id")
    if not isinstance(capability_id, str) or not capability_id:
        raise EvaluationRunError("EVALUATION_RUN_CAPABILITY_MISSING")
    telemetry = root / "results" / "telemetry.jsonl"
    if telemetry.is_symlink() or not telemetry.is_file():
        raise EvaluationRunError("EVALUATION_TELEMETRY_NOT_RECORDED")
    if telemetry.stat().st_size > MAX_EVALUATION_TELEMETRY_BYTES:
        raise EvaluationRunError("EVALUATION_TELEMETRY_TOO_LARGE", 413)
    return {
        "run_id": run_id, "run_status": "SUCCEEDED",
        "validation_result": validation_result, "capability_id": capability_id,
        "telemetry_path": telemetry, "integrity": integrity,
    }


def _walk(nodes: list[Mapping[str, Any]]):
    pending = list(nodes)
    while pending:
        node = pending.pop()
        if not isinstance(node, Mapping):
            continue
        yield node
        children = node.get("children")
        if isinstance(children, list):
            pending.extend(children)


def evaluation_source_definitions(tree: Mapping[str, Any], capability_id: str) -> list[dict[str, Any]]:
    """Expose only reviewed numeric process outputs for this run's capability."""
    roots = tree.get("roots")
    if not isinstance(roots, list):
        raise EvaluationRunError("EVALUATION_OBJECT_CATALOG_INVALID", 500)
    candidates: list[dict[str, Any]] = []
    for row in _walk(roots):
        if row.get("kind") != "observable" or row.get("source_capability_id") != capability_id:
            continue
        if row.get("value_type") not in {"number", "integer", "float", "int"}:
            continue
        owner, field, unit = row.get("owner_object_id"), row.get("source_field"), row.get("unit")
        if not all(isinstance(value, str) and value for value in (owner, field, unit)):
            continue
        identity = "|".join((owner, capability_id, field, unit))
        candidates.append({
            "source_id": hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
            "owner_object_id": owner, "source_capability_id": capability_id,
            "source_field": field, "display_name_zh": str(row.get("display_name_zh") or field),
            "unit": unit, "value_type": row.get("value_type"),
        })
    field_counts: dict[str, int] = {}
    for row in candidates:
        field_counts[row["source_field"]] = field_counts.get(row["source_field"], 0) + 1
    for row in candidates:
        row["ambiguous_owner"] = field_counts[row["source_field"]] > 1
    return sorted(candidates, key=lambda row: (row["display_name_zh"], row["source_field"]))


def inspect_recorded_sources(run: Mapping[str, Any], definitions: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    telemetry_path = Path(run["telemetry_path"])
    stats = {str(row["source_id"]): {"sample_count": 0, "missing_count": 0} for row in definitions}
    field_to_sources: dict[str, list[str]] = {}
    for row in definitions:
        field_to_sources.setdefault(str(row["source_field"]), []).append(str(row["source_id"]))
    first_time: float | None = None
    last_time: float | None = None
    previous_time: float | None = None
    total_rows = 0
    invalid_time_count = 0
    try:
        with telemetry_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                if total_rows >= MAX_EVALUATION_SAMPLES:
                    raise EvaluationRunError("EVALUATION_SAMPLE_LIMIT_EXCEEDED", 413)
                try:
                    row = json.loads(line)
                except (ValueError, TypeError) as exc:
                    raise EvaluationRunError("EVALUATION_TELEMETRY_INVALID") from exc
                if not isinstance(row, dict):
                    raise EvaluationRunError("EVALUATION_TELEMETRY_INVALID")
                time_value = row.get("time_s")
                if type(time_value) not in (int, float) or not math.isfinite(time_value):
                    invalid_time_count += 1
                else:
                    current_time = float(time_value)
                    if previous_time is not None and current_time <= previous_time:
                        invalid_time_count += 1
                    previous_time = current_time
                    first_time = current_time if first_time is None else first_time
                    last_time = current_time
                total_rows += 1
                for field, source_ids in field_to_sources.items():
                    value = row.get(field)
                    numeric = type(value) in (int, float) and math.isfinite(value)
                    for source_id in source_ids:
                        if numeric:
                            stats[source_id]["sample_count"] += 1
                        else:
                            stats[source_id]["missing_count"] += 1
    except EvaluationRunError:
        raise
    except OSError as exc:
        raise EvaluationRunError("EVALUATION_TELEMETRY_UNREADABLE") from exc
    output = []
    for definition in definitions:
        row = dict(definition)
        counts = stats[row["source_id"]]
        row.update(counts, total_samples=total_rows)
        row["time_range_s"] = [first_time, last_time] if first_time is not None and last_time is not None else None
        row["evaluable"] = bool(
            total_rows > 0 and counts["sample_count"] == total_rows
            and not row["ambiguous_owner"] and invalid_time_count == 0
            and first_time is not None and last_time is not None
        )
        if row["ambiguous_owner"]:
            reason = "同一遥测字段对应多个对象，暂不能确定唯一归属。"
        elif invalid_time_count:
            reason = "本次运行的时间记录不完整或顺序异常，暂不能用于评估。"
        elif counts["sample_count"] == 0:
            reason = "本次运行没有记录该参量。"
        elif counts["missing_count"]:
            reason = "部分采样缺少该参量，暂不能用于完整评估。"
        elif first_time is None or last_time is None:
            reason = "本次运行没有有效时间记录。"
        else:
            reason = None
        row["availability_reason"] = reason
        output.append(row)
    return output


def _formula_display_name(source_name: str, formula: str) -> str:
    label = FORMULA_LABELS.get(formula)
    if label is None:
        raise EvaluationRunError("EVALUATION_UNKNOWN_FORMULA", 422)
    return f"{source_name}（{label}）"


def build_recorded_evaluation(run: Mapping[str, Any], sources: list[Mapping[str, Any]],
                              request_plan: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve source IDs against the server catalog and evaluate archived samples."""
    from .performance_evaluation import evaluate_snapshot

    if request_plan.get("schema_version") != "evaluation-plan.v1":
        raise EvaluationRunError("EVALUATION_PLAN_VERSION", 422)
    metrics_request = request_plan.get("metrics")
    if not isinstance(metrics_request, list) or not metrics_request or len(metrics_request) > 64:
        raise EvaluationRunError("EVALUATION_INVALID_METRICS", 422)
    source_index = {str(row["source_id"]): row for row in sources}
    resolved_metrics: list[dict[str, Any]] = []
    selected_sources: dict[str, Mapping[str, Any]] = {}
    seen_metric_ids: set[str] = set()
    for metric in metrics_request:
        if not isinstance(metric, Mapping):
            raise EvaluationRunError("EVALUATION_INVALID_METRIC", 422)
        metric_id, source_id, formula = metric.get("metric_id"), metric.get("source_id"), metric.get("formula")
        if not isinstance(metric_id, str) or not metric_id.strip() or len(metric_id) > 100 or metric_id in seen_metric_ids:
            raise EvaluationRunError("EVALUATION_INVALID_METRIC_ID", 422)
        seen_metric_ids.add(metric_id)
        source = source_index.get(str(source_id))
        if source is None or not source.get("evaluable"):
            raise EvaluationRunError("EVALUATION_SOURCE_BINDING_MISMATCH", 422)
        selected_sources[metric_id] = source
        resolved_metrics.append({
            "metric_id": metric_id,
            "display_name_zh": _formula_display_name(str(source["display_name_zh"]), str(formula)),
            "series_id": metric_id, "owner_object_id": source["owner_object_id"],
            "source_capability_id": source["source_capability_id"],
            "source_field": source["source_field"], "unit": source["unit"],
            "formula": formula, "minimum_samples": metric.get("minimum_samples", 1),
            "limits": metric.get("limits"),
        })
    resolved_plan = {
        "schema_version": "evaluation-plan.v1",
        "plan_id": request_plan.get("plan_id"), "version": request_plan.get("version"),
        "display_name_zh": request_plan.get("display_name_zh"),
        "window_s": request_plan.get("window_s"),
        "max_sample_gap_s": request_plan.get("max_sample_gap_s"),
        "metrics": resolved_metrics,
    }
    declared_sample_counts = {
        int(source.get("total_samples", -1))
        for source in selected_sources.values()
        if type(source.get("total_samples")) is int
    }
    if len(declared_sample_counts) != 1 or any(
        type(source.get("total_samples")) is not int
        or source.get("total_samples") < 1
        or source.get("sample_count") != source.get("total_samples")
        for source in selected_sources.values()
    ):
        raise EvaluationRunError("EVALUATION_SOURCE_SAMPLE_COUNTS_INCONSISTENT", 409)
    declared_rows = next(iter(declared_sample_counts))
    if declared_rows > MAX_EVALUATION_SAMPLES:
        raise EvaluationRunError("EVALUATION_SAMPLE_LIMIT_EXCEEDED", 413)
    if declared_rows * len(selected_sources) > MAX_EVALUATION_CELLS:
        raise EvaluationRunError("EVALUATION_WORKLOAD_LIMIT_EXCEEDED", 413)
    series = {
        metric_id: {
            "owner_object_id": source["owner_object_id"],
            "source_capability_id": source["source_capability_id"],
            "source_field": source["source_field"], "unit": source["unit"],
            "times_s": [], "values": [],
        }
        for metric_id, source in selected_sources.items()
    }
    if not series:
        raise EvaluationRunError("EVALUATION_EMPTY_METRICS", 422)
    rows_count = 0
    try:
        with Path(run["telemetry_path"]).open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise EvaluationRunError("EVALUATION_TELEMETRY_INVALID", 422)
                rows_count += 1
                if rows_count > MAX_EVALUATION_SAMPLES:
                    raise EvaluationRunError("EVALUATION_SAMPLE_LIMIT_EXCEEDED", 413)
                for metric_id, source in selected_sources.items():
                    item = series[metric_id]
                    item["times_s"].append(row.get("time_s"))
                    item["values"].append(row.get(str(source["source_field"])))
    except EvaluationRunError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise EvaluationRunError("EVALUATION_TELEMETRY_INVALID", 422) from exc
    if rows_count == 0:
        raise EvaluationRunError("EVALUATION_TELEMETRY_EMPTY", 422)
    digest = hashlib.sha256()
    with Path(run["telemetry_path"]).open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
    telemetry_digest = digest.hexdigest()
    snapshot_id = hashlib.sha256(
        f"{run['run_id']}|{telemetry_digest}|{','.join(sorted(row['source_id'] for row in selected_sources.values()))}".encode("utf-8")
    ).hexdigest()[:24]
    snapshot = {
        "schema_version": "recorded-snapshot.v1", "run_id": run["run_id"],
        "snapshot_id": snapshot_id, "sealed": True,
        "run_status": run["run_status"], "series": series,
    }
    try:
        report = evaluate_snapshot(snapshot, resolved_plan)
    except ValueError as exc:
        raise EvaluationRunError(str(exc), 422) from exc
    report["source_validation_result"] = run["validation_result"]
    report["plan_name_zh"] = resolved_plan.get("display_name_zh") or resolved_plan["plan_id"]
    return report, resolved_plan
