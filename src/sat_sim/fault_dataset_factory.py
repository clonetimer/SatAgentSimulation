"""Deterministic fault-dataset campaign factory for AstroGraph.

The factory emits a standard sat-sim campaign TaskSpec and delegates execution
to :mod:`sat_sim.campaign`.  Every fault run is paired with a nominal run that
shares the same initial condition and controller parameters.
"""
from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .astrograph_dataset_contract import (
    AstroGraphCaseMetadata,
    build_astrograph_dataset_index,
    write_astrograph_case_package,
)
from .campaign import CampaignRunResult, run_campaign_spec
from .fault_experiment_templates import get_fault_experiment_template
from .fault_severity import build_fault_severity_descriptor
from .simulation_validation_agent import validate_dataset_pairs
from .diagnostic_validation_agent import validate_dataset_diagnostics
from .task_spec import write_json
from .simulation_fidelity import require_basilisk_runtime

FACTORY_SCHEMA_VERSION = "sat-sim.astrograph-fault-dataset-factory.v3"
SUPPORTED_FAULT_IDS: tuple[str, ...] = (
    "ADCS_RW_FRICTION_INCREASE",
    "ADCS_RW_JAM",
    "ADCS_RW_TORQUE_AUTHORITY_LOSS",
)


@dataclass(frozen=True)
class SplitCounts:
    train: int = 9
    validation: int = 3
    test: int = 3

    def __post_init__(self) -> None:
        if min(self.train, self.validation, self.test) < 0:
            raise ValueError("split counts must be non-negative")
        if self.train + self.validation + self.test <= 0:
            raise ValueError("at least one run is required")

    def items(self) -> tuple[tuple[str, int], ...]:
        return (("train", self.train), ("validation", self.validation), ("test", self.test))


@dataclass(frozen=True)
class FaultDatasetFactoryConfig:
    schema_version: str = FACTORY_SCHEMA_VERSION
    campaign_id: str = "astrograph_rw_fault_dataset_v1"
    base_seed: int = 20260725
    counts: SplitCounts = field(default_factory=SplitCounts)
    fault_ids: tuple[str, ...] = SUPPORTED_FAULT_IDS
    duration_s: float = 60.0
    sample_s: float = 1.0
    solver_step_s: float = 0.2
    capability_id: str = "subsystem.adcs_unified_native.v1"
    simulation_backend: str = "basilisk"
    allow_test_proxy: bool = False
    output_root: str = "datasets/astrograph_rw_basilisk_dataset_v1"

    def __post_init__(self) -> None:
        if self.schema_version != FACTORY_SCHEMA_VERSION:
            raise ValueError(f"unsupported factory schema: {self.schema_version}")
        unknown = sorted(set(self.fault_ids) - set(SUPPORTED_FAULT_IDS))
        if unknown:
            raise ValueError(f"unsupported fault IDs: {unknown}")
        if self.duration_s <= 0 or self.sample_s <= 0 or self.solver_step_s <= 0:
            raise ValueError("duration/sample/solver steps must be positive")
        if self.solver_step_s > self.sample_s:
            raise ValueError("solver_step_s must not exceed sample_s")
        formal = self.capability_id == "subsystem.adcs_unified_native.v1" and self.simulation_backend == "basilisk"
        if not formal and not self.allow_test_proxy:
            raise ValueError(
                "formal AstroGraph datasets must use subsystem.adcs_unified_native.v1 with backend=basilisk; "
                "set allow_test_proxy=true only for unit/interface tests"
            )

    @classmethod
    def from_json(cls, path: str | Path) -> "FaultDatasetFactoryConfig":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        counts = payload.pop("counts", {})
        return cls(counts=SplitCounts(**counts), fault_ids=tuple(payload.pop("fault_ids", SUPPORTED_FAULT_IDS)), **payload)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["fault_ids"] = list(self.fault_ids)
        return payload


@dataclass(frozen=True)
class FaultDatasetExecutionResult:
    campaign: CampaignRunResult
    dataset_index: dict[str, Any]
    index_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign": self.campaign.to_dict(),
            "dataset_index": self.dataset_index,
            "index_path": str(self.index_path),
        }


