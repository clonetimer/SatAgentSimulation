"""Small deterministic statistics helpers for experiment reports."""
from __future__ import annotations

from math import sqrt
from typing import Any, Iterable


def _percentile(sorted_values: list[float], percentile: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * percentile
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    weight = rank - low
    return sorted_values[low] * (1.0 - weight) + sorted_values[high] * weight


def summarize_numeric_samples(values: Iterable[Any]) -> dict[str, Any]:
    numeric = [float(item) for item in values if isinstance(item, (int, float))]
    numeric.sort()
    if not numeric:
        return {"count": 0}
    mean = sum(numeric) / len(numeric)
    variance = sum((item - mean) ** 2 for item in numeric) / len(numeric)
    return {
        "count": len(numeric),
        "min": numeric[0],
        "max": numeric[-1],
        "mean": mean,
        "std": sqrt(variance),
        "p5": _percentile(numeric, 0.05),
        "p50": _percentile(numeric, 0.50),
        "p95": _percentile(numeric, 0.95),
    }


def summarize_result_metrics(
    rows: Iterable[dict[str, Any]],
    metric_names: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Aggregate numeric output metrics and assertion pass rate across runs."""
    materialized = list(rows)
    selected = list(metric_names or [])
    if not selected:
        names: set[str] = set()
        for row in materialized:
            metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
            names.update(str(name) for name, value in metrics.items() if isinstance(value, (int, float)) and not isinstance(value, bool))
        selected = sorted(names)
    stats: dict[str, Any] = {}
    for name in selected:
        values = []
        for row in materialized:
            metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
            value = metrics.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.append(value)
        stats[name] = summarize_numeric_samples(values)
    terminal = [row for row in materialized if row.get("status")]
    passed = [row for row in terminal if str(row.get("assertion_status") or "").upper() in {"PASS", "PASSED"}]
    return {
        "run_count": len(materialized),
        "terminal_run_count": len(terminal),
        "assertion_evaluated_count": sum(1 for row in terminal if row.get("assertion_status") is not None),
        "pass_rate": (len(passed) / len(terminal)) if terminal else None,
        "metrics": stats,
    }
