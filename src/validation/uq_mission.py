"""Whole-mission uncertainty quantification helpers.

V17 is a *UQ harness*, not an engineering calibration claim.  It propagates a
small, explicitly demo-only uncertainty design through the V15 whole-spacecraft
mission chain and reports sensitivity / quantile / timestep-convergence evidence.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import contextlib
import io
import json
import math
import random
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from whole_spacecraft.runner import run_whole_spacecraft_native_case
from whole_spacecraft.schemas import WholeSpacecraftConfig, WholeSpacecraftRunConfig

BATCH = "UQ-MISSION-VALIDATION-1"
SCHEMA_VERSION = "uq-mission-validation-v1.0"

QOI_NAMES: tuple[str, ...] = (
    "mission_delivered_bits",
    "mission_dropped_bits",
    "mission_generated_bits",
    "final_data_storage_bits",
    "min_soc",
    "final_soc",
    "final_attitude_error_deg",
    "max_downlink_delivered_bps",
    "mission_success",
)


@dataclass(frozen=True)
class UQParameterSpec:
    """One uncertain design variable for the demo UQ campaign."""

    parameter_id: str
    fields: tuple[str, ...]
    distribution: str
    lower: float
    upper: float
    unit: str = "dimensionless"
    transform: str = "linear"
    confidence: str = "demo_uq_assumption"
    source: str = "configs/uq/mission_uq_demo_v1.json"
    notes: str = "Demo uncertainty range; not ground calibrated."


@dataclass(frozen=True)
class UQDesign:
    """Configuration for a deterministic LHS-based UQ smoke campaign."""

    schema_version: str = SCHEMA_VERSION
    batch: str = BATCH
    design_id: str = "mission_uq_demo_v1"
    purpose: str = "demo uncertainty propagation harness for the V15 whole-mission closure path"
    qualification_level: str = "demo_uq_only"
    sample_method: str = "latin_hypercube"
    seed: int = 17017
    sample_count: int = 6
    duration_s: float = 80.0
    sample_s: float = 20.0
    parameters: tuple[UQParameterSpec, ...] = field(default_factory=tuple)
    parameter_profile: str = "demo"
    parameter_registry_path: str | None = None
    strict_parameter_provenance: bool = True
    not_claimed: tuple[str, ...] = (
        "engineering_uncertainty_bounds",
        "ground_calibrated_parameter_distributions",
        "flight_correlated_mission_probability",
        "validated_rf_link_margin_statistics",
    )


@dataclass(frozen=True)
class UQRunResult:
    sample_id: str
    sample_index: int
    factors: dict[str, float]
    sampled_values: dict[str, float]
    status: str
    mission_status: str
    physics_status: str
    numerical_status: str
    runtime_injection_status: str
    qoi: dict[str, float]


@dataclass(frozen=True)
class UQCampaignResult:
    schema_version: str
    batch: str
    design: dict[str, Any]
    status: str
    qualification_level: str
    sample_count: int
    successful_run_count: int
    mission_success_rate: float
    quantiles: dict[str, dict[str, float]]
    sensitivities: dict[str, dict[str, float]]
    identifiability: dict[str, Any]
    timestep_convergence: dict[str, Any]
    calibration_split: dict[str, Any]
    runs: tuple[dict[str, Any], ...]
    not_claimed: tuple[str, ...]


def default_parameter_specs() -> tuple[UQParameterSpec, ...]:
    """Return intentionally demo-only uncertainty ranges for current mainline QoI.

    Ranges are deliberately modest to exercise the pipeline without declaring
    that the bounds are hardware-derived.  V16 parameter provenance remains the
    authority for qualification level.
    """

    return (
        UQParameterSpec("whole.battery_capacity_wh", ("battery_capacity_wh",), "uniform_factor", 0.90, 1.10, unit="factor"),
        UQParameterSpec("whole.initial_soc", ("initial_soc",), "uniform_absolute", 0.56, 0.68, unit="dimensionless"),
        UQParameterSpec("whole.solar_power_w", ("solar_power_w",), "uniform_factor", 0.85, 1.15, unit="factor"),
        UQParameterSpec("whole.payload_power_w", ("payload_power_w",), "uniform_factor", 0.80, 1.20, unit="factor"),
        UQParameterSpec("whole.bus_power_w", ("bus_power_w",), "uniform_factor", 0.80, 1.20, unit="factor"),
        UQParameterSpec("whole.instrument_baud_bps", ("instrument_baud_bps",), "uniform_factor", 0.80, 1.20, unit="factor"),
        UQParameterSpec(
            "whole.downlink_rate_family",
            ("transmitter_baud_bps", "native_downlink_bit_rate_request_bps"),
            "uniform_factor",
            0.80,
            1.20,
            unit="factor",
        ),
        UQParameterSpec("whole.native_downlink_cnr_linear", ("native_downlink_cnr_linear",), "log_uniform_factor", 0.30, 3.00, unit="factor"),
        UQParameterSpec("whole.native_downlink_distance_m", ("native_downlink_distance_m",), "uniform_factor", 0.80, 1.20, unit="factor"),
        UQParameterSpec("whole.storage_capacity_bits", ("storage_capacity_bits",), "uniform_factor", 0.80, 1.20, unit="factor"),
        UQParameterSpec("whole.thermal_heat_power_w", ("thermal_heat_power_w",), "uniform_factor", 0.80, 1.20, unit="factor"),
    )


def default_design() -> UQDesign:
    return UQDesign(parameters=default_parameter_specs())


def write_default_design(path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    design = default_design()
    payload = asdict(design)
    payload["parameters"] = [asdict(p) for p in design.parameters]
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_design(path: str | Path | None = None) -> UQDesign:
    if path is None:
        return default_design()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    params = tuple(UQParameterSpec(**row) for row in payload.pop("parameters", []))
    return UQDesign(**payload, parameters=params)


def _unit_lhs(n: int, k: int, seed: int) -> list[list[float]]:
    if n <= 0 or k <= 0:
        raise ValueError("LHS dimensions must be positive")
    rng = random.Random(seed)
    columns: list[list[float]] = []
    for _ in range(k):
        values = [(i + rng.random()) / n for i in range(n)]
        rng.shuffle(values)
        columns.append(values)
    return [[columns[j][i] for j in range(k)] for i in range(n)]


def _sample_value(spec: UQParameterSpec, u: float) -> float:
    u = min(1.0, max(0.0, float(u)))
    if spec.distribution == "uniform_factor":
        return float(spec.lower) + u * (float(spec.upper) - float(spec.lower))
    if spec.distribution == "uniform_absolute":
        return float(spec.lower) + u * (float(spec.upper) - float(spec.lower))
    if spec.distribution == "log_uniform_factor":
        if spec.lower <= 0 or spec.upper <= 0:
            raise ValueError(f"log_uniform_factor requires positive bounds: {spec.parameter_id}")
        lo = math.log(float(spec.lower))
        hi = math.log(float(spec.upper))
        return math.exp(lo + u * (hi - lo))
    raise ValueError(f"unsupported UQ distribution {spec.distribution!r} for {spec.parameter_id}")


def apply_sample_to_structure(base: WholeSpacecraftConfig, specs: Iterable[UQParameterSpec], values: dict[str, float]) -> tuple[WholeSpacecraftConfig, dict[str, float]]:
    updates: dict[str, Any] = {}
    sampled_values: dict[str, float] = {}
    for spec in specs:
        draw = float(values[spec.parameter_id])
        for field_name in spec.fields:
            nominal = float(getattr(base, field_name))
            if spec.distribution in {"uniform_factor", "log_uniform_factor"}:
                value = nominal * draw
            else:
                value = draw
            if field_name == "initial_soc":
                value = min(0.95, max(0.05, value))
            if field_name.endswith("_bps") or field_name.endswith("_bits") or field_name.endswith("_w") or field_name.endswith("_wh"):
                value = max(0.0, value)
            updates[field_name] = value
            sampled_values[f"whole.{field_name}"] = value
    return replace(base, **updates), sampled_values


def _quiet_run(cfg: WholeSpacecraftRunConfig):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        return run_whole_spacecraft_native_case(cfg)


def _qoi_from_summary(summary: Any) -> dict[str, float]:
    return {
        "mission_delivered_bits": float(summary.mission_delivered_bits),
        "mission_dropped_bits": float(summary.mission_dropped_bits),
        "mission_generated_bits": float(summary.mission_generated_bits),
        "final_data_storage_bits": float(summary.final_data_storage_bits),
        "min_soc": float(summary.min_soc),
        "final_soc": float(summary.final_soc),
        "final_attitude_error_deg": float(summary.final_attitude_error_deg),
        "max_downlink_delivered_bps": float(summary.max_downlink_delivered_bps),
        "mission_success": 1.0 if str(summary.mission_status) == "PASS" else 0.0,
    }


def _quantile(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    xs = sorted(float(v) for v in values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * p
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return xs[lo]
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def quantile_table(runs: list[UQRunResult]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for name in QOI_NAMES:
        values = [row.qoi[name] for row in runs]
        result[name] = {
            "p05": _quantile(values, 0.05),
            "p50": _quantile(values, 0.50),
            "p95": _quantile(values, 0.95),
            "mean": mean(values) if values else float("nan"),
            "min": min(values) if values else float("nan"),
            "max": max(values) if values else float("nan"),
        }
    return result


def _ranks(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = rank
        i = j + 1
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float:
    if len(xs) != len(ys) or len(xs) < 3:
        return 0.0
    mx = mean(xs)
    my = mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs)
    dy = sum((y - my) ** 2 for y in ys)
    if dx <= 0.0 or dy <= 0.0:
        return 0.0
    return num / math.sqrt(dx * dy)


def spearman_sensitivities(design: UQDesign, runs: list[UQRunResult]) -> dict[str, dict[str, float]]:
    sensitivities: dict[str, dict[str, float]] = {}
    for spec in design.parameters:
        x = [row.factors[spec.parameter_id] for row in runs]
        xr = _ranks(x)
        sensitivities[spec.parameter_id] = {}
        for qoi in QOI_NAMES:
            y = [row.qoi[qoi] for row in runs]
            yr = _ranks(y)
            sensitivities[spec.parameter_id][qoi] = _pearson(xr, yr)
    return sensitivities


def identifiability_summary(sensitivities: dict[str, dict[str, float]]) -> dict[str, Any]:
    per_qoi: dict[str, Any] = {}
    for qoi in QOI_NAMES:
        ranked = sorted(
            ((pid, abs(vals.get(qoi, 0.0)), vals.get(qoi, 0.0)) for pid, vals in sensitivities.items()),
            key=lambda item: item[1],
            reverse=True,
        )
        top = ranked[:3]
        per_qoi[qoi] = {
            "top_parameters": [{"parameter_id": pid, "abs_spearman": abs_rho, "spearman": rho} for pid, abs_rho, rho in top],
            "identifiability_status": "screening_only_low_sample_count",
            "notes": "Spearman screening indicates influence, not unique physical identifiability.",
        }
    return {
        "status": "screening_only",
        "method": "rank_correlation_sensitivity",
        "sample_count_limit": "demo smoke sample; not sufficient for final identifiability claims",
        "per_qoi": per_qoi,
    }


def timestep_convergence(base_structure: WholeSpacecraftConfig, *, duration_s: float = 80.0, sample_steps: tuple[float, ...] = (20.0, 10.0)) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    for sample_s in sample_steps:
        summary, _rows = _quiet_run(WholeSpacecraftRunConfig(duration_s=duration_s, sample_s=sample_s, structure=base_structure))
        runs.append({
            "sample_s": float(sample_s),
            "status": summary.status,
            "mission_status": summary.mission_status,
            "qoi": _qoi_from_summary(summary),
        })
    comparisons: list[dict[str, Any]] = []
    for coarse, fine in zip(runs, runs[1:]):
        diffs: dict[str, float] = {}
        rels: dict[str, float] = {}
        for qoi in ("mission_delivered_bits", "min_soc", "final_attitude_error_deg", "final_data_storage_bits"):
            a = float(coarse["qoi"][qoi])
            b = float(fine["qoi"][qoi])
            diffs[qoi] = b - a
            denom = max(abs(b), abs(a), 1.0)
            rels[qoi] = abs(b - a) / denom
        comparisons.append({"coarse_sample_s": coarse["sample_s"], "fine_sample_s": fine["sample_s"], "absolute_difference": diffs, "relative_difference": rels})
    worst_rel = max((v for comp in comparisons for v in comp["relative_difference"].values()), default=0.0)
    return {
        "status": "PASS" if worst_rel <= 0.35 else "REVIEW",
        "method": "recorder_sample_cadence_refinement",
        "duration_s": float(duration_s),
        "sample_steps_s": list(sample_steps),
        "worst_relative_difference": worst_rel,
        "runs": runs,
        "comparisons": comparisons,
        "notes": "This checks output sampling / mission-gate cadence sensitivity, not integrator-step convergence.",
    }


def run_uq_campaign(design: UQDesign | None = None, *, include_timestep_convergence: bool = True) -> UQCampaignResult:
    design = design or default_design()
    base_structure = WholeSpacecraftConfig(
        parameter_profile=str(design.parameter_profile),
        parameter_registry_path=design.parameter_registry_path,
        strict_parameter_provenance=bool(design.strict_parameter_provenance),
    )
    unit = _unit_lhs(int(design.sample_count), len(design.parameters), int(design.seed))
    runs: list[UQRunResult] = []
    for i, row in enumerate(unit):
        factors: dict[str, float] = {}
        for spec, u in zip(design.parameters, row):
            factors[spec.parameter_id] = _sample_value(spec, u)
        structure, sampled_values = apply_sample_to_structure(base_structure, design.parameters, factors)
        cfg = WholeSpacecraftRunConfig(duration_s=float(design.duration_s), sample_s=float(design.sample_s), structure=structure)
        summary, _rows = _quiet_run(cfg)
        qoi = _qoi_from_summary(summary)
        runs.append(UQRunResult(
            sample_id=f"lhs_{i:03d}",
            sample_index=i,
            factors=factors,
            sampled_values=sampled_values,
            status=str(summary.status),
            mission_status=str(summary.mission_status),
            physics_status=str(summary.physics_status),
            numerical_status=str(summary.numerical_status),
            runtime_injection_status=str(summary.runtime_injection_status),
            qoi=qoi,
        ))
    quantiles = quantile_table(runs)
    sensitivities = spearman_sensitivities(design, runs)
    success_rate = sum(row.qoi["mission_success"] for row in runs) / len(runs) if runs else 0.0
    timestep = timestep_convergence(base_structure, duration_s=float(design.duration_s)) if include_timestep_convergence else {"status": "SKIPPED"}
    all_finite = all(all(math.isfinite(float(v)) for v in row.qoi.values()) for row in runs)
    all_numeric_pass = all(row.numerical_status == "PASS" for row in runs)
    all_runtime_pass = all(row.runtime_injection_status == "PASS" for row in runs)
    status = "PASS" if runs and all_finite and all_numeric_pass and all_runtime_pass and timestep.get("status") in {"PASS", "SKIPPED"} else "REVIEW"
    if str(design.parameter_profile) == "engineering_estimate":
        calibration_split = {
            "status": "NOT_APPLICABLE_ENGINEERING_ESTIMATE_PROFILE",
            "calibration_dataset_count": 0,
            "validation_dataset_count": 0,
            "required_for": ["ground_calibrated", "flight_correlated"],
            "notes": "No ground/flight truth data were provided. UQ outputs use reviewed surrogate engineering-estimate priors only.",
        }
    else:
        calibration_split = {
            "status": "NOT_APPLICABLE_DEMO_PROFILE",
            "calibration_dataset_count": 0,
            "validation_dataset_count": 0,
            "required_for": ["ground_calibrated", "flight_correlated"],
            "notes": "No ground/flight truth data were provided. UQ outputs are demo uncertainty propagation only.",
        }
    return UQCampaignResult(
        schema_version=SCHEMA_VERSION,
        batch=BATCH,
        design={**asdict(design), "parameters": [asdict(p) for p in design.parameters]},
        status=status,
        qualification_level=str(design.qualification_level),
        sample_count=len(runs),
        successful_run_count=sum(1 for row in runs if row.status == "PASS"),
        mission_success_rate=success_rate,
        quantiles=quantiles,
        sensitivities=sensitivities,
        identifiability=identifiability_summary(sensitivities),
        timestep_convergence=timestep,
        calibration_split=calibration_split,
        runs=tuple(asdict(row) for row in runs),
        not_claimed=design.not_claimed,
    )


def write_uq_campaign_outputs(output_dir: str | Path, design_path: str | Path | None = None) -> dict[str, str]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    design = load_design(design_path)
    result = run_uq_campaign(design)
    summary_path = output_dir / "uq_mission_validation_1_summary.json"
    samples_path = output_dir / "uq_mission_validation_1_samples.csv"
    design_path_out = output_dir / "mission_uq_demo_v1.resolved.json"
    summary_path.write_text(json.dumps(asdict(result), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    design_path_out.write_text(json.dumps(result.design, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # Lightweight CSV table for external plotting.
    import csv
    fieldnames = ["sample_id", "status", "mission_status"] + [p.parameter_id for p in design.parameters] + list(QOI_NAMES)
    with samples_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in result.runs:
            writer.writerow({
                "sample_id": row["sample_id"],
                "status": row["status"],
                "mission_status": row["mission_status"],
                **row["factors"],
                **row["qoi"],
            })
    return {"summary": str(summary_path), "samples": str(samples_path), "design": str(design_path_out)}


__all__ = [
    "BATCH",
    "SCHEMA_VERSION",
    "UQParameterSpec",
    "UQDesign",
    "UQRunResult",
    "UQCampaignResult",
    "default_parameter_specs",
    "default_design",
    "write_default_design",
    "load_design",
    "apply_sample_to_structure",
    "run_uq_campaign",
    "write_uq_campaign_outputs",
]
