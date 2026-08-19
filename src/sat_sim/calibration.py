"""Calibration evidence framework for data-ready, non-fabricated qualification.

V34 deliberately does not create calibration values.  It validates dataset
manifests, file integrity, evidence qualification and parameter-to-dataset
bindings so that real ground-test or flight-correlation data can be connected
later without changing the simulation model architecture.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import StrEnum
from importlib import resources
from pathlib import Path

from parameters.resource_paths import resolve_parameter_registry
from typing import Any, Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

CALIBRATION_SCHEMA_VERSION = "v34.calibration-dataset.v1"
CALIBRATION_REGISTRY_VERSION = "v34.calibration-registry.v1"
CALIBRATION_REPORT_VERSION = "v34.calibration-readiness.v1"
CALIBRATION_BATCH = "CALIBRATION-EVIDENCE-FRAMEWORK-AND-DATA-READY-1"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CalibrationSourceType(StrEnum):
    GROUND_TEST = "ground_test"
    FLIGHT_TELEMETRY = "flight_telemetry"
    MANUFACTURER_DATASHEET = "manufacturer_datasheet"
    PUBLIC_LITERATURE = "public_literature"
    ENGINEERING_ESTIMATE = "engineering_estimate"
    SYNTHETIC = "synthetic"
    DEMO = "demo"


class CalibrationClaimLevel(StrEnum):
    DEMO = "demo"
    ENGINEERING_ESTIMATE = "engineering_estimate"
    GROUND_CALIBRATED = "ground_calibrated"
    FLIGHT_CORRELATED = "flight_correlated"


class DatasetStatus(StrEnum):
    DRAFT = "draft"
    RECEIVED = "received"
    VERIFIED = "verified"
    REJECTED = "rejected"


class ApprovalStatus(StrEnum):
    DRAFT = "draft"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    REJECTED = "rejected"


class CalibrationIssue(_StrictModel):
    code: str
    severity: str
    message: str
    field: str | None = None
    evidence: Any = None


class DatasetFile(_StrictModel):
    path: str
    sha256: str
    media_type: str = "text/csv"
    size_bytes: int | None = Field(default=None, ge=0)
    role: str = "raw_measurement"

    @field_validator("sha256")
    @classmethod
    def _validate_sha256(cls, value: str) -> str:
        normalized = value.lower().strip()
        if not re.fullmatch(r"[0-9a-f]{64}", normalized):
            raise ValueError("sha256 must contain exactly 64 hexadecimal characters")
        return normalized


class MeasurementVariable(_StrictModel):
    name: str
    unit: str
    role: str = "measured"
    description: str = ""


class SamplingSpec(_StrictModel):
    frequency_hz: float | None = Field(default=None, gt=0)
    sample_count: int | None = Field(default=None, ge=1)
    start_time_utc: str | None = None
    end_time_utc: str | None = None


class UncertaintySpec(_StrictModel):
    provided: bool = False
    method: str | None = None
    confidence_level: float | None = Field(default=None, gt=0, le=1)
    measurement_uncertainty: dict[str, Any] = Field(default_factory=dict)


class CalibrationMethodSpec(_StrictModel):
    method: str | None = None
    target_parameters: list[str] = Field(default_factory=list)
    training_fraction: float | None = Field(default=None, gt=0, lt=1)
    validation_fraction: float | None = Field(default=None, gt=0, lt=1)
    residual_metrics: dict[str, float] = Field(default_factory=dict)
    correlation_metrics: dict[str, float] = Field(default_factory=dict)


class ApprovalSpec(_StrictModel):
    status: ApprovalStatus = ApprovalStatus.DRAFT
    reviewed_by: str | None = None
    approved_by: str | None = None
    approved_at_utc: str | None = None


class CalibrationDatasetManifest(_StrictModel):
    schema_version: str = CALIBRATION_SCHEMA_VERSION
    dataset_id: str
    title: str
    component: str
    source_type: CalibrationSourceType
    source_ref: str
    requested_claim_level: CalibrationClaimLevel = CalibrationClaimLevel.ENGINEERING_ESTIMATE
    status: DatasetStatus = DatasetStatus.DRAFT
    collected_at_utc: str | None = None
    files: list[DatasetFile] = Field(default_factory=list)
    measurements: list[MeasurementVariable] = Field(default_factory=list)
    sampling: SamplingSpec = Field(default_factory=SamplingSpec)
    uncertainty: UncertaintySpec = Field(default_factory=UncertaintySpec)
    calibration: CalibrationMethodSpec = Field(default_factory=CalibrationMethodSpec)
    approval: ApprovalSpec = Field(default_factory=ApprovalSpec)
    validity: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    notes: str = ""

    @field_validator("dataset_id")
    @classmethod
    def _validate_dataset_id(cls, value: str) -> str:
        normalized = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,127}", normalized):
            raise ValueError("dataset_id must be 3-128 characters using letters, digits, '.', '_' or '-'")
        return normalized


class CalibrationRegistryEntry(_StrictModel):
    dataset_id: str
    manifest_path: str
    manifest_sha256: str
    status: DatasetStatus
    qualification_level: CalibrationClaimLevel
    registered_at_utc: str


class CalibrationDatasetRegistry(_StrictModel):
    schema_version: str = CALIBRATION_REGISTRY_VERSION
    registry_id: str = "sat-sim-calibration-datasets"
    datasets: list[CalibrationRegistryEntry] = Field(default_factory=list)


class CalibrationValidationReport(_StrictModel):
    schema_version: str = CALIBRATION_REPORT_VERSION
    dataset_id: str | None = None
    status: str
    qualification_level: CalibrationClaimLevel
    requested_claim_level: CalibrationClaimLevel
    claim_allowed: bool
    file_count: int
    verified_file_count: int
    issues: list[CalibrationIssue] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


class CalibrationReadinessReport(_StrictModel):
    schema_version: str = CALIBRATION_REPORT_VERSION
    batch: str = CALIBRATION_BATCH
    generated_at_utc: str
    status: str
    dataset_available: bool
    dataset_count: int
    validated_dataset_count: int
    ground_calibrated_dataset_count: int
    flight_correlated_dataset_count: int
    calibration_claim_allowed: bool
    maximum_claim_level: CalibrationClaimLevel
    parameter_calibrated_record_count: int
    issues: list[CalibrationIssue] = Field(default_factory=list)
    datasets: list[CalibrationValidationReport] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    boundaries: list[str] = Field(default_factory=list)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_mapping(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    payload = yaml.safe_load(text) if path.suffix.lower() in {".yaml", ".yml"} else json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a mapping/object")
    return payload


def _packaged_json(name: str) -> dict[str, Any]:
    payload = json.loads(resources.files("sat_sim.calibration_data").joinpath(name).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"packaged calibration resource {name} is not an object")
    return payload


def default_calibration_registry_path(project_root: str | Path | None = None) -> Path | None:
    root = Path(project_root).resolve() if project_root is not None else Path.cwd().resolve()
    candidate = root / "configs" / "calibration" / "dataset_registry_v1.json"
    return candidate if candidate.is_file() else None


def load_calibration_registry(path: str | Path | None = None, *, project_root: str | Path | None = None) -> CalibrationDatasetRegistry:
    resolved = Path(path).resolve() if path is not None else default_calibration_registry_path(project_root)
    payload = _load_mapping(resolved) if resolved is not None else _packaged_json("dataset_registry_v1.json")
    return CalibrationDatasetRegistry.model_validate(payload)


def load_calibration_manifest(path: str | Path) -> CalibrationDatasetManifest:
    resolved = Path(path).resolve()
    return CalibrationDatasetManifest.model_validate(_load_mapping(resolved))


def _rank(level: CalibrationClaimLevel) -> int:
    return {
        CalibrationClaimLevel.DEMO: 0,
        CalibrationClaimLevel.ENGINEERING_ESTIMATE: 1,
        CalibrationClaimLevel.GROUND_CALIBRATED: 2,
        CalibrationClaimLevel.FLIGHT_CORRELATED: 3,
    }[level]


def validate_calibration_manifest(
    manifest: CalibrationDatasetManifest | Mapping[str, Any],
    *,
    manifest_path: str | Path | None = None,
    project_root: str | Path | None = None,
) -> CalibrationValidationReport:
    model = manifest if isinstance(manifest, CalibrationDatasetManifest) else CalibrationDatasetManifest.model_validate(manifest)
    base = Path(manifest_path).resolve().parent if manifest_path is not None else Path(project_root or Path.cwd()).resolve()
    issues: list[CalibrationIssue] = []
    verified_files = 0
    file_evidence: list[dict[str, Any]] = []
    for index, item in enumerate(model.files):
        path = Path(item.path)
        resolved = path if path.is_absolute() else base / path
        exists = resolved.is_file()
        actual_sha = _sha256_file(resolved) if exists else None
        hash_ok = actual_sha == item.sha256 if exists else False
        size_ok = item.size_bytes in {None, resolved.stat().st_size if exists else None}
        if exists and hash_ok and size_ok:
            verified_files += 1
        else:
            code = "CALIBRATION_DATA_FILE_MISSING" if not exists else "CALIBRATION_DATA_HASH_MISMATCH" if not hash_ok else "CALIBRATION_DATA_SIZE_MISMATCH"
            issues.append(CalibrationIssue(code=code, severity="fail", field=f"files[{index}]", message=f"dataset file failed integrity validation: {resolved}", evidence={"exists": exists, "expected_sha256": item.sha256, "actual_sha256": actual_sha}))
        file_evidence.append({"path": str(resolved), "exists": exists, "hash_ok": hash_ok, "size_ok": size_ok})

    approved = model.approval.status == ApprovalStatus.APPROVED and bool(model.approval.approved_by) and bool(model.approval.approved_at_utc)
    complete_method = bool(model.calibration.method and model.calibration.target_parameters)
    has_validation_split = bool(model.calibration.validation_fraction and model.calibration.validation_fraction > 0)
    integrity_ok = bool(model.files) and verified_files == len(model.files)
    measurement_ok = bool(model.measurements) and all(item.unit.strip() for item in model.measurements)

    qualification = CalibrationClaimLevel.DEMO
    if model.source_type in {CalibrationSourceType.MANUFACTURER_DATASHEET, CalibrationSourceType.PUBLIC_LITERATURE, CalibrationSourceType.ENGINEERING_ESTIMATE}:
        qualification = CalibrationClaimLevel.ENGINEERING_ESTIMATE
    elif model.source_type == CalibrationSourceType.GROUND_TEST:
        qualification = CalibrationClaimLevel.GROUND_CALIBRATED if all((model.status == DatasetStatus.VERIFIED, integrity_ok, measurement_ok, model.uncertainty.provided, complete_method, has_validation_split, approved)) else CalibrationClaimLevel.ENGINEERING_ESTIMATE
    elif model.source_type == CalibrationSourceType.FLIGHT_TELEMETRY:
        flight_ready = all((model.status == DatasetStatus.VERIFIED, integrity_ok, measurement_ok, model.uncertainty.provided, complete_method, has_validation_split, approved, bool(model.calibration.correlation_metrics)))
        qualification = CalibrationClaimLevel.FLIGHT_CORRELATED if flight_ready else CalibrationClaimLevel.ENGINEERING_ESTIMATE

    if model.source_type in {CalibrationSourceType.SYNTHETIC, CalibrationSourceType.DEMO} and _rank(model.requested_claim_level) >= _rank(CalibrationClaimLevel.GROUND_CALIBRATED):
        issues.append(CalibrationIssue(code="SYNTHETIC_CALIBRATION_PROMOTION_FORBIDDEN", severity="fail", field="requested_claim_level", message="synthetic/demo data cannot qualify ground_calibrated or flight_correlated claims"))
    if model.requested_claim_level == CalibrationClaimLevel.GROUND_CALIBRATED and model.source_type != CalibrationSourceType.GROUND_TEST:
        issues.append(CalibrationIssue(code="GROUND_CALIBRATION_SOURCE_INVALID", severity="fail", field="source_type", message="ground_calibrated requires source_type=ground_test"))
    if model.requested_claim_level == CalibrationClaimLevel.FLIGHT_CORRELATED and model.source_type != CalibrationSourceType.FLIGHT_TELEMETRY:
        issues.append(CalibrationIssue(code="FLIGHT_CORRELATION_SOURCE_INVALID", severity="fail", field="source_type", message="flight_correlated requires source_type=flight_telemetry"))
    if _rank(model.requested_claim_level) >= _rank(CalibrationClaimLevel.GROUND_CALIBRATED):
        requirements = {
            "files": integrity_ok,
            "measurements": measurement_ok,
            "uncertainty": model.uncertainty.provided,
            "calibration_method": complete_method,
            "validation_split": has_validation_split,
            "approval": approved,
            "dataset_status": model.status == DatasetStatus.VERIFIED,
        }
        for field, ok in requirements.items():
            if not ok:
                issues.append(CalibrationIssue(code="CALIBRATION_EVIDENCE_MISSING", severity="fail", field=field, message=f"{field} is required for {model.requested_claim_level.value}"))
    if model.requested_claim_level == CalibrationClaimLevel.FLIGHT_CORRELATED and not model.calibration.correlation_metrics:
        issues.append(CalibrationIssue(code="FLIGHT_CORRELATION_METRICS_MISSING", severity="fail", field="calibration.correlation_metrics", message="flight-correlated qualification requires correlation metrics"))

    claim_allowed = _rank(qualification) >= _rank(model.requested_claim_level) and not any(item.severity == "fail" for item in issues)
    status = "PASS" if claim_allowed else "FAIL"
    return CalibrationValidationReport(
        dataset_id=model.dataset_id,
        status=status,
        qualification_level=qualification,
        requested_claim_level=model.requested_claim_level,
        claim_allowed=claim_allowed,
        file_count=len(model.files),
        verified_file_count=verified_files,
        issues=issues,
        evidence={
            "manifest_path": str(Path(manifest_path).resolve()) if manifest_path is not None else None,
            "source_type": model.source_type.value,
            "dataset_status": model.status.value,
            "approval_status": model.approval.status.value,
            "file_integrity": file_evidence,
            "target_parameters": model.calibration.target_parameters,
        },
    )


def _parameter_records(project_root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for name in ("demo_parameter_registry_v1.json", "engineering_estimate_registry_v1.json"):
        path = resolve_parameter_registry(name, root=project_root)
        if path.is_file():
            payload = _load_mapping(path)
            for row in payload.get("parameters", []):
                if isinstance(row, dict):
                    records.append({**row, "_registry": str(path)})
    return records


def build_calibration_readiness_report(
    *,
    project_root: str | Path | None = None,
    registry_path: str | Path | None = None,
) -> CalibrationReadinessReport:
    root = Path(project_root or Path.cwd()).resolve()
    registry = load_calibration_registry(registry_path, project_root=root)
    reports: list[CalibrationValidationReport] = []
    issues: list[CalibrationIssue] = []
    registry_by_id: dict[str, CalibrationRegistryEntry] = {}
    for entry in registry.datasets:
        if entry.dataset_id in registry_by_id:
            issues.append(CalibrationIssue(code="DUPLICATE_CALIBRATION_DATASET", severity="fail", field=entry.dataset_id, message="dataset_id appears more than once in registry"))
            continue
        registry_by_id[entry.dataset_id] = entry
        manifest_path = Path(entry.manifest_path)
        resolved = manifest_path if manifest_path.is_absolute() else root / manifest_path
        if not resolved.is_file():
            issues.append(CalibrationIssue(code="CALIBRATION_MANIFEST_MISSING", severity="fail", field=entry.dataset_id, message=f"registered manifest does not exist: {resolved}"))
            continue
        actual_manifest_sha = _sha256_file(resolved)
        if actual_manifest_sha != entry.manifest_sha256:
            issues.append(CalibrationIssue(code="CALIBRATION_MANIFEST_HASH_MISMATCH", severity="fail", field=entry.dataset_id, message="registered manifest hash does not match", evidence={"expected": entry.manifest_sha256, "actual": actual_manifest_sha}))
            continue
        manifest = load_calibration_manifest(resolved)
        report = validate_calibration_manifest(manifest, manifest_path=resolved, project_root=root)
        reports.append(report)
        if report.dataset_id != entry.dataset_id or report.qualification_level != entry.qualification_level or manifest.status != entry.status:
            issues.append(CalibrationIssue(code="CALIBRATION_REGISTRY_ENTRY_MISMATCH", severity="fail", field=entry.dataset_id, message="registry entry does not match validated manifest"))

    calibrated_records = 0
    qualified = {item.dataset_id: item for item in reports if item.claim_allowed}
    for row in _parameter_records(root):
        confidence = str(row.get("confidence", "demo"))
        if confidence not in {"ground_calibrated", "flight_correlated"}:
            continue
        calibrated_records += 1
        dataset_id = str((row.get("calibration") or {}).get("dataset_id") or "")
        report = qualified.get(dataset_id)
        required = CalibrationClaimLevel(confidence)
        if report is None or _rank(report.qualification_level) < _rank(required):
            issues.append(CalibrationIssue(code="PARAMETER_CALIBRATION_EVIDENCE_MISSING", severity="fail", field=str(row.get("parameter_id")), message=f"{confidence} parameter does not reference a qualified dataset", evidence={"dataset_id": dataset_id, "registry": row.get("_registry")}))
        elif str(row.get("parameter_id")) not in set(report.evidence.get("target_parameters", [])):
            issues.append(CalibrationIssue(code="PARAMETER_NOT_IN_CALIBRATION_TARGETS", severity="fail", field=str(row.get("parameter_id")), message="parameter is not listed in the dataset calibration target_parameters", evidence={"dataset_id": dataset_id}))

    ground_count = sum(item.claim_allowed and item.qualification_level == CalibrationClaimLevel.GROUND_CALIBRATED for item in reports)
    flight_count = sum(item.claim_allowed and item.qualification_level == CalibrationClaimLevel.FLIGHT_CORRELATED for item in reports)
    dataset_available = bool(registry.datasets)
    maximum = CalibrationClaimLevel.FLIGHT_CORRELATED if flight_count else CalibrationClaimLevel.GROUND_CALIBRATED if ground_count else CalibrationClaimLevel.ENGINEERING_ESTIMATE
    if not dataset_available:
        reason_codes = ["NO_CALIBRATION_DATA_AVAILABLE"]
        boundaries = [
            "No real calibration dataset is registered; ground_calibrated and flight_correlated claims remain forbidden.",
            "Engineering-estimate parameters and surrogate UQ may be used only within their declared validity and Claim ceiling.",
            "Synthetic, demo, literature or manufacturer data cannot be relabeled as ground-test or flight evidence.",
        ]
    else:
        reason_codes = []
        boundaries = ["Qualification is limited to the variables, parameter targets, validity envelope and approval recorded by each dataset manifest."]
    status = "PASS" if not any(item.severity == "fail" for item in issues) else "FAIL"
    return CalibrationReadinessReport(
        generated_at_utc=_utc_now(),
        status=status,
        dataset_available=dataset_available,
        dataset_count=len(registry.datasets),
        validated_dataset_count=sum(item.status == "PASS" for item in reports),
        ground_calibrated_dataset_count=ground_count,
        flight_correlated_dataset_count=flight_count,
        calibration_claim_allowed=bool(ground_count or flight_count) and status == "PASS",
        maximum_claim_level=maximum,
        parameter_calibrated_record_count=calibrated_records,
        issues=issues,
        datasets=reports,
        reason_codes=reason_codes,
        boundaries=boundaries,
    )


def register_calibration_manifest(
    manifest_path: str | Path,
    *,
    project_root: str | Path | None = None,
    registry_path: str | Path | None = None,
    commit: bool = False,
) -> dict[str, Any]:
    root = Path(project_root or Path.cwd()).resolve()
    resolved_manifest = Path(manifest_path).resolve()
    manifest = load_calibration_manifest(resolved_manifest)
    validation = validate_calibration_manifest(manifest, manifest_path=resolved_manifest, project_root=root)
    if not validation.claim_allowed:
        return {"status": "FAIL", "committed": False, "validation": validation.model_dump(mode="json"), "reason_code": "CALIBRATION_MANIFEST_NOT_QUALIFIED"}
    resolved_registry = Path(registry_path).resolve() if registry_path is not None else root / "configs" / "calibration" / "dataset_registry_v1.json"
    registry = load_calibration_registry(resolved_registry if resolved_registry.is_file() else None, project_root=root)
    if any(item.dataset_id == manifest.dataset_id for item in registry.datasets):
        return {"status": "FAIL", "committed": False, "validation": validation.model_dump(mode="json"), "reason_code": "DUPLICATE_CALIBRATION_DATASET"}
    try:
        relative_manifest = str(resolved_manifest.relative_to(root))
    except ValueError:
        relative_manifest = str(resolved_manifest)
    entry = CalibrationRegistryEntry(
        dataset_id=manifest.dataset_id,
        manifest_path=relative_manifest,
        manifest_sha256=_sha256_file(resolved_manifest),
        status=manifest.status,
        qualification_level=validation.qualification_level,
        registered_at_utc=_utc_now(),
    )
    updated = CalibrationDatasetRegistry(registry_id=registry.registry_id, datasets=[*registry.datasets, entry])
    if commit:
        resolved_registry.parent.mkdir(parents=True, exist_ok=True)
        resolved_registry.write_text(updated.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return {"status": "PASS", "committed": commit, "registry_path": str(resolved_registry), "entry": entry.model_dump(mode="json"), "validation": validation.model_dump(mode="json")}


def calibration_dataset_schema() -> dict[str, Any]:
    return CalibrationDatasetManifest.model_json_schema(mode="validation")


def calibration_registry_schema() -> dict[str, Any]:
    return CalibrationDatasetRegistry.model_json_schema(mode="validation")


def calibration_readiness_schema() -> dict[str, Any]:
    return CalibrationReadinessReport.model_json_schema(mode="validation")


__all__ = [
    "CALIBRATION_SCHEMA_VERSION",
    "CALIBRATION_REGISTRY_VERSION",
    "CALIBRATION_REPORT_VERSION",
    "CALIBRATION_BATCH",
    "CalibrationSourceType",
    "CalibrationClaimLevel",
    "DatasetStatus",
    "ApprovalStatus",
    "CalibrationIssue",
    "DatasetFile",
    "MeasurementVariable",
    "SamplingSpec",
    "UncertaintySpec",
    "CalibrationMethodSpec",
    "ApprovalSpec",
    "CalibrationDatasetManifest",
    "CalibrationRegistryEntry",
    "CalibrationDatasetRegistry",
    "CalibrationValidationReport",
    "CalibrationReadinessReport",
    "default_calibration_registry_path",
    "load_calibration_registry",
    "load_calibration_manifest",
    "validate_calibration_manifest",
    "build_calibration_readiness_report",
    "register_calibration_manifest",
    "calibration_dataset_schema",
    "calibration_registry_schema",
    "calibration_readiness_schema",
]
