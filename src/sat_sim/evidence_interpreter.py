"""Evidence-constrained interpretation for experiment results."""
from __future__ import annotations

import json
import math
import re
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .llm_backends import LLMCallConfig, LLMCallResult, TextLLMBackend, make_text_llm_backend

INTERPRETATION_SCHEMA_VERSION = "agent-evidence-interpretation.v1"
_NUMERIC_LITERAL = re.compile(r"(?<![A-Za-z_])[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_CAUSAL_TERMS = (
    "导致", "引起", "造成", "证明了", "because", "caused", "causes", "due to",
    "responsible for", "demonstrates that",
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceRef(_Strict):
    run_id: str = Field(min_length=1, max_length=300)
    metric: str | None = Field(default=None, max_length=200)


class Observation(_Strict):
    statement: str = Field(min_length=1, max_length=1000)
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=8)


class ComparisonClaim(_Strict):
    statement: str = Field(min_length=1, max_length=1000)
    baseline_run_id: str
    run_id: str
    metric: str
    delta: float


class Hypothesis(_Strict):
    statement: str = Field(min_length=1, max_length=1000)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=8)
    limitation: str = Field(min_length=1, max_length=1000)
    confidence: Literal["low", "medium"] = "low"


class NextExperiment(_Strict):
    objective: str = Field(min_length=1, max_length=1000)
    parameter_path: str = Field(min_length=1, max_length=300)
    values: list[Any] = Field(min_length=1, max_length=5)
    rationale: str = Field(min_length=1, max_length=1000)
    estimated_runs: int = Field(ge=1, le=5)


class UnresolvedItem(_Strict):
    item: str = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=1, max_length=1000)


class EvidenceInterpretation(_Strict):
    schema_version: Literal["agent-evidence-interpretation.v1"] = INTERPRETATION_SCHEMA_VERSION
    analysis_status: Literal["EVIDENCE_READY", "INSUFFICIENT_EVIDENCE"]
    observations: list[Observation] = Field(default_factory=list, max_length=24)
    comparisons: list[ComparisonClaim] = Field(default_factory=list, max_length=24)
    hypotheses: list[Hypothesis] = Field(default_factory=list, max_length=12)
    next_experiments: list[NextExperiment] = Field(default_factory=list, max_length=8)
    unresolved: list[UnresolvedItem] = Field(default_factory=list, max_length=24)


class InterpretationValidationError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"reason_code": self.reason_code, "message": self.message}


