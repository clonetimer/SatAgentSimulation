"""Deterministic, leakage-aware feature extraction for diagnostic pipeline pairs.

The extractor only reads declared telemetry feature contracts. Simulator-truth
channels and label columns are never emitted as model inputs. Provisional
transformations that lack the physical parameters needed for a defensible
implementation are reported as blocked rather than approximated silently.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from statistics import fmean
from typing import Any, Mapping, Sequence

from .telemetry_feature_contract_library import TelemetryFeatureContract, get_telemetry_feature_contract

DIAGNOSTIC_FEATURE_VECTOR_SCHEMA_VERSION = "sat-sim.diagnostic-feature-vector.v1"


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes"}:
            return 1.0
        if normalized in {"false", "no"}:
            return 0.0
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _time_map(rows: Sequence[Mapping[str, Any]]) -> dict[float, Mapping[str, Any]]:
    result: dict[float, Mapping[str, Any]] = {}
    for row in rows:
        time_value = _number(row.get("time_s"))
        if time_value is not None:
            result[time_value] = row
    return result


def _paired_series(
    fault_rows: Sequence[Mapping[str, Any]],
    nominal_rows: Sequence[Mapping[str, Any]],
    channel: str,
    *,
    onset: float,
) -> list[tuple[float, float, float]]:
    fault = _time_map(fault_rows)
    nominal = _time_map(nominal_rows)
    values: list[tuple[float, float, float]] = []
    for time_value in sorted(set(fault).intersection(nominal)):
        if time_value < onset:
            continue
        fault_value = _number(fault[time_value].get(channel))
        nominal_value = _number(nominal[time_value].get(channel))
        if fault_value is not None and nominal_value is not None:
            values.append((time_value, fault_value, nominal_value))
    return values


def _summary(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "mean": None, "minimum": None, "maximum": None, "rms": None}
    return {
        "count": len(values),
        "mean": fmean(values),
        "minimum": min(values),
        "maximum": max(values),
        "rms": math.sqrt(fmean([item * item for item in values])),
    }


def _linear_slope(points: Sequence[tuple[float, float]]) -> float | None:
    if len(points) < 2:
        return None
    mean_x = fmean([item[0] for item in points])
    mean_y = fmean([item[1] for item in points])
    denominator = sum((x - mean_x) ** 2 for x, _ in points)
    if denominator <= 0.0:
        return None
    return sum((x - mean_x) * (y - mean_y) for x, y in points) / denominator


def _sha(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def extract_pair_features(
    *,
    fault_root: str | Path,
    nominal_root: str | Path,
    contract: TelemetryFeatureContract | None = None,
) -> dict[str, Any]:
    fault_root = Path(fault_root)
    nominal_root = Path(nominal_root)
    fault_contract = _read_json(fault_root / "astrograph" / "dataset_contract.json")
    nominal_contract = _read_json(nominal_root / "astrograph" / "dataset_contract.json")
    ground_truth = fault_contract.get("ground_truth") if isinstance(fault_contract.get("ground_truth"), Mapping) else {}
    fault_id = str(ground_truth.get("class_id") or "")
    contract = contract or get_telemetry_feature_contract(fault_id)
    onset = _number(ground_truth.get("onset_time_s"))
    if onset is None:
        raise ValueError("fault case must provide finite onset_time_s")
    fault_file = str((fault_contract.get("telemetry") or {}).get("file") or "")
    nominal_file = str((nominal_contract.get("telemetry") or {}).get("file") or "")
    fault_rows = _read_csv(fault_root / fault_file)
    nominal_rows = _read_csv(nominal_root / nominal_file)
    shared_columns = set(fault_rows[0]) & set(nominal_rows[0]) if fault_rows and nominal_rows else set()

    raw_results: list[dict[str, Any]] = []
    feature_vector: dict[str, float] = {}
    feature_channels: dict[str, str] = {}
    unavailable_required: list[str] = []
    for feature in contract.raw.get("raw_features") or []:
        feature_id = str(feature["feature_id"])
        channel = str(feature["channel"])
        role = str(feature["evidence_role"])
        declared_available = feature.get("availability") == "available"
        present = channel in shared_columns
        eligible = bool(feature.get("model_input_eligible")) and role != "simulator_truth"
        if str(channel).startswith("label."):
            eligible = False
        pairs = _paired_series(fault_rows, nominal_rows, channel, onset=onset) if present else []
        residuals = [fault_value - nominal_value for _, fault_value, nominal_value in pairs]
        result = {
            "feature_id": feature_id,
            "channel": channel,
            "evidence_role": role,
            "declared_availability": feature.get("availability"),
            "present_in_pair": present,
            "model_input_eligible": eligible,
            "post_fault_pair_residual_summary": _summary(residuals),
        }
        raw_results.append(result)
        if feature.get("required_for_formal_model_input") and (not declared_available or not present):
            unavailable_required.append(channel)
        if eligible and residuals:
            vector_id = f"raw.{feature_id}.paired_rms"
            value = math.sqrt(fmean([item * item for item in residuals]))
            feature_vector[vector_id] = value
            feature_channels[vector_id] = channel

    derived_results: list[dict[str, Any]] = []
    raw_by_id = {str(item["feature_id"]): item for item in contract.raw.get("raw_features") or []}
    for feature in contract.raw.get("derived_features") or []:
        feature_id = str(feature["feature_id"])
        transformation = str(feature.get("transformation") or "")
        inputs = [str(item) for item in feature.get("inputs") or []]
        result: dict[str, Any] = {
            "feature_id": feature_id,
            "transformation": transformation,
            "inputs": inputs,
            "model_input_eligible": bool(feature.get("model_input_eligible")),
            "status": "BLOCKED",
            "value": None,
            "reason": None,
        }
        channels = [str(raw_by_id[item]["channel"]) for item in inputs if item in raw_by_id]
        if len(channels) != len(inputs):
            result["reason"] = "unknown_input_feature"
        elif any(channel not in shared_columns for channel in channels):
            if "dynamic_consistency" in transformation:
                result["reason"] = "command_torque_contract_or_input_channel_missing_from_pair"
            else:
                result["reason"] = "input_channel_missing_from_pair"
        elif transformation == "paired_rms_residual_over_post_fault_window":
            values = _paired_series(fault_rows, nominal_rows, channels[0], onset=onset)
            residuals = [fault_value - nominal_value for _, fault_value, nominal_value in values]
            if residuals:
                result.update(status="COMPLETED", value=math.sqrt(fmean([item * item for item in residuals])))
            else:
                result["reason"] = "insufficient_paired_samples"
        elif transformation == "post_fault_linear_slope":
            fault_map = _time_map(fault_rows)
            points = []
            for time_value in sorted(fault_map):
                if time_value < onset:
                    continue
                value = _number(fault_map[time_value].get(channels[0]))
                if value is not None:
                    points.append((time_value, value))
            slope = _linear_slope(points)
            if slope is not None:
                result.update(status="COMPLETED", value=slope)
            else:
                result["reason"] = "insufficient_post_fault_samples"
        elif transformation in {"paired_dynamic_consistency_residual", "paired_dynamic_consistency_residual_with_frozen_actuator_contract"}:
            result["reason"] = "requires_reviewed_actuator_dynamics_and_command_torque_contract"
        else:
            result["reason"] = "unsupported_provisional_transformation"
        if result["status"] == "COMPLETED" and bool(feature.get("model_input_eligible")):
            feature_vector[f"derived.{feature_id}"] = float(result["value"])
            feature_channels[f"derived.{feature_id}"] = ",".join(channels)
        derived_results.append(result)

    blocked_derived = [item["feature_id"] for item in derived_results if item["status"] != "COMPLETED"]
    payload: dict[str, Any] = {
        "schema_version": DIAGNOSTIC_FEATURE_VECTOR_SCHEMA_VERSION,
        "fault_id": fault_id,
        "pair_id": ground_truth.get("pair_id"),
        "feature_contract_id": contract.contract_id,
        "fault_dataset_id": fault_contract.get("dataset_id"),
        "nominal_dataset_id": nominal_contract.get("dataset_id"),
        "onset_time_s": onset,
        "status": "COMPLETED" if not unavailable_required and not blocked_derived else "PARTIAL",
        "raw_feature_results": raw_results,
        "derived_feature_results": derived_results,
        "feature_vector": feature_vector,
        "feature_channels": feature_channels,
        "missing_required_model_channels": sorted(set(unavailable_required)),
        "blocked_derived_features": blocked_derived,
        "leakage_guard": {
            "simulator_truth_excluded": True,
            "label_prefixes_excluded": ["label."],
            "emitted_feature_count": len(feature_vector),
            "emitted_simulator_truth_count": 0,
        },
        "claim_boundary": {
            "features_are_engineering_candidates": True,
            "formal_model_binding_allowed": False,
            "blocked_transformations_must_not_be_imputed_with_fault_truth": True,
        },
    }
    payload["feature_vector_sha256"] = _sha(payload)
    return payload


__all__ = ["DIAGNOSTIC_FEATURE_VECTOR_SCHEMA_VERSION", "extract_pair_features"]