def _is_formal_basilisk(config: FaultDatasetFactoryConfig) -> bool:
    return config.capability_id == "subsystem.adcs_unified_native.v1" and config.simulation_backend == "basilisk"


def _base_parameters(config: FaultDatasetFactoryConfig) -> dict[str, Any]:
    if _is_formal_basilisk(config):
        return {
            "initial_pointing_error_deg": 12.0,
            "inclination_deg": 35.0,
            "orbit_radius_m": 7000000.0,
            "controller_k": 3.5,
            "controller_p": 30.0,
            "rw_max_torque_nm": 0.2,
            "rw_wheel_inertia_kg_m2": 0.015,
            "rw_motor_torque_constant_nm_per_a": 0.02,
            "rw_motor_current_limit_a": 10.0,
            "simulation_seed": config.base_seed,
            "gyro_base_noise_std_rad_s": 1.0e-5,
        }
    return {
        "initial_attitude_error_deg": 12.0,
        "initial_rate_deg_s": [0.1, -0.05, 0.02],
        "target_mode": "nadir",
        "wheel_configuration": "orthogonal_3",
        "initial_wheel_speed_rad_s": [60.0, 60.0, 60.0],
        "max_wheel_torque_nm": 0.05,
        "max_wheel_speed_rad_s": 900.0,
        "gyro_noise_std_deg_s": 0.001,
    }


def _record_fields(config: FaultDatasetFactoryConfig) -> list[str]:
    pointing_field = "adcs.pointing_error_deg" if _is_formal_basilisk(config) else "adcs.pointing.error_deg"
    return [
        "time_s",
        pointing_field,
        "label.fault_active",
        "label.degradation_active",
        "label.constraint_active",
        "adcs.event.active_effects",
        *[f"adcs.control.command_torque_nm_{i}" for i in range(3)],
        *[f"adcs.control.applied_torque_nm_{i}" for i in range(3)],
        *[f"adcs.rw.speed_rad_s_{i}" for i in range(3)],
        *[f"adcs.rw.command_torque_nm_{i}" for i in range(3)],
        *[f"adcs.rw.motor_current_a_{i}" for i in range(3)],
        *[f"adcs.rw.effective_max_torque_nm_{i}" for i in range(3)],
        *[f"adcs.rw.effective_friction_torque_nm_{i}" for i in range(3)],
        *[f"adcs.rw.effective_drag_nms_{i}" for i in range(3)],
    ]


def _base_spec(config: FaultDatasetFactoryConfig) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "task_id": f"{config.campaign_id}_base",
        "task_type": "subsystem",
        "description": "AstroGraph paired reaction-wheel dataset case",
        "capability_id": config.capability_id,
        "simulation": {
            "duration_s": config.duration_s,
            "sample_s": config.sample_s,
            "seed": config.base_seed,
            "backend": config.simulation_backend,
            "solver": {"step_s": config.solver_step_s},
        },
        "assurance": {"allow_proxy": bool(config.allow_test_proxy)},
        "target": {"level": "subsystem", "name": "adcs", "mode": "nominal"},
        "parameters": _base_parameters(config),
        "faults": [],
        "modifiers": {"faults": [], "degradations": [], "constraints": []},
        "outputs": {
            "output_root": "datasets/_astrograph_child",
            "trace_format": "csv",
            "include_summary": True,
            "include_trace": True,
            "include_labels": True,
            "include_manifest": True,
            "record_fields": _record_fields(config),
        },
        "metadata": {
            "integration_target": "AstroGraph",
            "dataset_contract": "sat-sim.astrograph-dataset.v2",
            "formal_dataset_path": not config.allow_test_proxy,
            "required_simulation_engine": "Basilisk" if not config.allow_test_proxy else "test_only",
            "claim_guardrail": "Basilisk-native engineering simulation data; not flight validated",
        },
    }


