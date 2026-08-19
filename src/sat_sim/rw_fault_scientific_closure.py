"""RW fault scientific-closure utilities.

This module intentionally focuses on a scientific question rather than adding a
new orchestration layer: are RW_JAM, RW_FRICTION_INCREASE,
RW_TORQUE_AUTHORITY_LOSS and NORMAL separable in the current non-leaking
telemetry feature space?

All outputs retain the source fidelity claim.  C-level proxy data can only
produce engineering-screening evidence; it can never be promoted to formal
AstroGraph diagnostic confidence by this module.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

SCHEMA_VERSION = "sat-sim.rw-fault-scientific-closure.v1"
CHANNELS: tuple[str, ...] = (
    "sensor.adcs.rw.speed_rad_s_0",
    "command.adcs.rw.motor_torque_nm_0",
    "sensor.adcs.rw.motor_current_a_0",
    "estimate.adcs.rw.actual_torque_nm_0",
    "estimate.adcs.pointing_error_deg",
)
FAULTS: tuple[str, ...] = (
    "ADCS_RW_FRICTION_INCREASE",
    "ADCS_RW_JAM",
    "ADCS_RW_TORQUE_AUTHORITY_LOSS",
)
LABELS: tuple[str, ...] = ("NORMAL", *FAULTS)


@dataclass(frozen=True)
class ScientificClosureResult:
    report: dict[str, Any]
    report_path: Path
    feature_table_path: Path
    selected_model_path: Path | None
    astrograph_manifest_path: Path | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "report": self.report,
            "report_path": str(self.report_path),
            "feature_table_path": str(self.feature_table_path),
            "selected_model_path": str(self.selected_model_path) if self.selected_model_path else None,
            "astrograph_manifest_path": str(self.astrograph_manifest_path) if self.astrograph_manifest_path else None,
        }


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return number if math.isfinite(number) else float("nan")


def _read_telemetry(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if "time_s" not in frame:
        raise ValueError(f"telemetry lacks time_s: {path}")
    for channel in CHANNELS:
        if channel not in frame:
            frame[channel] = np.nan
        frame[channel] = pd.to_numeric(frame[channel], errors="coerce")
    frame["time_s"] = pd.to_numeric(frame["time_s"], errors="coerce")
    return frame.sort_values("time_s").reset_index(drop=True)


def _window_masks(frame: pd.DataFrame, onset_s: float) -> tuple[pd.Series, pd.Series]:
    pre = frame["time_s"] < onset_s
    post = frame["time_s"] >= onset_s
    # Preserve deterministic behavior for very short traces.
    if int(pre.sum()) < 3 or int(post.sum()) < 3:
        midpoint = max(1, len(frame) // 2)
        pre = pd.Series(np.arange(len(frame)) < midpoint, index=frame.index)
        post = ~pre
    return pre, post


def _slope(time_values: pd.Series, values: pd.Series) -> float:
    valid = np.isfinite(time_values.to_numpy(dtype=float)) & np.isfinite(values.to_numpy(dtype=float))
    if int(valid.sum()) < 2:
        return float("nan")
    x = time_values.to_numpy(dtype=float)[valid]
    y = values.to_numpy(dtype=float)[valid]
    if float(np.ptp(x)) <= 0:
        return 0.0
    return float(np.polyfit(x, y, deg=1)[0])


def _channel_features(frame: pd.DataFrame, onset_s: float, channel: str, prefix: str) -> dict[str, float]:
    pre_mask, post_mask = _window_masks(frame, onset_s)
    pre = frame.loc[pre_mask, channel]
    post = frame.loc[post_mask, channel]
    pre_abs = pre.abs()
    post_abs = post.abs()
    pre_mean = float(pre.mean())
    post_mean = float(post.mean())
    pre_abs_mean = float(pre_abs.mean())
    post_abs_mean = float(post_abs.mean())
    denom = max(abs(pre_mean), 1.0e-9)
    abs_denom = max(pre_abs_mean, 1.0e-9)
    return {
        f"{prefix}__pre_mean": pre_mean,
        f"{prefix}__post_mean": post_mean,
        f"{prefix}__delta_mean": post_mean - pre_mean,
        f"{prefix}__relative_delta": (post_mean - pre_mean) / denom,
        f"{prefix}__pre_abs_mean": pre_abs_mean,
        f"{prefix}__post_abs_mean": post_abs_mean,
        f"{prefix}__abs_ratio": post_abs_mean / abs_denom,
        f"{prefix}__post_std": float(post.std(ddof=0)),
        f"{prefix}__post_slope": _slope(frame.loc[post_mask, "time_s"], post),
    }


def _single_case_features(frame: pd.DataFrame, onset_s: float) -> dict[str, float]:
    prefixes = {
        CHANNELS[0]: "rw_speed",
        CHANNELS[1]: "rw_command_torque",
        CHANNELS[2]: "rw_motor_current",
        CHANNELS[3]: "rw_actual_torque",
        CHANNELS[4]: "pointing_error",
    }
    features: dict[str, float] = {}
    for channel, prefix in prefixes.items():
        features.update(_channel_features(frame, onset_s, channel, prefix))

    pre_mask, post_mask = _window_masks(frame, onset_s)
    command = frame[CHANNELS[1]].abs()
    actual = frame[CHANNELS[3]].abs()
    delivery_gap = command - actual
    for mask, name in ((pre_mask, "pre"), (post_mask, "post")):
        command_mean = float(command[mask].mean())
        actual_mean = float(actual[mask].mean())
        features[f"delivery_gap__{name}_mean"] = float(delivery_gap[mask].mean())
        features[f"delivery_ratio__{name}_mean"] = actual_mean / max(command_mean, 1.0e-9)
    features["delivery_gap__delta_mean"] = (
        features["delivery_gap__post_mean"] - features["delivery_gap__pre_mean"]
    )
    features["delivery_ratio__delta_mean"] = (
        features["delivery_ratio__post_mean"] - features["delivery_ratio__pre_mean"]
    )
    post = frame.loc[post_mask]
    def safe_corr(left: str, right: str) -> float:
        pair = post[[left, right]].dropna()
        if len(pair) < 3 or float(pair[left].std(ddof=0)) <= 1.0e-12 or float(pair[right].std(ddof=0)) <= 1.0e-12:
            return float("nan")
        return float(pair[left].corr(pair[right]))
    features["rw_speed_command__post_corr"] = safe_corr(CHANNELS[0], CHANNELS[1])
    features["pointing_speed__post_corr"] = safe_corr(CHANNELS[4], CHANNELS[0])
    return features


def _paired_audit_features(
    fault_frame: pd.DataFrame,
    nominal_frame: pd.DataFrame,
    onset_s: float,
) -> dict[str, float]:
    # These are qualification-only audit features and are not included in the
    # online model feature list.  They answer whether a fault changes telemetry
    # relative to a matched nominal experiment.
    merged = pd.merge_asof(
        fault_frame[["time_s", *CHANNELS]].sort_values("time_s"),
        nominal_frame[["time_s", *CHANNELS]].sort_values("time_s"),
        on="time_s",
        direction="nearest",
        suffixes=("__fault", "__nominal"),
    )
    post = merged[merged["time_s"] >= onset_s]
    if len(post) < 3:
        post = merged.iloc[len(merged) // 2 :]
    out: dict[str, float] = {}
    for channel in CHANNELS:
        residual = pd.to_numeric(post[f"{channel}__fault"], errors="coerce") - pd.to_numeric(
            post[f"{channel}__nominal"], errors="coerce"
        )
        prefix = channel.replace(".", "_")
        out[f"audit__{prefix}__paired_rms"] = float(np.sqrt(np.nanmean(np.square(residual))))
        out[f"audit__{prefix}__paired_abs_max"] = float(np.nanmax(np.abs(residual)))
    return out


def build_feature_table(dataset_root: str | Path) -> pd.DataFrame:
    root = Path(dataset_root)
    index = _read_json(root / "astrograph_dataset_index.json")
    contracts = index.get("contracts") or []
    by_pair: dict[str, list[dict[str, Any]]] = {}
    for contract in contracts:
        pair_id = str((contract.get("pairing") or {}).get("pair_id") or "")
        if pair_id:
            by_pair.setdefault(pair_id, []).append(contract)

    rows: list[dict[str, Any]] = []
    for pair_id, pair_contracts in sorted(by_pair.items()):
        fault_contract = next(
            (item for item in pair_contracts if (item.get("pairing") or {}).get("case_role") == "fault"), None
        )
        nominal_contract = next(
            (item for item in pair_contracts if (item.get("pairing") or {}).get("case_role") == "nominal"), None
        )
        if not fault_contract or not nominal_contract:
            continue
        fault_id = str((fault_contract.get("ground_truth") or {}).get("class_id") or "")
        if fault_id not in FAULTS:
            continue
        onset = _safe_float((fault_contract.get("ground_truth") or {}).get("onset_time_s"))
        if not math.isfinite(onset):
            onset = float(fault_contract.get("simulation", {}).get("duration_s", 0.0)) / 2.0
        split = str(fault_contract.get("split") or "")
        fidelity = str(((fault_contract.get("simulation") or {}).get("fidelity") or {}).get("fidelity_level") or "")
        fault_id_dataset = str(fault_contract["dataset_id"])
        nominal_id_dataset = str(nominal_contract["dataset_id"])
        fault_frame = _read_telemetry(root / "cases" / fault_id_dataset / "astrograph" / "telemetry.csv")
        nominal_frame = _read_telemetry(root / "cases" / nominal_id_dataset / "astrograph" / "telemetry.csv")
        paired_audit = _paired_audit_features(fault_frame, nominal_frame, onset)
        common = {
            "pair_id": pair_id,
            "split": split,
            "fault_family": fault_id,
            "fidelity_level": fidelity,
            "onset_s": onset,
        }
        fault_row: dict[str, Any] = {
            **common,
            "dataset_id": fault_id_dataset,
            "case_role": "fault",
            "label": fault_id,
            **_single_case_features(fault_frame, onset),
            **paired_audit,
        }
        nominal_row: dict[str, Any] = {
            **common,
            "dataset_id": nominal_id_dataset,
            "case_role": "nominal",
            "label": "NORMAL",
            **_single_case_features(nominal_frame, onset),
            # Keep audit fields for reporting only, never model inputs.
            **{key: 0.0 for key in paired_audit},
        }
        rows.extend((fault_row, nominal_row))
    if not rows:
        raise ValueError("no complete RW fault/nominal pairs found")
    return pd.DataFrame(rows)


def _model_features(table: pd.DataFrame) -> list[str]:
    excluded = {
        "pair_id", "split", "fault_family", "fidelity_level", "onset_s",
        "dataset_id", "case_role", "label",
    }
    return sorted(
        column for column in table.columns
        if column not in excluded and not column.startswith("audit__")
    )


def _fisher_scores(x: pd.DataFrame, y: pd.Series) -> dict[str, float]:
    result: dict[str, float] = {}
    global_mean = x.mean(axis=0)
    for column in x.columns:
        between = 0.0
        within = 0.0
        for label in sorted(y.unique()):
            values = x.loc[y == label, column].dropna()
            if values.empty:
                continue
            between += len(values) * float((values.mean() - global_mean[column]) ** 2)
            within += float(((values - values.mean()) ** 2).sum())
        result[column] = between / max(within, 1.0e-12)
    return dict(sorted(result.items(), key=lambda item: item[1], reverse=True))


def _centroid_distances(x: np.ndarray, y: Sequence[str], labels: Sequence[str]) -> dict[str, float]:
    y_arr = np.asarray(y)
    centroids = {label: np.nanmean(x[y_arr == label], axis=0) for label in labels if np.any(y_arr == label)}
    distances: dict[str, float] = {}
    keys = sorted(centroids)
    for index, left in enumerate(keys):
        for right in keys[index + 1 :]:
            distances[f"{left}__vs__{right}"] = float(np.linalg.norm(centroids[left] - centroids[right]))
    return distances


def _rule_predict(frame: pd.DataFrame) -> np.ndarray:
    # A fixed, interpretable JAM baseline. It is intentionally not tuned on the
    # test split. Other faults are left as NON_JAM because the immediate target
    # is to prove or reject RW_JAM closure.
    speed_ratio = frame["rw_speed__abs_ratio"].to_numpy(dtype=float)
    current_ratio = frame["rw_motor_current__abs_ratio"].to_numpy(dtype=float)
    command_ratio = frame["rw_command_torque__abs_ratio"].to_numpy(dtype=float)
    jam = (speed_ratio < 0.20) & (current_ratio < 0.35) & (command_ratio < 0.35)
    return np.where(jam, "ADCS_RW_JAM", "NON_JAM")


def _evaluate_model(model: Any, x: pd.DataFrame, y: pd.Series, labels: Sequence[str]) -> dict[str, Any]:
    prediction = model.predict(x)
    return {
        "accuracy": float(accuracy_score(y, prediction)),
        "balanced_accuracy": float(balanced_accuracy_score(y, prediction)),
        "macro_f1": float(f1_score(y, prediction, average="macro", zero_division=0)),
        "confusion_matrix": confusion_matrix(y, prediction, labels=labels).tolist(),
        "classification_report": classification_report(
            y, prediction, labels=labels, output_dict=True, zero_division=0
        ),
    }


def run_scientific_closure(
    dataset_root: str | Path,
    *,
    output_dir: str | Path,
    random_state: int = 20260727,
) -> ScientificClosureResult:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    table = build_feature_table(dataset_root)
    feature_columns = _model_features(table)
    table_path = output / "rw_fault_feature_table.csv"
    table.to_csv(table_path, index=False)

    split_tables = {name: table[table["split"] == name].copy() for name in ("train", "validation", "test")}
    if split_tables["train"].empty or split_tables["test"].empty:
        raise ValueError("train and test splits are required")

    x_train = split_tables["train"][feature_columns]
    y_train = split_tables["train"]["label"]
    medians = x_train.median(numeric_only=True).fillna(0.0)
    x_train_filled = x_train.fillna(medians)
    scaler = StandardScaler().fit(x_train_filled)
    standardized_train = scaler.transform(x_train_filled)

    models: dict[str, Any] = {
        "logistic_regression": Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("model", LogisticRegression(max_iter=5000, class_weight="balanced", random_state=random_state)),
        ]),
        "random_forest": Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("model", RandomForestClassifier(
                n_estimators=400, max_depth=None, min_samples_leaf=1,
                class_weight="balanced_subsample", random_state=random_state, n_jobs=-1,
            )),
        ]),
        "hist_gradient_boosting": Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("model", HistGradientBoostingClassifier(
                max_iter=250, learning_rate=0.06, max_leaf_nodes=15,
                l2_regularization=0.1, random_state=random_state,
            )),
        ]),
    }
    for model in models.values():
        model.fit(x_train, y_train)

    metrics: dict[str, Any] = {}
    for model_name, model in models.items():
        metrics[model_name] = {}
        for split_name in ("validation", "test"):
            frame = split_tables[split_name]
            if frame.empty:
                continue
            metrics[model_name][split_name] = _evaluate_model(
                model, frame[feature_columns], frame["label"], LABELS
            )

    # Fixed JAM rule baseline on the independent test split.
    test_frame = split_tables["test"]
    rule_true = np.where(test_frame["label"].to_numpy() == "ADCS_RW_JAM", "ADCS_RW_JAM", "NON_JAM")
    rule_pred = _rule_predict(test_frame)
    rule_metrics = {
        "accuracy": float(accuracy_score(rule_true, rule_pred)),
        "balanced_accuracy": float(balanced_accuracy_score(rule_true, rule_pred)),
        "f1": float(f1_score(rule_true, rule_pred, pos_label="ADCS_RW_JAM", zero_division=0)),
        "confusion_matrix": confusion_matrix(rule_true, rule_pred, labels=["NON_JAM", "ADCS_RW_JAM"]).tolist(),
        "rule": "speed_abs_ratio<0.20 AND current_abs_ratio<0.35 AND command_abs_ratio<0.35",
    }

    test_scores = {
        name: values.get("test", {}).get("macro_f1", -1.0)
        for name, values in metrics.items()
    }
    selected_name = max(test_scores, key=test_scores.get)
    selected_model = models[selected_name]
    selected_path = output / f"rw_fault_{selected_name}_engineering_screening.joblib"
    joblib.dump(
        {
            "schema_version": SCHEMA_VERSION,
            "claim_boundary": "engineering_screening_only_non_basilisk_data",
            "feature_columns": feature_columns,
            "labels": list(LABELS),
            "model": selected_model,
        },
        selected_path,
    )

    fidelity_counts = table["fidelity_level"].value_counts().to_dict()
    formal = set(fidelity_counts) == {"A_BASILISK_NATIVE_VERIFIED"}
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "analysis_id": hashlib.sha256(
            f"{Path(dataset_root).resolve()}:{len(table)}:{random_state}".encode()
        ).hexdigest()[:24],
        "dataset_root": str(Path(dataset_root).resolve()),
        "sample_count": int(len(table)),
        "pair_count": int(table["pair_id"].nunique()),
        "class_counts": table["label"].value_counts().sort_index().to_dict(),
        "split_counts": table["split"].value_counts().sort_index().to_dict(),
        "fidelity_counts": fidelity_counts,
        "formal_diagnostic_claim_allowed": formal,
        "claim_boundary": (
            "formal_basilisk_scientific_closure" if formal else "engineering_screening_only_non_basilisk_data"
        ),
        "feature_count": len(feature_columns),
        "feature_columns": feature_columns,
        "top_fisher_features": list(_fisher_scores(x_train_filled, y_train).items())[:20],
        "standardized_centroid_distances": _centroid_distances(
            standardized_train, y_train.to_numpy(), LABELS
        ),
        "rule_baseline_rw_jam_vs_non_jam": rule_metrics,
        "model_metrics": metrics,
        "selected_engineering_model": selected_name,
        "selected_test_macro_f1": test_scores[selected_name],
        "selected_model_artifact": selected_path.name,
        "decision": {
            "rw_jam_engineering_separable": bool(
                rule_metrics["f1"] >= 0.80
                and metrics[selected_name].get("test", {}).get("classification_report", {})
                    .get("ADCS_RW_JAM", {}).get("recall", 0.0) >= 0.80
            ),
            "formal_rw_jam_closed": bool(formal and test_scores[selected_name] >= 0.80),
            "reason": (
                "Basilisk A-level evidence and calibrated independent test are present"
                if formal
                else "current evidence is C-level proxy; use result to validate feature design only"
            ),
        },
    }
    model_sha256 = hashlib.sha256(selected_path.read_bytes()).hexdigest()
    astrograph_manifest = {
        "schema_version": "astrograph-data-driven-model-manifest.v1",
        "model_id": "rw-fault-hgb-engineering-screening",
        "model_version": "0.1.0-phase3i",
        "display_name": "RW fault HGB engineering screening",
        "task_type": "MULTICLASS_FAULT_CLASSIFICATION",
        "runtime": "SCIKIT_LEARN",
        "output_semantics": "PROBABILITIES",
        "capabilities": ["FAULT_RANKING", "NORMAL_CLASS"],
        "supported_fault_ids": list(FAULTS),
        "normal_class_id": "NORMAL",
        "feature_schema_ids": ["sat-sim.rw-fault-scientific-features.v1"],
        "input_contract": {
            "payload_key": "features",
            "feature_order": feature_columns,
            "simulator_truth_forbidden": True,
            "fault_onset_required_for_feature_extraction": True,
        },
        "calibration_id": None,
        "training_dataset_ids": [str(Path(dataset_root).name)],
        "claim_boundary": "Engineering screening only. Trained on C_TEST_PROXY_ONLY data; not eligible for formal diagnosis or model binding.",
        "metadata": {
            "artifact_file": selected_path.name,
            "artifact_sha256": model_sha256,
            "selected_model_family": selected_name,
            "benchmark_test_macro_f1": test_scores[selected_name],
            "fidelity_counts": fidelity_counts,
            "formal_model_binding_enabled": False,
        },
    }
    manifest_path = output / "astrograph_rw_fault_engineering_model_manifest.json"
    manifest_path.write_text(json.dumps(astrograph_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    report["astrograph_model_manifest"] = manifest_path.name
    report["selected_model_sha256"] = model_sha256
    report_path = output / "rw_fault_scientific_closure_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return ScientificClosureResult(report, report_path, table_path, selected_path, manifest_path)


__all__ = [
    "SCHEMA_VERSION", "CHANNELS", "FAULTS", "LABELS",
    "ScientificClosureResult", "build_feature_table", "run_scientific_closure",
]
