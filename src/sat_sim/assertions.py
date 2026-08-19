"""Deterministic metric assertions for simulation acceptance checks."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isclose
from typing import Any, Mapping, Sequence

ASSERTION_SCHEMA_VERSION = "sat-sim.metric-assertions.v1"
_ALLOWED_OPERATORS = {"<", "<=", ">", ">=", "==", "!=", "within", "approx", "truthy", "falsy"}


def _lookup(metrics: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    if path in metrics:
        return True, metrics[path]
    cursor: Any = metrics
    parts = path.split(".")
    for index, part in enumerate(parts):
        if not isinstance(cursor, Mapping):
            return False, None
        remainder = ".".join(parts[index:])
        if remainder in cursor:
            return True, cursor[remainder]
        if part not in cursor:
            return False, None
        cursor = cursor[part]
    return True, cursor


@dataclass(frozen=True)
class AssertionResult:
    assertion_id: str
    metric: str
    operator: str
    expected: Any
    actual: Any
    status: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _compare(actual: Any, operator: str, expected: Any, tolerance: float) -> bool:
    if operator == "truthy":
        return bool(actual)
    if operator == "falsy":
        return not bool(actual)
    if operator == "within":
        if not isinstance(expected, Sequence) or isinstance(expected, (str, bytes)) or len(expected) != 2:
            raise ValueError("within requires a two-item [minimum, maximum] value")
        return float(expected[0]) <= float(actual) <= float(expected[1])
    if operator == "approx":
        return isclose(float(actual), float(expected), rel_tol=tolerance, abs_tol=tolerance)
    if operator == "<":
        return float(actual) < float(expected)
    if operator == "<=":
        return float(actual) <= float(expected)
    if operator == ">":
        return float(actual) > float(expected)
    if operator == ">=":
        return float(actual) >= float(expected)
    if operator == "==":
        return actual == expected
    if operator == "!=":
        return actual != expected
    raise ValueError(f"unsupported assertion operator: {operator}")


def normalize_assertions(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("assertions must be a list")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"assertions[{index}] must be an object")
        metric = str(item.get("metric") or "").strip()
        operator = str(item.get("operator") or "").strip()
        if not metric:
            raise ValueError(f"assertions[{index}].metric is required")
        if operator not in _ALLOWED_OPERATORS:
            raise ValueError(f"assertions[{index}].operator must be one of {sorted(_ALLOWED_OPERATORS)}")
        if operator not in {"truthy", "falsy"} and "value" not in item:
            raise ValueError(f"assertions[{index}].value is required")
        normalized.append({
            "id": str(item.get("id") or f"assertion_{index + 1:03d}"),
            "metric": metric,
            "operator": operator,
            "value": item.get("value"),
            "tolerance": float(item.get("tolerance", 1e-6)),
            "required": bool(item.get("required", True)),
        })
    return normalized


def evaluate_assertions(metrics: Mapping[str, Any], assertions: Any) -> dict[str, Any]:
    declared = normalize_assertions(assertions)
    results: list[AssertionResult] = []
    for item in declared:
        found, actual = _lookup(metrics, item["metric"])
        if not found:
            status = "FAIL" if item["required"] else "NOT_EVALUATED"
            results.append(AssertionResult(
                item["id"], item["metric"], item["operator"], item["value"], None, status,
                f"metric not found: {item['metric']}",
            ))
            continue
        try:
            passed = _compare(actual, item["operator"], item["value"], item["tolerance"])
            status = "PASS" if passed else "FAIL"
            message = f"actual={actual!r} {item['operator']} expected={item['value']!r}"
        except Exception as exc:
            status = "FAIL"
            message = str(exc)
        results.append(AssertionResult(
            item["id"], item["metric"], item["operator"], item["value"], actual, status, message,
        ))
    fail_count = sum(item.status == "FAIL" for item in results)
    pass_count = sum(item.status == "PASS" for item in results)
    not_evaluated_count = sum(item.status == "NOT_EVALUATED" for item in results)
    overall = "NOT_CONFIGURED" if not results else ("FAIL" if fail_count else ("PASS" if pass_count else "NOT_EVALUATED"))
    return {
        "schema_version": ASSERTION_SCHEMA_VERSION,
        "status": overall,
        "assertion_count": len(results),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "not_evaluated_count": not_evaluated_count,
        "results": [item.to_dict() for item in results],
    }


__all__ = ["ASSERTION_SCHEMA_VERSION", "AssertionResult", "normalize_assertions", "evaluate_assertions"]