def _event_for_fault(
    fault_id: str,
    *,
    onset: float,
    severity: float,
    wheel_index: int,
    formal_basilisk: bool,
) -> tuple[str, dict[str, Any]]:
    target = f"adcs.reaction_wheel.{wheel_index}"
    common = {
        "modifier_id": f"{fault_id.lower()}_{wheel_index}",
        "target": target,
        "onset_time_s": onset,
        "duration_s": -1.0,
        "severity": severity,
    }
    if fault_id == "ADCS_RW_JAM":
        return "fault", {
            **common,
            "fault_type": "adcs_rw_jamming",
            "parameters": {"wheel_index": wheel_index},
        }
    if fault_id == "ADCS_RW_FRICTION_INCREASE":
        drag = 0.001 + 0.011 * severity
        return "degradation", {
            **common,
            "degradation_type": "rw_friction_degradation" if formal_basilisk else "adcs_rw_friction_increase",
            "parameters": ({"wheel_index": wheel_index, "drag_nms": round(drag, 8)}
                           if formal_basilisk
                           else {"wheel_index": wheel_index, "drag_torque_nm": round(drag, 8)}),
        }
    if fault_id == "ADCS_RW_TORQUE_AUTHORITY_LOSS":
        torque_scale = max(0.1, 1.0 - 0.8 * severity)
        return "degradation", {
            **common,
            "degradation_type": "adcs_rw_torque_authority_loss",
            "parameters": {"wheel_index": wheel_index, "torque_scale": round(torque_scale, 8)},
        }
    raise KeyError(fault_id)


def _case_parameters(
    rng: random.Random,
    *,
    pair_seed: int,
    formal_basilisk: bool,
) -> dict[str, Any]:
    if formal_basilisk:
        return {
            "initial_pointing_error_deg": round(rng.uniform(7.0, 18.0), 6),
            "inclination_deg": round(rng.uniform(20.0, 75.0), 6),
            "orbit_radius_m": round(rng.uniform(6_850_000.0, 7_250_000.0), 3),
            "controller_k": round(rng.uniform(2.8, 4.2), 8),
            "controller_p": round(rng.uniform(24.0, 38.0), 8),
            "rw_max_torque_nm": (rw_max := round(rng.uniform(0.15, 0.25), 8)),
            "rw_wheel_inertia_kg_m2": 0.015,
            "rw_motor_torque_constant_nm_per_a": 0.02,
            "rw_motor_current_limit_a": round(rw_max / 0.02, 8),
            "simulation_seed": pair_seed,
            "gyro_base_noise_std_rad_s": round(rng.uniform(5.0e-6, 2.0e-5), 12),
        }
    return {
        "initial_attitude_error_deg": round(rng.uniform(7.0, 18.0), 6),
        "initial_rate_deg_s": [round(rng.uniform(-0.15, 0.15), 8) for _ in range(3)],
        "initial_wheel_speed_rad_s": [round(rng.uniform(35.0, 120.0), 6) for _ in range(3)],
        "control_kp_nm_per_rad": round(rng.uniform(0.08, 0.13), 8),
        "control_kd_nm_per_rad_s": round(rng.uniform(0.7, 1.0), 8),
        "gyro_noise_std_deg_s": round(rng.uniform(0.0005, 0.002), 9),
    }