def _parse_json_text(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    fence = chr(96) * 3
    if text.startswith(fence):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip() == fence:
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise InterpretationValidationError("INTERPRETATION_NOT_OBJECT", "model output must be one JSON object")
    return value


def _reject_numeric_free_text(value: str, field: str) -> None:
    if _NUMERIC_LITERAL.search(str(value)):
        raise InterpretationValidationError(
            "UNSTRUCTURED_NUMERIC_CLAIM",
            f"{field} contains a numeric literal; use structured metric/delta fields",
        )


def _reject_causal_observation(value: str, field: str) -> None:
    lowered = str(value).lower()
    if any(term.lower() in lowered for term in _CAUSAL_TERMS):
        raise InterpretationValidationError(
            "CAUSAL_LANGUAGE_OUTSIDE_HYPOTHESIS",
            f"{field} uses causal language; causal mechanisms belong in hypotheses with limitations",
        )


def _indexes(evidence: Mapping[str, Any]):
    runs: dict[str, set[str]] = {}
    for row in evidence.get("runs") or []:
        if isinstance(row, Mapping) and row.get("run_id"):
            metrics = row.get("metrics") if isinstance(row.get("metrics"), Mapping) else {}
            runs[str(row["run_id"])] = {str(name) for name in metrics}
    comparisons: dict[tuple[str, str, str], float] = {}
    for item in evidence.get("comparisons") or []:
        if not isinstance(item, Mapping):
            continue
        deltas = item.get("metric_deltas") if isinstance(item.get("metric_deltas"), Mapping) else {}
        for metric, delta in deltas.items():
            if isinstance(delta, (int, float)) and not isinstance(delta, bool):
                key = (str(item.get("baseline_run_id") or ""), str(item.get("run_id") or ""), str(metric))
                comparisons[key] = float(delta)
    return runs, comparisons


def validate_interpretation(
    interpretation: EvidenceInterpretation,
    *,
    evidence: Mapping[str, Any],
    allowed_parameter_paths: Sequence[str] = (),
    parameter_options: Sequence[Mapping[str, Any]] = (),
) -> EvidenceInterpretation:
    runs, comparisons = _indexes(evidence)
    option_map = {
        str(item.get("path")): dict(item)
        for item in parameter_options
        if isinstance(item, Mapping) and item.get("path")
    }
    allowed_paths = {str(path) for path in allowed_parameter_paths} | set(option_map)
    expected_status = "EVIDENCE_READY" if evidence.get("evidence_ready") else "INSUFFICIENT_EVIDENCE"
    if interpretation.analysis_status != expected_status:
        raise InterpretationValidationError("INTERPRETATION_STATUS_MISMATCH", f"analysis_status must be {expected_status}")

    def check_ref(ref: EvidenceRef) -> None:
        if ref.run_id not in runs:
            raise InterpretationValidationError("UNKNOWN_RUN_REFERENCE", f"unknown run: {ref.run_id}")
        if ref.metric is not None and ref.metric not in runs[ref.run_id]:
            raise InterpretationValidationError("UNKNOWN_METRIC_REFERENCE", f"metric {ref.metric} unavailable for {ref.run_id}")

    for index, item in enumerate(interpretation.observations):
        _reject_numeric_free_text(item.statement, f"observations[{index}].statement")
        _reject_causal_observation(item.statement, f"observations[{index}].statement")
        for ref in item.evidence:
            check_ref(ref)

    for index, item in enumerate(interpretation.comparisons):
        _reject_numeric_free_text(item.statement, f"comparisons[{index}].statement")
        _reject_causal_observation(item.statement, f"comparisons[{index}].statement")
        key = (item.baseline_run_id, item.run_id, item.metric)
        if key not in comparisons:
            raise InterpretationValidationError("UNKNOWN_COMPARISON_REFERENCE", f"unknown comparison: {key}")
        if not math.isclose(item.delta, comparisons[key], rel_tol=1e-9, abs_tol=1e-12):
            raise InterpretationValidationError("COMPARISON_DELTA_MISMATCH", f"delta mismatch: {key}")

    for index, item in enumerate(interpretation.hypotheses):
        _reject_numeric_free_text(item.statement, f"hypotheses[{index}].statement")
        _reject_numeric_free_text(item.limitation, f"hypotheses[{index}].limitation")
        for ref in item.evidence:
            check_ref(ref)

    def check_candidate_value(path: str, value: Any) -> None:
        option = option_map.get(path)
        if not option:
            return
        field_type = str(option.get("type") or "")
        valid_type = (
            (field_type == "number" and isinstance(value, (int, float)) and not isinstance(value, bool))
            or (field_type == "integer" and isinstance(value, int) and not isinstance(value, bool))
            or (field_type == "boolean" and isinstance(value, bool))
            or (field_type == "string" and isinstance(value, str))
        )
        if field_type and not valid_type:
            raise InterpretationValidationError(
                "EXPERIMENT_VALUE_TYPE_MISMATCH",
                f"value for {path} does not match registered type {field_type}",
            )
        if option.get("enum") and value not in option["enum"]:
            raise InterpretationValidationError(
                "EXPERIMENT_VALUE_OUT_OF_SCHEMA",
                f"value for {path} is outside registered enum",
            )
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if option.get("minimum") is not None and float(value) < float(option["minimum"]):
                raise InterpretationValidationError(
                    "EXPERIMENT_VALUE_OUT_OF_SCHEMA",
                    f"value for {path} is below registered minimum",
                )
            if option.get("maximum") is not None and float(value) > float(option["maximum"]):
                raise InterpretationValidationError(
                    "EXPERIMENT_VALUE_OUT_OF_SCHEMA",
                    f"value for {path} exceeds registered maximum",
                )

    for index, item in enumerate(interpretation.next_experiments):
        _reject_numeric_free_text(item.objective, f"next_experiments[{index}].objective")
        _reject_numeric_free_text(item.rationale, f"next_experiments[{index}].rationale")
        if item.parameter_path not in allowed_paths:
            raise InterpretationValidationError("UNREGISTERED_EXPERIMENT_PARAMETER", f"unregistered parameter: {item.parameter_path}")
        for value in item.values:
            check_candidate_value(item.parameter_path, value)
        if item.estimated_runs != len(item.values):
            raise InterpretationValidationError("EXPERIMENT_RUN_ESTIMATE_MISMATCH", "estimated_runs must equal proposed values count")

    for index, item in enumerate(interpretation.unresolved):
        _reject_numeric_free_text(item.item, f"unresolved[{index}].item")
        _reject_numeric_free_text(item.reason, f"unresolved[{index}].reason")
    return interpretation


def interpretation_response_schema() -> dict[str, Any]:
    return EvidenceInterpretation.model_json_schema()


def build_interpretation_prompt(
    *,
    question: str,
    evidence: Mapping[str, Any],
    allowed_parameter_paths: Sequence[str],
    parameter_options: Sequence[Mapping[str, Any]] = (),
) -> str:
    bounded = {
        "schema_version": evidence.get("schema_version"),
        "experiment_id": evidence.get("experiment_id"),
        "evidence_ready": bool(evidence.get("evidence_ready")),
        "runs": evidence.get("runs") or [],
        "comparisons": evidence.get("comparisons") or [],
        "warnings": evidence.get("warnings") or [],
        "claim_policy": evidence.get("claim_policy") or {},
    }
    rules = [
        "Return one JSON object only.",
        "Use only run IDs, metrics and deltas in evidence.",
        "Do not put numeric literals in free-text fields.",
        "Hypotheses are not causal proof and confidence is low or medium only.",
        "Next experiments may use only allowed parameter paths and are proposals only.",
        "Never convert missing evidence into PASS or normal behavior.",
    ]
    return (
        "You are an evidence-constrained satellite simulation analyst.\n"
        + "USER_QUESTION:\n" + str(question).strip() + "\n"
        + "RULES_JSON:\n" + json.dumps(rules, ensure_ascii=False) + "\n"
        + "ALLOWED_PARAMETER_PATHS:\n" + json.dumps(list(allowed_parameter_paths), ensure_ascii=False) + "\n"
        + "PARAMETER_OPTIONS_JSON:\n" + json.dumps(list(parameter_options), ensure_ascii=False, sort_keys=True) + "\n"
        + "EVIDENCE_JSON:\n" + json.dumps(bounded, ensure_ascii=False, sort_keys=True)
    )


def deterministic_interpretation(evidence: Mapping[str, Any]) -> EvidenceInterpretation:
    observations: list[Observation] = []
    for row in evidence.get("runs") or []:
        if not isinstance(row, Mapping) or not row.get("run_id"):
            continue
        refs = [EvidenceRef(run_id=str(row["run_id"]), metric=str(name)) for name in (row.get("metrics") or {})][:8]
        if refs:
            observations.append(Observation(statement="该运行提供了可引用的结果指标。", evidence=refs))

    comparisons: list[ComparisonClaim] = []
    for item in evidence.get("comparisons") or []:
        if not isinstance(item, Mapping):
            continue
        for metric, delta in (item.get("metric_deltas") or {}).items():
            comparisons.append(ComparisonClaim(
                statement="该指标相对基线存在已计算差异。",
                baseline_run_id=str(item.get("baseline_run_id")),
                run_id=str(item.get("run_id")),
                metric=str(metric),
                delta=float(delta),
            ))

    unresolved = [
        UnresolvedItem(item="证据包存在未解决项。", reason=str(item.get("code") or "UNKNOWN"))
        for item in (evidence.get("warnings") or []) if isinstance(item, Mapping)
    ][:24]
    return EvidenceInterpretation(
        analysis_status="EVIDENCE_READY" if evidence.get("evidence_ready") else "INSUFFICIENT_EVIDENCE",
        observations=observations[:24],
        comparisons=comparisons[:24],
        hypotheses=[],
        next_experiments=[],
        unresolved=unresolved,
    )


def interpret_evidence(
    *,
    question: str,
    evidence: Mapping[str, Any],
    allowed_parameter_paths: Sequence[str] = (),
    parameter_options: Sequence[Mapping[str, Any]] = (),
    backend: TextLLMBackend | None = None,
    config: LLMCallConfig | None = None,
) -> dict[str, Any]:
    if backend is None and config is None:
        interpretation = validate_interpretation(
            deterministic_interpretation(evidence),
            evidence=evidence,
            allowed_parameter_paths=allowed_parameter_paths,
            parameter_options=parameter_options,
        )
        return {"ok": True, "mode": "deterministic", "interpretation": interpretation.model_dump(mode="json"), "model_call": None}

    model = backend or make_text_llm_backend(config)  # type: ignore[arg-type]
    prompt = build_interpretation_prompt(
        question=question,
        evidence=evidence,
        allowed_parameter_paths=allowed_parameter_paths,
        parameter_options=parameter_options,
    )
    result: LLMCallResult = model.generate_text(prompt)
    if not result.ok:
        raise InterpretationValidationError("MODEL_CALL_FAILED", result.error or "model call failed")
    try:
        interpretation = EvidenceInterpretation.model_validate(_parse_json_text(result.raw_text))
    except InterpretationValidationError:
        raise
    except Exception as exc:
        raise InterpretationValidationError("INTERPRETATION_SCHEMA_INVALID", str(exc)) from exc
    interpretation = validate_interpretation(
        interpretation,
        evidence=evidence,
        allowed_parameter_paths=allowed_parameter_paths,
        parameter_options=parameter_options,
    )
    return {"ok": True, "mode": "model", "interpretation": interpretation.model_dump(mode="json"), "model_call": result.to_dict()}


__all__ = [
    "EvidenceInterpretation",
    "InterpretationValidationError",
    "build_interpretation_prompt",
    "deterministic_interpretation",
    "interpret_evidence",
    "interpretation_response_schema",
    "validate_interpretation",
]
