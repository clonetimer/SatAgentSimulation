"""Deterministic evidence summaries for Agent experiment analysis.

This module converts immutable Run Bundle artifacts and ExperimentStore member
metadata into a bounded, machine-readable evidence package.  It deliberately
separates observations from causal interpretation: the LLM may explain these
records later, but it may not upgrade missing or unsealed evidence into a
verified physical claim.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

EXPERIMENT_EVIDENCE_SCHEMA_VERSION = "agent-experiment-evidence.v1"
TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _safe_run_root(runs_root: Path, run_id: str) -> Path:
    root = (runs_root / str(run_id)).resolve()
    base = runs_root.resolve()
    if root != base and base not in root.parents:
        raise ValueError(f"run_id escapes runs root: {run_id}")
    return root


def _metric_values(payload: Mapping[str, Any]) -> dict[str, Any]:
    nested = payload.get("metrics")
    if isinstance(nested, Mapping):
        return {str(key): value for key, value in nested.items()}
    return {
        str(key): value
        for key, value in payload.items()
        if key not in {"schema_version", "run_id", "generated_at", "metadata"}
    }


def _row_count(payload: Mapping[str, Any], *keys: str) -> int:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
        if isinstance(value, Mapping):
            return len(value)
    for key in ("count", "row_count", "event_count"):
        value = payload.get(key)
        if isinstance(value, int):
            return int(value)
    return 0


def _selected_metrics(values: Mapping[str, Any], requested_metrics: Sequence[str]) -> dict[str, Any]:
    if requested_metrics:
        names = [str(name) for name in requested_metrics if str(name).strip()]
    else:
        names = sorted(str(name) for name in values)[:64]
    return {name: values.get(name) for name in names if name in values}


def _numeric_delta(current: Any, baseline: Any) -> float | None:
    if isinstance(current, bool) or isinstance(baseline, bool):
        return None
    if not isinstance(current, (int, float)) or not isinstance(baseline, (int, float)):
        return None
    return float(current) - float(baseline)


def build_experiment_evidence(
    *,
    experiment_id: str,
    members: Sequence[Mapping[str, Any]],
    runs_root: str | Path,
    requested_metrics: Sequence[str] = (),
    integrity_checker: Callable[[Path], Any] | None = None,
) -> dict[str, Any]:
    """Build bounded evidence without executing or mutating a simulation."""

    base = Path(runs_root).resolve()
    rows: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    terminal_count = 0
    sealed_count = 0
    metrics_available_count = 0

    ordered = sorted(
        (dict(item) for item in members),
        key=lambda item: int(item.get("variant_index", 0)),
    )

    for member in ordered:
        variant_index = int(member.get("variant_index", 0))
        run_id = str(member.get("run_id") or "").strip()
        member_state = str(member.get("state") or "DRAFT")
        parameters = dict(member.get("parameters") or {})
        row: dict[str, Any] = {
            "variant_index": variant_index,
            "parameters": parameters,
            "run_id": run_id or None,
            "member_state": member_state,
            "terminal": member_state in TERMINAL_STATES,
            "sealed": False,
            "integrity": None,
            "validation_result": member.get("validation_result"),
            "assertion_status": member.get("assertion_status"),
            "metrics": {},
            "event_count": 0,
            "artifact_refs": {},
        }

        if member_state in TERMINAL_STATES:
            terminal_count += 1

        if not run_id:
            warnings.append({
                "code": "RUN_NOT_ASSIGNED",
                "variant_index": variant_index,
                "message": "Experiment variant has no run_id yet.",
            })
            rows.append(row)
            continue

        root = _safe_run_root(base, run_id)
        if not root.exists():
            warnings.append({
                "code": "RUN_BUNDLE_NOT_FOUND",
                "variant_index": variant_index,
                "run_id": run_id,
                "message": "Run Bundle directory is not available.",
            })
            rows.append(row)
            continue

        record = _json(root / "run_record.json")
        metrics_payload = _json(root / "results" / "metrics.json")
        events_payload = _json(root / "results" / "events.json")
        assertions_payload = _json(root / "results" / "assertions.json")
        provenance = _json(root / "input" / "provenance.json")
        validation = _json(root / "validation" / "validation_outcome.json")

        actual_state = str(record.get("status") or member_state)
        row["execution_state"] = actual_state
        row["terminal"] = actual_state in TERMINAL_STATES
        row["validation_result"] = (
            record.get("validation_result")
            or validation.get("result")
            or validation.get("validation_result")
            or row["validation_result"]
        )
        row["assertion_status"] = assertions_payload.get("status") or row["assertion_status"]

        values = _metric_values(metrics_payload)
        row["metrics"] = _selected_metrics(values, requested_metrics)
        if values:
            metrics_available_count += 1
        row["event_count"] = _row_count(events_payload, "events", "rows", "items")

        sealed = (root / "SEALED.json").is_file()
        row["sealed"] = sealed
        if sealed:
            sealed_count += 1
            if integrity_checker is not None:
                try:
                    row["integrity"] = integrity_checker(root)
                except Exception as exc:
                    row["integrity"] = {"ok": False, "error": str(exc)}
                    warnings.append({
                        "code": "RUN_BUNDLE_INTEGRITY_CHECK_FAILED",
                        "variant_index": variant_index,
                        "run_id": run_id,
                        "message": str(exc),
                    })
        elif row["terminal"]:
            warnings.append({
                "code": "TERMINAL_RUN_NOT_SEALED",
                "variant_index": variant_index,
                "run_id": run_id,
                "message": "Terminal run lacks SEALED.json and must not be treated as immutable evidence.",
            })

        if not values:
            warnings.append({
                "code": "METRICS_NOT_AVAILABLE",
                "variant_index": variant_index,
                "run_id": run_id,
                "message": "No metrics.json values are available for this run.",
            })

        row["provenance"] = {
            key: provenance.get(key)
            for key in (
                "source_request_id",
                "task_spec_sha256",
                "resolved_spec_sha256",
                "execution_plan_sha256",
                "capability_id",
                "captured_at",
            )
            if provenance.get(key) is not None
        }
        row["artifact_refs"] = {
            "task_spec": f"runs/{run_id}/input/task_spec.json",
            "provenance": f"runs/{run_id}/input/provenance.json",
            "run_record": f"runs/{run_id}/run_record.json",
            "metrics": f"runs/{run_id}/results/metrics.json" if metrics_payload else None,
            "events": f"runs/{run_id}/results/events.json" if events_payload else None,
            "assertions": f"runs/{run_id}/results/assertions.json" if assertions_payload else None,
            "validation": f"runs/{run_id}/validation/validation_outcome.json" if validation else None,
            "seal": f"runs/{run_id}/SEALED.json" if sealed else None,
        }
        rows.append(row)

    baseline = next((row for row in rows if row.get("metrics")), None)
    comparisons: list[dict[str, Any]] = []
    if baseline is not None:
        for row in rows:
            if row is baseline or not row.get("metrics"):
                continue
            deltas: dict[str, float] = {}
            for name, value in row["metrics"].items():
                delta = _numeric_delta(value, baseline["metrics"].get(name))
                if delta is not None:
                    deltas[name] = delta
            comparisons.append({
                "baseline_variant_index": baseline["variant_index"],
                "variant_index": row["variant_index"],
                "baseline_run_id": baseline.get("run_id"),
                "run_id": row.get("run_id"),
                "parameter_changes": row.get("parameters") or {},
                "metric_deltas": deltas,
                "interpretation_boundary": (
                    "Metric deltas are observations only. Causal attribution requires "
                    "controlled experimental assumptions and supporting evidence."
                ),
            })

    complete = bool(rows) and all(bool(row.get("terminal")) for row in rows)
    integrity_ready = all(
        (
            row.get("integrity") is None
            or not isinstance(row.get("integrity"), Mapping)
            or bool(row["integrity"].get("ok"))
        )
        for row in rows
    )
    evidence_ready = (
        complete
        and all(bool(row.get("sealed")) for row in rows)
        and all(bool(row.get("metrics")) for row in rows)
        and integrity_ready
    )
    return {
        "ok": True,
        "schema_version": EXPERIMENT_EVIDENCE_SCHEMA_VERSION,
        "experiment_id": str(experiment_id),
        "variant_count": len(rows),
        "terminal_count": terminal_count,
        "sealed_count": sealed_count,
        "metrics_available_count": metrics_available_count,
        "complete": complete,
        "evidence_ready": evidence_ready,
        "requested_metrics": [str(item) for item in requested_metrics if str(item).strip()],
        "runs": rows,
        "comparisons": comparisons,
        "warnings": warnings,
        "claim_policy": {
            "observation": "Allowed when directly backed by returned artifacts.",
            "comparison": "Allowed for matched metrics and explicitly shown variant parameters.",
            "causal_explanation": "Not verified by this evidence package alone.",
            "missing_evidence": "Must remain missing; do not infer PASS or normal behavior.",
        },
    }


__all__ = [
    "EXPERIMENT_EVIDENCE_SCHEMA_VERSION",
    "build_experiment_evidence",
]