def build_fault_dataset_campaign(config: FaultDatasetFactoryConfig) -> dict[str, Any]:
    rng = random.Random(config.base_seed)
    cases: list[dict[str, Any]] = []
    global_index = 0
    for split, count in config.counts.items():
        for fault_id in config.fault_ids:
            template = get_fault_experiment_template(fault_id)
            if template.capability_id != config.capability_id or template.simulation_backend != config.simulation_backend:
                if not config.allow_test_proxy:
                    raise ValueError(
                        f"experiment template {template.template_id} requires "
                        f"{template.capability_id}/{template.simulation_backend}"
                    )
            for split_index in range(count):
                pair_id = f"{split}-{fault_id.lower()}-{split_index:03d}"
                wheel_index = rng.randrange(0, 3)
                onset = round(rng.uniform(config.duration_s * 0.25, config.duration_s * 0.55), 6)
                severity = (
                    float(template.severity_model.get("fixed_normalized_severity", 1.0))
                    if template.severity_model.get("mechanism_kind") == "binary_failure"
                    else round(rng.uniform(0.35, 1.0), 6)
                )
                pair_seed = config.base_seed + global_index
                parameters = _case_parameters(rng, pair_seed=pair_seed, formal_basilisk=_is_formal_basilisk(config))
                event_kind, event = _event_for_fault(
                    fault_id,
                    onset=onset,
                    severity=severity,
                    wheel_index=wheel_index,
                    formal_basilisk=_is_formal_basilisk(config),
                )
                severity_descriptor = build_fault_severity_descriptor(
                    fault_id=fault_id,
                    severity_model=template.severity_model,
                    normalized_severity=severity,
                    event_parameters=event.get("parameters") or {},
                ).to_dict()
                fault_case_id = f"{config.campaign_id}-{pair_id}-fault"
                nominal_case_id = f"{config.campaign_id}-{pair_id}-nominal"
                shared_metadata = {
                    "campaign_preserve_seed": True,
                    "experiment_template_id": template.template_id,
                    "experiment_template_schema": template.raw.get("schema_version"),
                    "experiment_expected_response": template.expected_response,
                    "experiment_qualification": template.qualification,
                    "fault_severity_model": template.severity_model,
                    "astrograph_split": split,
                    "astrograph_pair_id": pair_id,
                    "astrograph_fault_id": fault_id,
                    "astrograph_fault_case_id": fault_case_id,
                    "astrograph_nominal_case_id": nominal_case_id,
                    "astrograph_independent_run_id": f"{split}-{fault_id}-{split_index:03d}",
                }
                fault_modifiers = {"faults": [], "degradations": [], "constraints": []}
                fault_modifiers[f"{event_kind}s"] = [event]
                fault_mode = "fault" if event_kind == "fault" else "degradation"
                cases.append({
                    "case_id": fault_case_id,
                    "overrides": {
                        "simulation.seed": pair_seed,
                        "target.mode": fault_mode,
                        "parameters": parameters,
                        "modifiers": fault_modifiers,
                        "metadata": {
                            **shared_metadata,
                            "astrograph_case_role": "fault",
                            "experiment_id": f"{config.campaign_id}:{pair_id}:fault",
                            "simulator_effect": event.get("fault_type") or event.get("degradation_type"),
                            "simulator_event_kind": event_kind,
                            "onset_time_s": onset,
                            "end_time_s": None,
                            "severity": severity,
                            "fault_severity": severity_descriptor,
                            "target": event["target"],
                        },
                    },
                })
                cases.append({
                    "case_id": nominal_case_id,
                    "overrides": {
                        "simulation.seed": pair_seed,
                        "target.mode": "nominal",
                        "parameters": parameters,
                        "modifiers": {"faults": [], "degradations": [], "constraints": []},
                        "metadata": {
                            **shared_metadata,
                            "astrograph_case_role": "nominal",
                            "experiment_id": f"{config.campaign_id}:{pair_id}:nominal",
                            "simulator_effect": None,
                            "simulator_event_kind": None,
                            "onset_time_s": None,
                            "end_time_s": None,
                            "severity": None,
                            "fault_severity": None,
                            "target": event["target"],
                        },
                    },
                })
                global_index += 1

    return {
        "schema_version": "0.1.0",
        "task_id": config.campaign_id,
        "task_type": "campaign",
        "description": "Paired AstroGraph reaction-wheel fault dataset campaign",
        "simulation": {"seed": config.base_seed},
        "campaign": {
            "sampling": "manual",
            "base_spec": _base_spec(config),
            "cases": cases,
            "count": len(cases),
            "max_cases": len(cases),
        },
        "outputs": {
            "output_root": config.output_root,
            "include_summary": True,
            "include_manifest": True,
        },
        "metadata": {
            "factory_schema_version": config.schema_version,
            "factory_config": config.to_dict(),
            "pairing_policy": "same seed and parameters; one fault/degradation case and one nominal case",
            "simulation_engine_policy": "Basilisk required for formal datasets; proxy execution is test-only and explicit",
            "formal_dataset_path": not config.allow_test_proxy,
        },
    }


