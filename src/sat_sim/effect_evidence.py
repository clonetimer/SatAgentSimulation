"""V32 effect-to-QoI evidence profiles and deterministic runtime assertions.

The registry distinguishes four final states for every reviewed effect:

* ``VERIFIED_NATIVE_EFFECT``: the requested effect is implemented by the
  registered capability implementation and has direct, effect-specific runtime
  evidence.  The companion ``implementation`` field still distinguishes
  ``source_native`` from ``basilisk_native``; this classification is not a
  hardware-calibration or flight-validation claim.
* ``DELIVERY_ONLY_INCONCLUSIVE``: delivery can be audited, but the current
  output contract cannot prove the intended effect.
* ``PROJECT_PROXY``: a project-side proxy behavior is observable, but native
  physical-equivalence claims are forbidden.
* ``OUT_OF_SCOPE``: the effect is not executable and must be rejected during
  planning.

Runtime assertions deliberately inspect direct parameter/effect fields rather
than accepting the mere presence of a generic QoI as proof.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from typing import Any, Mapping, Sequence


EFFECT_EVIDENCE_VERSION = "v32.effect-evidence.v1"


class EffectEvidenceClassification(StrEnum):
    VERIFIED_NATIVE_EFFECT = "VERIFIED_NATIVE_EFFECT"
    DELIVERY_ONLY_INCONCLUSIVE = "DELIVERY_ONLY_INCONCLUSIVE"
    PROJECT_PROXY = "PROJECT_PROXY"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


@dataclass(frozen=True)
class EffectEvidenceProfile:
    capability_id: str
    effect_id: str
    kind: str
    classification: EffectEvidenceClassification
    implementation: str
    executable: bool
    evidence_fields: tuple[str, ...]
    assertions: tuple[Mapping[str, Any], ...]
    claim_limitations: tuple[str, ...]
    rationale: str


@dataclass(frozen=True)
class RuntimeEffectEvaluation:
    classification: EffectEvidenceClassification
    result: str
    reason_code: str
    evidence_fields_present: tuple[str, ...]
    evidence_fields_missing: tuple[str, ...]
    assertion_results: tuple[Mapping[str, Any], ...]
    limitations: tuple[str, ...]


def _registry_payload() -> dict[str, Any]:
    path = resources.files("sat_sim.evidence").joinpath("effect_evidence_v32.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != EFFECT_EVIDENCE_VERSION:
        raise ValueError(f"unsupported effect evidence schema: {payload.get('schema_version')!r}")
    return payload


def list_effect_evidence_profiles() -> tuple[EffectEvidenceProfile, ...]:
    profiles: list[EffectEvidenceProfile] = []
    for raw in _registry_payload().get("effects", []):
        profiles.append(EffectEvidenceProfile(
            capability_id=str(raw["capability_id"]),
            effect_id=str(raw["effect_id"]),
            kind=str(raw["kind"]),
            classification=EffectEvidenceClassification(str(raw["classification"])),
            implementation=str(raw.get("implementation") or "unknown"),
            executable=bool(raw.get("executable", True)),
            evidence_fields=tuple(str(item) for item in raw.get("evidence_fields", [])),
            assertions=tuple(dict(item) for item in raw.get("runtime_assertions", [])),
            claim_limitations=tuple(str(item) for item in raw.get("claim_limitations", [])),
            rationale=str(raw.get("rationale") or ""),
        ))
    return tuple(profiles)


def get_effect_evidence_profile(capability_id: str, effect_id: str) -> EffectEvidenceProfile | None:
    for profile in list_effect_evidence_profiles():
        if profile.capability_id == capability_id and profile.effect_id == effect_id:
            return profile
    return None


def _field_present(field: str, summary: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> bool:
    if field in summary and summary.get(field) is not None:
        return True
    qoi = summary.get("qoi") if isinstance(summary.get("qoi"), Mapping) else {}
    if field in qoi and qoi.get(field) is not None:
        return True
    return any(field in row and row.get(field) is not None for row in rows)


def _selected_rows(
    rows: Sequence[Mapping[str, Any]],
    scope: str,
    event_kind: str = "fault",
    effect_id: str | None = None,
) -> list[Mapping[str, Any]]:
    if scope in {"active_any", "active_all"}:
        label = {
            "fault": "label.fault_active",
            "degradation": "label.degradation_active",
            "constraint": "label.constraint_active",
        }.get(event_kind, "label.modifier_active")
        selected = [row for row in rows if row.get(label) is True]
        if effect_id:
            exact = []
            for row in selected:
                active = {part.strip() for part in str(row.get("event.active_effects") or "").split(",") if part.strip()}
                if not active or effect_id in active:
                    exact.append(row)
            selected = exact
        return selected
    if scope == "last":
        return [rows[-1]] if rows else []
    if scope == "first":
        return [rows[0]] if rows else []
    return list(rows)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _compare(left: Any, operator: str, right: Any, tolerance: float) -> bool:
    if operator in {"eq", "=="}:
        ln, rn = _number(left), _number(right)
        if ln is not None and rn is not None:
            return math.isclose(ln, rn, rel_tol=tolerance, abs_tol=tolerance)
        return left == right
    if operator in {"ne", "!="}:
        return not _compare(left, "eq", right, tolerance)
    ln, rn = _number(left), _number(right)
    if ln is None or rn is None:
        return False
    if operator in {"lt", "<"}:
        return ln < rn - tolerance
    if operator in {"le", "<="}:
        return ln <= rn + tolerance
    if operator in {"gt", ">"}:
        return ln > rn + tolerance
    if operator in {"ge", ">="}:
        return ln >= rn - tolerance
    raise ValueError(f"unsupported effect assertion operator: {operator}")


def _event_parameters(event: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(event, Mapping):
        return {}
    parameters = event.get("parameters")
    return parameters if isinstance(parameters, Mapping) else {}


def _template_value(value: Any, event: Mapping[str, Any] | None) -> Any:
    if not isinstance(value, str) or "{" not in value:
        return value
    context: dict[str, Any] = {}
    if isinstance(event, Mapping):
        context.update(event)
    context.update(_event_parameters(event))
    try:
        return value.format_map(context)
    except (KeyError, ValueError):
        return value


def _assertion_result(
    assertion: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    event_kind: str = "fault",
    *,
    effect_id: str | None = None,
    event: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    field = str(_template_value(assertion.get("field") or "", event))
    operator = str(assertion.get("operator") or "eq")
    scope = str(assertion.get("scope") or "any")
    tolerance = float(assertion.get("tolerance", 1.0e-12))
    selected = _selected_rows(rows, scope, event_kind, effect_id)
    right_field = str(_template_value(assertion["right_field"], event)) if assertion.get("right_field") is not None else None
    parameter_name = assertion.get("value_parameter")
    expected_value = assertion.get("value")
    if parameter_name is not None:
        expected_value = _event_parameters(event).get(str(parameter_name), assertion.get("default_value"))
    if expected_value is not None:
        expected_value = _template_value(expected_value, event)
        try:
            expected_value = float(expected_value)
        except (TypeError, ValueError):
            pass
    comparisons: list[bool] = []
    observed: list[dict[str, Any]] = []
    for row in selected:
        if field not in row:
            continue
        left = row.get(field)
        if right_field is not None:
            if right_field not in row:
                continue
            right = row.get(right_field)
        else:
            right = expected_value
        passed = _compare(left, operator, right, tolerance)
        comparisons.append(passed)
        observed.append({"time_s": row.get("time_s"), "left": left, "right": right, "passed": passed})
    aggregate_all = scope.endswith("_all") or scope == "all"
    passed = bool(comparisons) and (all(comparisons) if aggregate_all else any(comparisons))
    return {
        "assertion_id": str(assertion.get("id") or field),
        "field": field,
        "operator": operator,
        "right_field": right_field,
        "value": expected_value if assertion.get("right_field") is None else None,
        "scope": scope,
        "sample_count": len(comparisons),
        "passed": passed,
        "observed": observed[:8],
    }


def evaluate_runtime_effect(
    *,
    capability_id: str,
    effect_id: str,
    summary: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    delivery_ok: bool,
    event: Mapping[str, Any] | None = None,
) -> RuntimeEffectEvaluation | None:
    """Evaluate direct effect evidence for one registered V32 profile."""

    profile = get_effect_evidence_profile(capability_id, effect_id)
    if profile is None:
        return None
    resolved_fields = tuple(str(_template_value(field, event)) for field in profile.evidence_fields)
    present = tuple(field for field in resolved_fields if _field_present(field, summary, rows))
    missing = tuple(field for field in resolved_fields if field not in present)

    if profile.classification == EffectEvidenceClassification.OUT_OF_SCOPE or not profile.executable:
        return RuntimeEffectEvaluation(
            classification=profile.classification,
            result="INCONCLUSIVE",
            reason_code="EFFECT_OUT_OF_SCOPE",
            evidence_fields_present=present,
            evidence_fields_missing=missing,
            assertion_results=(),
            limitations=profile.claim_limitations,
        )
    if not delivery_ok:
        return RuntimeEffectEvaluation(
            classification=profile.classification,
            result="INCONCLUSIVE",
            reason_code="RUNTIME_INJECTION_UNVERIFIED",
            evidence_fields_present=present,
            evidence_fields_missing=missing,
            assertion_results=(),
            limitations=profile.claim_limitations,
        )
    if missing:
        return RuntimeEffectEvaluation(
            classification=profile.classification,
            result="INCONCLUSIVE",
            reason_code="EFFECT_EVIDENCE_MISSING",
            evidence_fields_present=present,
            evidence_fields_missing=missing,
            assertion_results=(),
            limitations=profile.claim_limitations,
        )
    results = tuple(
        _assertion_result(item, rows, profile.kind, effect_id=effect_id, event=event)
        for item in profile.assertions
    )
    if not results:
        return RuntimeEffectEvaluation(
            classification=profile.classification,
            result="INCONCLUSIVE",
            reason_code="EFFECT_ASSERTION_MISSING",
            evidence_fields_present=present,
            evidence_fields_missing=missing,
            assertion_results=results,
            limitations=profile.claim_limitations,
        )
    passed = all(bool(item.get("passed")) for item in results)
    if not passed:
        return RuntimeEffectEvaluation(
            classification=profile.classification,
            result="FAIL",
            reason_code="EFFECT_ASSERTION_FAILED",
            evidence_fields_present=present,
            evidence_fields_missing=missing,
            assertion_results=results,
            limitations=profile.claim_limitations,
        )
    reason = (
        "PROJECT_PROXY_EFFECT_VERIFIED"
        if profile.classification == EffectEvidenceClassification.PROJECT_PROXY
        else "RUNTIME_EFFECT_VERIFIED"
    )
    return RuntimeEffectEvaluation(
        classification=profile.classification,
        result="PASS",
        reason_code=reason,
        evidence_fields_present=present,
        evidence_fields_missing=missing,
        assertion_results=results,
        limitations=profile.claim_limitations,
    )