def _metadata_from_case(spec: Mapping[str, Any]) -> AstroGraphCaseMetadata:
    meta = spec.get("metadata") if isinstance(spec.get("metadata"), Mapping) else {}
    role = str(meta.get("astrograph_case_role"))
    fault_id = str(meta.get("astrograph_fault_id"))
    return AstroGraphCaseMetadata(
        split=str(meta.get("astrograph_split")),
        class_id=fault_id if role == "fault" else "NORMAL",
        pair_id=str(meta.get("astrograph_pair_id")),
        case_role=role,
        fault_case_id=str(meta.get("astrograph_fault_case_id")),
        nominal_case_id=str(meta.get("astrograph_nominal_case_id")),
        simulator_effect=meta.get("simulator_effect"),
        simulator_event_kind=meta.get("simulator_event_kind"),
        onset_time_s=meta.get("onset_time_s"),
        end_time_s=meta.get("end_time_s"),
        severity=meta.get("severity"),
        severity_descriptor=dict(meta.get("fault_severity") or {}),
        target=str(meta.get("target") or "adcs.reaction_wheel.0"),
        independent_run_id=str(meta.get("astrograph_independent_run_id")),
        metadata={
            "source_task_id": spec.get("task_id"),
            "experiment_id": meta.get("experiment_id"),
            "experiment_template_id": meta.get("experiment_template_id"),
        },
    )


def run_fault_dataset_campaign(
    campaign_spec: Mapping[str, Any],
    *,
    output_root: str | Path | None = None,
    continue_on_error: bool = True,
    dry_run: bool = False,
) -> FaultDatasetExecutionResult:
    campaign = campaign_spec.get("campaign") if isinstance(campaign_spec.get("campaign"), Mapping) else {}
    base_spec = campaign.get("base_spec") if isinstance(campaign.get("base_spec"), Mapping) else {}
    simulation = base_spec.get("simulation") if isinstance(base_spec.get("simulation"), Mapping) else {}
    metadata = campaign_spec.get("metadata") if isinstance(campaign_spec.get("metadata"), Mapping) else {}
    formal_path = bool(metadata.get("formal_dataset_path", True))
    if formal_path and not dry_run:
        if str(base_spec.get("capability_id")) != "subsystem.adcs_unified_native.v1" or str(simulation.get("backend")) != "basilisk":
            raise RuntimeError("formal AstroGraph dataset campaign must use the unified Basilisk ADCS capability")
        require_basilisk_runtime()

    result = run_campaign_spec(
        campaign_spec,
        output_root=output_root,
        continue_on_error=continue_on_error,
        dry_run=dry_run,
    )
    contracts: list[dict[str, Any]] = []
    if not dry_run:
        row_by_id = {str(row.get("case_id")): row for row in result.case_rows}
        for case in result.plan.cases:
            row = row_by_id.get(case.case_id, {})
            if row.get("status") != "complete":
                continue
            case_root = result.output_root / "cases" / case.case_id
            written = write_astrograph_case_package(case_root, _metadata_from_case(case.spec))
            relative = written.contract_path.relative_to(result.output_root).as_posix()
            written.contract["contract_file"] = relative
            contracts.append(written.contract)

    qualification_index: dict[str, Any] = {
        "schema_version": "sat-sim.dataset-qualification-index.v1",
        "pair_count": 0,
        "formal_training_qualified_pair_count": 0,
        "counts_by_decision": {},
        "reports": [],
    }
    diagnostic_index: dict[str, Any] = {
        "schema_version": "sat-sim.diagnostic-qualification-index.v1",
        "pair_count": 0,
        "basilisk_candidate_qualified_pair_count": 0,
        "formal_diagnostic_qualified_pair_count": 0,
        "counts_by_decision": {},
        "fault_summaries": [],
        "reports": [],
    }
    if contracts:
        contracts, qualification_index = validate_dataset_pairs(result.output_root, contracts)
        contracts, diagnostic_index = validate_dataset_diagnostics(result.output_root, contracts)

    index = build_astrograph_dataset_index(contracts)
    index["qualification"] = qualification_index
    index["diagnostic_qualification"] = diagnostic_index
    index["campaign_id"] = result.plan.campaign_id
    index["campaign_status"] = result.manifest.get("status")
    index["dry_run"] = dry_run
    index_path = result.output_root / "astrograph_dataset_index.json"
    write_json(index_path, index)
    return FaultDatasetExecutionResult(result, index, index_path)


__all__ = [
    "FACTORY_SCHEMA_VERSION",
    "SUPPORTED_FAULT_IDS",
    "FaultDatasetExecutionResult",
    "FaultDatasetFactoryConfig",
    "SplitCounts",
    "build_fault_dataset_campaign",
    "run_fault_dataset_campaign",
]
