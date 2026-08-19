"""Evidence-oriented release validation and closure checks."""
from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import os
import platform
import sys
import tempfile
from datetime import datetime, timezone
from enum import StrEnum
from importlib import resources
from pathlib import Path
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from .capability_registry import list_capabilities, validate_operator_registry
from .calibration import build_calibration_readiness_report
from .reason_codes import ReasonCode
from .run_bundle import execute_prepared_run, prepare_run, verify_run_bundle
from .task_models import CANONICAL_TASK_SPEC_VERSION, CanonicalTaskSpec, canonicalize_task_spec
from .task_spec import spec_sha256
from .unified_agent import UnifiedAgentRequest, run_unified_agent

RELEASE_VERSION = "0.7.8"
RELEASE_ID = "SAT-SIM-0.7.8-ENGINEERING-BASELINE"
ENVIRONMENT_REPORT_VERSION = "sat-sim.environment-doctor.v1"
RELEASE_REPORT_VERSION = "sat-sim.release-closure-report.v1"
EXPECTED_NUMPY_VERSION = "2.2.6" if sys.version_info < (3, 11) else "2.3.5"

ACCEPTED_BSK_RUNTIME_VERSIONS = frozenset({"2.11.0", "2.11.0+satfix1"})

EXPECTED_DISTRIBUTIONS: dict[str, tuple[str, str]] = {
    "bsk": ("Basilisk", "2.11.0+satfix1"),
    "colorama": ("colorama", "0.4.6"),
    "sgp4": ("sgp4", "2.25"),
    "numpy": ("numpy", EXPECTED_NUMPY_VERSION),
    "pydantic": ("pydantic", "2.13.4"),
    "PyYAML": ("yaml", "6.0.3"),
    "jsonschema": ("jsonschema", "4.26.0"),
}
OPTIONAL_DISTRIBUTIONS: dict[str, tuple[str, str | None]] = {
    "fastapi": ("fastapi", "0.139.2"),
    "starlette": ("starlette", "1.3.1"),
    "uvicorn": ("uvicorn", "0.48.0"),
    "httpx": ("httpx", "0.28.1"),
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CheckStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


class ReleaseCheck(_StrictModel):
    check_id: str
    title: str
    status: CheckStatus
    required: bool = True
    summary: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    reason_codes: list[str] = Field(default_factory=list)


class EnvironmentDoctorReport(_StrictModel):
    schema_version: str = ENVIRONMENT_REPORT_VERSION
    release_id: str = RELEASE_ID
    release_version: str = RELEASE_VERSION
    generated_at_utc: str
    ok: bool
    strict_assets: bool
    checks: list[ReleaseCheck]
    summary: dict[str, int]
    environment: dict[str, Any]


class RepresentativeRunResult(_StrictModel):
    case_id: str
    category: str
    capability_id: str
    level: str
    expected_validation: str
    run_status: str
    validation_result: str
    bundle_root: str
    bundle_integrity_ok: bool
    task_spec_sha256: str
    resolved_spec_sha256: str
    execution_plan_sha256: str
    injection_delivery_results: list[str] = Field(default_factory=list)
    injection_effect_results: list[str] = Field(default_factory=list)
    allowed_claims: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    passed: bool
    notes: list[str] = Field(default_factory=list)


class RepresentativeSuiteReport(_StrictModel):
    schema_version: str = "sat-sim.representative-suite.v1"
    generated_at_utc: str
    ok: bool
    total: int
    passed: int
    failed: int
    cases: list[RepresentativeRunResult]


class ReleaseClosureReport(_StrictModel):
    schema_version: str = RELEASE_REPORT_VERSION
    release_id: str = RELEASE_ID
    release_version: str = RELEASE_VERSION
    generated_at_utc: str
    ok: bool
    environment_doctor: EnvironmentDoctorReport
    golden_evidence: dict[str, Any]
    representative_suite: RepresentativeSuiteReport | None = None
    termination_conditions: list[ReleaseCheck]
    summary: dict[str, int]
    known_boundaries: list[str]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_resource(package: str, name: str) -> dict[str, Any]:
    value = json.loads(resources.files(package).joinpath(name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"resource {package}:{name} is not an object")
    return value


def release_manifest() -> dict[str, Any]:
    return _json_resource("sat_sim", "release_manifest.json")


def release_baseline() -> dict[str, Any]:
    return _json_resource("sat_sim", "release_baseline.json")


def environment_doctor_schema() -> dict[str, Any]:
    schema = EnvironmentDoctorReport.model_json_schema(mode="validation")
    schema["$id"] = "https://example.local/sat-sim/environment-doctor.schema.json"
    schema["title"] = "Satellite Simulation Agent Environment Doctor"
    return schema


def release_closure_report_schema() -> dict[str, Any]:
    schema = ReleaseClosureReport.model_json_schema(mode="validation")
    schema["$id"] = "https://example.local/sat-sim/release-closure.schema.json"
    schema["title"] = "Satellite Simulation Agent Release Closure Report"
    return schema


def _check(
    check_id: str,
    title: str,
    status: CheckStatus,
    summary: str,
    *,
    required: bool = True,
    evidence: Mapping[str, Any] | None = None,
    reason_codes: list[str] | None = None,
) -> ReleaseCheck:
    return ReleaseCheck(
        check_id=check_id,
        title=title,
        status=status,
        required=required,
        summary=summary,
        evidence=dict(evidence or {}),
        reason_codes=list(reason_codes or []),
    )


def _summary(checks: list[ReleaseCheck]) -> dict[str, int]:
    return {status.value: sum(1 for item in checks if item.status == status) for status in CheckStatus}


def _report_ok(checks: list[ReleaseCheck]) -> bool:
    return not any(item.required and item.status == CheckStatus.FAIL for item in checks)


def _writable_check(path: Path) -> tuple[bool, str | None]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="sat-sim-doctor-", dir=path, delete=True) as stream:
            stream.write(b"ok")
            stream.flush()
        return True, None
    except Exception as exc:  # pragma: no cover - depends on filesystem permissions
        return False, str(exc)


def run_environment_doctor(
    *,
    runs_root: str | Path = "runs",
    artifacts_root: str | Path = ".sat_sim_api",
    strict_assets: bool = False,
    require_api: bool = False,
    smoke: bool = True,
) -> EnvironmentDoctorReport:
    """Inspect the runtime without downloading or mutating model assets."""

    checks: list[ReleaseCheck] = []
    py_ok = (3, 10) <= sys.version_info < (3, 14)
    checks.append(_check(
        "python.version",
        "Python version",
        CheckStatus.PASS if py_ok else CheckStatus.FAIL,
        f"Python {platform.python_version()} detected; project supports Python 3.10 through 3.13.",
        evidence={"version": platform.python_version(), "executable": sys.executable},
    ))

    dependency_evidence: dict[str, Any] = {}
    dependency_failures: list[str] = []
    for distribution, (module, expected) in EXPECTED_DISTRIBUTIONS.items():
        try:
            importlib.import_module(module)
            actual = importlib.metadata.version(distribution)
            accepted = actual in ACCEPTED_BSK_RUNTIME_VERSIONS if distribution == "bsk" else actual == expected
            dependency_evidence[distribution] = {
                "module": module,
                "expected": expected,
                "accepted_versions": sorted(ACCEPTED_BSK_RUNTIME_VERSIONS) if distribution == "bsk" else [expected],
                "actual": actual,
                "matched": accepted,
            }
            if not accepted:
                dependency_failures.append(distribution)
        except Exception as exc:
            dependency_evidence[distribution] = {"module": module, "expected": expected, "error": str(exc)}
            dependency_failures.append(distribution)
    checks.append(_check(
        "dependencies.core",
        "Pinned core dependencies",
        CheckStatus.PASS if not dependency_failures else CheckStatus.FAIL,
        "All pinned core dependencies import with the expected versions." if not dependency_failures else f"Dependency failures or version mismatches: {dependency_failures}",
        evidence=dependency_evidence,
        reason_codes=[] if not dependency_failures else [ReasonCode.ENVIRONMENT_CHECK_FAILED.value],
    ))

    optional_evidence: dict[str, Any] = {}
    optional_missing: list[str] = []
    optional_mismatches: list[str] = []
    for distribution, (module, expected) in OPTIONAL_DISTRIBUTIONS.items():
        try:
            importlib.import_module(module)
            actual = importlib.metadata.version(distribution)
            matched = expected is None or actual == expected
            optional_evidence[distribution] = {
                "module": module,
                "expected": expected,
                "actual": actual,
                "matched": matched,
            }
            if not matched:
                optional_mismatches.append(f"{distribution}: expected {expected}, found {actual}")
        except Exception as exc:
            optional_evidence[distribution] = {"module": module, "expected": expected, "error": str(exc)}
            optional_missing.append(distribution)
    optional_issues = optional_missing + optional_mismatches
    optional_status = CheckStatus.FAIL if require_api and optional_issues else CheckStatus.WARN if optional_issues else CheckStatus.PASS
    if not optional_issues:
        optional_summary = "The tested FastAPI, Starlette, Uvicorn and HTTPX stack is installed."
    else:
        optional_summary = f"API dependency issues: {optional_issues}"
    checks.append(_check(
        "dependencies.api",
        "Optional local API dependencies",
        optional_status,
        optional_summary,
        required=require_api,
        evidence=optional_evidence,
        reason_codes=[ReasonCode.ENVIRONMENT_CHECK_FAILED.value] if require_api and optional_issues else [],
    ))

    try:
        import sat_sim
        package_version = getattr(sat_sim, "__version__", None)
        try:
            distribution_version = importlib.metadata.version("satellite-simulation-platform")
        except importlib.metadata.PackageNotFoundError:
            distribution_version = None
        version_match = package_version == RELEASE_VERSION and distribution_version in {None, RELEASE_VERSION}
        status = CheckStatus.PASS if version_match else CheckStatus.WARN
        checks.append(_check(
            "package.version",
            "Release version identity",
            status,
            f"Module version={package_version}; installed distribution version={distribution_version or 'source-tree'}; release={RELEASE_VERSION}.",
            required=False,
            evidence={"module_version": package_version, "distribution_version": distribution_version, "release_version": RELEASE_VERSION},
            reason_codes=[] if version_match else [ReasonCode.VERSION_MISMATCH.value],
        ))
    except Exception as exc:  # pragma: no cover
        checks.append(_check(
            "package.version", "Release version identity", CheckStatus.FAIL, str(exc),
            evidence={}, reason_codes=[ReasonCode.ENVIRONMENT_CHECK_FAILED.value],
        ))

    manifest = release_manifest()
    try:
        capabilities = list_capabilities()
        active = [item for item in capabilities if item.is_active and item.exposed_to_agent]
        issues = validate_operator_registry(active_only=True)
        errors = [item.to_dict() for item in issues if item.severity == "error"]
        warnings = [item.to_dict() for item in issues if item.severity == "warning"]
        counts_ok = (
            len(capabilities) == int(manifest["capability_contract_count"])
            and len(active) == int(manifest["active_agent_capability_count"])
            and not errors
        )
        checks.append(_check(
            "registry.capabilities",
            "Capability and Operator Registry",
            CheckStatus.PASS if counts_ok else CheckStatus.FAIL,
            f"Loaded {len(capabilities)} capability contracts and {len(active)} active Agent capabilities; {len(errors)} errors, {len(warnings)} warnings.",
            evidence={"total": len(capabilities), "active": len(active), "errors": errors, "warnings": warnings},
            reason_codes=[] if counts_ok else [ReasonCode.OPERATOR_CONTRACT_INVALID.value],
        ))
    except Exception as exc:
        checks.append(_check(
            "registry.capabilities", "Capability and Operator Registry", CheckStatus.FAIL, str(exc),
            reason_codes=[ReasonCode.OPERATOR_CONTRACT_INVALID.value],
        ))

    schema_evidence: dict[str, Any] = {}
    schema_missing: list[str] = []
    schema_root = resources.files("sat_sim.schemas")
    for name in manifest.get("required_schemas", []):
        exists = schema_root.joinpath(str(name)).is_file()
        schema_evidence[str(name)] = exists
        if not exists:
            schema_missing.append(str(name))
    checks.append(_check(
        "package.schemas",
        "Packaged schemas",
        CheckStatus.PASS if not schema_missing else CheckStatus.FAIL,
        "All release schemas are packaged." if not schema_missing else f"Missing schemas: {schema_missing}",
        evidence=schema_evidence,
        reason_codes=[] if not schema_missing else [ReasonCode.ENVIRONMENT_CHECK_FAILED.value],
    ))

    try:
        golden = _json_resource("sat_sim.evals", "golden_set.json")
        count = len(golden.get("cases", []))
        golden_ok = count == int(manifest["golden_case_count"])
        checks.append(_check(
            "package.golden_set",
            "Bundled Golden Set",
            CheckStatus.PASS if golden_ok else CheckStatus.FAIL,
            f"Bundled Golden Set contains {count} cases.",
            evidence={"case_count": count, "schema_version": golden.get("schema_version")},
            reason_codes=[] if golden_ok else [ReasonCode.ENVIRONMENT_CHECK_FAILED.value],
        ))
    except Exception as exc:
        checks.append(_check(
            "package.golden_set", "Bundled Golden Set", CheckStatus.FAIL, str(exc),
            reason_codes=[ReasonCode.ENVIRONMENT_CHECK_FAILED.value],
        ))

    try:
        calibration = build_calibration_readiness_report(project_root=Path.cwd())
        calibration_ok = (
            calibration.status == "PASS"
            and calibration.maximum_claim_level.value == str(manifest.get("calibration_maximum_claim_level", "engineering_estimate"))
            and calibration.dataset_count == int(manifest.get("calibration_dataset_count", 0))
            and calibration.calibration_claim_allowed is False
        )
        checks.append(_check(
            "calibration.readiness",
            "Calibration evidence and data readiness",
            CheckStatus.PASS if calibration_ok else CheckStatus.FAIL,
            "Calibration infrastructure is data-ready and no calibrated Claim is fabricated." if calibration_ok else "Calibration readiness or Claim ceiling does not match the release manifest.",
            evidence=calibration.model_dump(mode="json"),
            reason_codes=[] if calibration_ok else [ReasonCode.CALIBRATION_EVIDENCE_MISSING.value],
        ))
    except Exception as exc:
        checks.append(_check(
            "calibration.readiness", "Calibration evidence and data readiness", CheckStatus.FAIL, str(exc),
            reason_codes=[ReasonCode.CALIBRATION_EVIDENCE_MISSING.value],
        ))

    for check_id, title, path in (
        ("filesystem.runs", "Writable Run root", Path(runs_root)),
        ("filesystem.artifacts", "Writable API artifact root", Path(artifacts_root)),
    ):
        writable, error = _writable_check(path)
        checks.append(_check(
            check_id, title, CheckStatus.PASS if writable else CheckStatus.FAIL,
            f"Writable directory: {path.resolve()}" if writable else f"Not writable: {error}",
            evidence={"path": str(path.resolve())},
            reason_codes=[] if writable else [ReasonCode.ENVIRONMENT_CHECK_FAILED.value],
        ))

    try:
        from integrated.orbit_environment.assets import resolve_spice_assets, resolve_wmm_asset
        wmm, wmm_searched = resolve_wmm_asset(strict=False)
        spice, missing, spice_searched = resolve_spice_assets(strict=False)
        assets_ok = wmm is not None and not missing
        status = CheckStatus.PASS if assets_ok else CheckStatus.FAIL if strict_assets else CheckStatus.WARN
        checks.append(_check(
            "assets.environment",
            "WMM and SPICE environment assets",
            status,
            "All WMM/SPICE assets resolved." if assets_ok else "Core Agent remains usable, but strict high-fidelity environment assets are incomplete.",
            required=strict_assets,
            evidence={
                "wmm": None if wmm is None else wmm.__dict__,
                "wmm_searched": list(wmm_searched),
                "spice_count": len(spice),
                "spice_missing": list(missing),
                "spice_searched": list(spice_searched),
                "asset_root_env": os.getenv("SAT_SIM_ASSET_ROOT"),
                "wmm_path_env": os.getenv("SAT_SIM_WMM_PATH"),
                "spice_path_env": os.getenv("SAT_SIM_SPICE_PATH"),
            },
            reason_codes=[] if assets_ok else [ReasonCode.OPTIONAL_ASSET_MISSING.value],
        ))
    except Exception as exc:
        checks.append(_check(
            "assets.environment", "WMM and SPICE environment assets",
            CheckStatus.FAIL if strict_assets else CheckStatus.WARN,
            str(exc), required=strict_assets,
            reason_codes=[ReasonCode.OPTIONAL_ASSET_MISSING.value],
        ))

    if smoke:
        smoke_root = Path(artifacts_root) / "doctor-smoke"
        try:
            result = run_unified_agent(UnifiedAgentRequest(
                input_kind="form",
                form_data={
                    "task_id": "doctor_smoke",
                    "capability_id": "component.battery.v1",
                    "level": "component",
                    "duration_s": 10.0,
                    "sample_s": 5.0,
                    "outputs": {"qoi": ["qoi.eps.battery.final_soc"]},
                },
                output_dir=smoke_root,
            ))
            smoke_ok = bool(result.ok and result.planning and result.planning.ok and result.compiled)
            checks.append(_check(
                "workflow.smoke",
                "Canonical TaskSpec and planning smoke",
                CheckStatus.PASS if smoke_ok else CheckStatus.FAIL,
                "Form → Canonical TaskSpec → guard → plan → compile succeeded." if smoke_ok else "Core Agent smoke failed.",
                evidence={
                    "task_spec_sha256": spec_sha256(result.task_spec) if result.task_spec else None,
                    "plan_sha256": result.planning.execution_plan.plan_sha256 if result.planning and result.planning.execution_plan else None,
                    "reason_codes": list(result.reason_codes),
                },
                reason_codes=[] if smoke_ok else [ReasonCode.ENVIRONMENT_CHECK_FAILED.value],
            ))
        except Exception as exc:
            checks.append(_check(
                "workflow.smoke", "Canonical TaskSpec and planning smoke", CheckStatus.FAIL, str(exc),
                reason_codes=[ReasonCode.ENVIRONMENT_CHECK_FAILED.value],
            ))

    summary = _summary(checks)
    return EnvironmentDoctorReport(
        generated_at_utc=_utc_now(),
        ok=_report_ok(checks),
        strict_assets=strict_assets,
        checks=checks,
        summary=summary,
        environment={
            "platform": platform.platform(),
            "python": platform.python_version(),
            "executable": sys.executable,
            "cwd": str(Path.cwd()),
        },
    )


def representative_release_forms() -> list[dict[str, Any]]:
    """Return deterministic release examples used by docs and closure tests."""

    common_outputs = {"qoi": ["qoi.eps.battery.final_soc"]}
    return [
        {
            "case_id": "component_nominal",
            "category": "nominal",
            "expected_validation": "PASS",
            "form": {
                "task_id": "representative_component_battery_nominal",
                "capability_id": "component.battery.v1",
                "level": "component",
                "duration_s": 30.0,
                "sample_s": 5.0,
                "outputs": common_outputs,
            },
        },
        {
            "case_id": "component_fault",
            "category": "fault",
            "expected_validation": "PASS",
            "form": {
                "task_id": "representative_component_battery_fault",
                "capability_id": "component.battery.v1",
                "level": "component",
                "duration_s": 30.0,
                "sample_s": 5.0,
                "outputs": common_outputs,
                "faults": [{
                    "id": "battery_open_circuit",
                    "target": "battery",
                    "target_type": "battery",
                    "effect": "open_circuit",
                    "start_s": 5.0,
                    "magnitude": 1.0,
                    "delivery": "legacy_fault",
                }],
            },
        },
        {
            "case_id": "component_degradation",
            "category": "degradation",
            "expected_validation": "PASS",
            "form": {
                "task_id": "representative_component_battery_degradation",
                "capability_id": "component.battery.v1",
                "level": "component",
                "duration_s": 30.0,
                "sample_s": 5.0,
                "outputs": common_outputs,
                "degradations": [{
                    "id": "battery_capacity_loss",
                    "target": "battery",
                    "target_type": "battery",
                    "effect": "capacity_loss_pct",
                    "start_s": 5.0,
                    "magnitude": 0.2,
                    "delivery": "legacy_degradation",
                    "parameters": {"capacity_loss_pct": 20.0},
                }],
                "model": {
                    "legacy_degradations": {"eps": {"battery": {"capacity_loss_pct": 20.0}}}
                },
            },
        },
        {
            "case_id": "subsystem_nominal",
            "category": "nominal",
            "expected_validation": "PASS",
            "form": {
                "task_id": "representative_subsystem_eps_nominal",
                "capability_id": "subsystem.eps.basic.v1",
                "level": "subsystem",
                "duration_s": 30.0,
                "sample_s": 5.0,
                "outputs": {"qoi": ["qoi.eps.battery.final_soc", "qoi.eps.power.min_margin_w"]},
            },
        },
        {
            "case_id": "whole_spacecraft_combined_nominal",
            "category": "combined",
            "expected_validation": "PASS",
            "form": {
                "task_id": "representative_whole_power_thermal_orbit_nominal",
                "capability_id": "whole_spacecraft.power_thermal_orbit_coupled.v1",
                "level": "whole_spacecraft",
                "duration_s": 30.0,
                "sample_s": 5.0,
                "outputs": {
                    "qoi": [
                        "qoi.spacecraft.power.final_soc",
                        "qoi.spacecraft.thermal.final_bus_temp_c",
                    ]
                },
            },
        },
        {
            "case_id": "mission_fail_preserved",
            "category": "mission_fail",
            "expected_validation": "FAIL",
            "form": {
                "task_id": "representative_mission_fail_preserved",
                "capability_id": "component.battery.v1",
                "level": "component",
                "duration_s": 30.0,
                "sample_s": 5.0,
                "outputs": {"qoi": ["eps.battery.soc"]},
                "validation": {
                    "assertions": [{
                        "id": "impossible_final_soc",
                        "field": "eps.battery.soc",
                        "aggregation": "last",
                        "operator": ">=",
                        "value": 2.0,
                    }]
                },
            },
        },
    ]


def _expected_case_pass(case: Mapping[str, Any], *, run_status: str, validation: str, integrity: bool, delivery: list[str]) -> bool:
    expected = str(case["expected_validation"])
    if run_status != "SUCCEEDED" or validation != expected or not integrity:
        return False
    if case["category"] in {"fault", "degradation"}:
        return bool(delivery) and all(item == "PASS" for item in delivery)
    return True


def run_representative_release_suite(*, output_root: str | Path) -> RepresentativeSuiteReport:
    """Run component, subsystem, whole-spacecraft and negative examples."""

    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    results: list[RepresentativeRunResult] = []
    for case in representative_release_forms():
        case_id = str(case["case_id"])
        agent = run_unified_agent(UnifiedAgentRequest(
            input_kind="form",
            form_data=case["form"],
            output_dir=root / "agent" / case_id,
        ))
        if not agent.ok:
            results.append(RepresentativeRunResult(
                case_id=case_id,
                category=str(case["category"]),
                capability_id=str((case["form"] or {}).get("capability_id")),
                level=str((case["form"] or {}).get("level")),
                expected_validation=str(case["expected_validation"]),
                run_status="NOT_STARTED",
                validation_result="NOT_EVALUATED",
                bundle_root="",
                bundle_integrity_ok=False,
                task_spec_sha256=spec_sha256(agent.task_spec) if agent.task_spec else "",
                resolved_spec_sha256="",
                execution_plan_sha256="",
                passed=False,
                notes=[f"Agent preparation failed: {agent.validation.to_dict()}", *agent.reason_codes],
            ))
            continue

        task_spec = canonicalize_task_spec(agent.task_spec)
        prepared = prepare_run(task_spec, output_root=root / "runs")
        execution = execute_prepared_run(
            prepared.bundle_root,
            expected_plan_sha256=prepared.execution_plan_sha256,
            max_attempts=1,
        )
        integrity = verify_run_bundle(execution.bundle_root)
        delivery = [item.delivery_result.value for item in execution.validation.injection_evidence]
        effects = [item.effect_result.value for item in execution.validation.injection_evidence]
        passed = _expected_case_pass(
            case,
            run_status=execution.run_record.status.value,
            validation=execution.validation.result.value,
            integrity=bool(integrity.get("ok")),
            delivery=delivery,
        )
        results.append(RepresentativeRunResult(
            case_id=case_id,
            category=str(case["category"]),
            capability_id=str(task_spec["model"]["capability_id"]),
            level=str(task_spec["simulation"]["level"]),
            expected_validation=str(case["expected_validation"]),
            run_status=execution.run_record.status.value,
            validation_result=execution.validation.result.value,
            bundle_root=str(execution.bundle_root),
            bundle_integrity_ok=bool(integrity.get("ok")),
            task_spec_sha256=prepared.canonical_task_spec_sha256,
            resolved_spec_sha256=prepared.resolved_spec_sha256,
            execution_plan_sha256=prepared.execution_plan_sha256,
            injection_delivery_results=delivery,
            injection_effect_results=effects,
            allowed_claims=list(execution.claim_report.allowed_claims),
            forbidden_claims=list(execution.claim_report.forbidden_claims),
            passed=passed,
            notes=list(execution.claim_report.limitations),
        ))

    passed_count = sum(1 for item in results if item.passed)
    return RepresentativeSuiteReport(
        generated_at_utc=_utc_now(),
        ok=passed_count == len(results),
        total=len(results),
        passed=passed_count,
        failed=len(results) - passed_count,
        cases=results,
    )


def write_representative_examples(output_dir: str | Path) -> list[Path]:
    """Write canonical YAML examples from the packaged representative forms."""

    import yaml

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for case in representative_release_forms():
        result = run_unified_agent(UnifiedAgentRequest(
            input_kind="form",
            form_data=case["form"],
            output_dir=root / ".generated" / str(case["case_id"]),
        ))
        if not result.ok:
            raise RuntimeError(f"failed to generate release example {case['case_id']}: {result.validation.to_dict()}")
        path = root / f"{case['case_id']}.yaml"
        path.write_text(yaml.safe_dump(result.task_spec, sort_keys=False, allow_unicode=True), encoding="utf-8")
        paths.append(path)
    return paths


def _load_golden_evidence(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        baseline = release_baseline()
        return {
            "source": "packaged_release_baseline",
            "formal": bool(baseline.get("formal")),
            "report_sha256": None,
            **baseline,
        }
    report_path = Path(path)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Golden report root must be an object")
    return {
        "source": str(report_path.resolve()),
        "formal": True,
        "report_sha256": _sha256_file(report_path),
        "schema_version": payload.get("schema_version"),
        "ok": bool(payload.get("ok")),
        "total": payload.get("total"),
        "passed": payload.get("passed"),
        "repeat_count": payload.get("repeat_count"),
        "metrics": dict(payload.get("metrics") or {}),
    }


def _live_overclaim_guard() -> dict[str, Any]:
    result = run_unified_agent(UnifiedAgentRequest(
        input_kind="form",
        form_data={
            "task_id": "release_overclaim_probe",
            "capability_id": "component.battery.v1",
            "level": "component",
            "duration_s": 10.0,
            "sample_s": 5.0,
            "claim_level": "ground_calibrated",
            "parameter_profile": "demo",
        },
        output_dir=Path(tempfile.mkdtemp(prefix="sat-sim-overclaim-")),
    ))
    return {
        "ok": not result.ok,
        "reason_codes": list(result.reason_codes),
        "guards": result.guards.to_dict(),
    }


def evaluate_release_closure(
    *,
    golden_report: str | Path | None = None,
    output_root: str | Path = "reports/release_validation",
    source_root: str | Path | None = None,
    strict_assets: bool = False,
    execute_representative: bool = True,
) -> ReleaseClosureReport:
    """Evaluate the packaged release termination conditions."""

    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    doctor = run_environment_doctor(
        runs_root=root / "doctor-runs",
        artifacts_root=root / "doctor-artifacts",
        strict_assets=strict_assets,
        require_api=True,
        smoke=True,
    )
    golden = _load_golden_evidence(golden_report)
    metrics = dict(golden.get("metrics") or {})
    representative = run_representative_release_suite(output_root=root / "representative") if execute_representative else None
    manifest = release_manifest()
    checks: list[ReleaseCheck] = []

    generated_schema = CanonicalTaskSpec.model_json_schema(mode="validation")
    packaged_schema_resource = resources.files("sat_sim.schemas").joinpath("task_spec.schema.json")
    packaged_schema_bytes = packaged_schema_resource.read_bytes()
    packaged_schema = json.loads(packaged_schema_bytes.decode("utf-8"))
    packaged_schema_sha256 = hashlib.sha256(packaged_schema_bytes).hexdigest()
    schema_ok = (
        CANONICAL_TASK_SPEC_VERSION == manifest["task_spec_version"]
        and packaged_schema == {
            **generated_schema,
            "$id": "https://example.local/sat-sim/task-spec-v1.schema.json",
            "title": "Satellite Simulation Canonical TaskSpec v1.0",
        }
        and packaged_schema_sha256 == manifest.get("task_spec_schema_sha256")
    )
    checks.append(_check(
        "T01",
        "Canonical TaskSpec schema is stable",
        CheckStatus.PASS if schema_ok else CheckStatus.FAIL,
        f"TaskSpec version is {CANONICAL_TASK_SPEC_VERSION}; packaged schema matches Pydantic source of truth={schema_ok}.",
        evidence={
            "task_spec_version": CANONICAL_TASK_SPEC_VERSION,
            "schema_match": schema_ok,
            "schema_sha256": packaged_schema_sha256,
            "expected_schema_sha256": manifest.get("task_spec_schema_sha256"),
        },
    ))

    expected_golden_sha = manifest.get("golden_report_sha256")
    golden_hash_ok = golden.get("source") == "packaged_release_baseline" or not expected_golden_sha or golden.get("report_sha256") == expected_golden_sha
    golden_ok = (
        bool(golden.get("ok", golden.get("formal")))
        and int(golden.get("total", 0)) == 140
        and float(metrics.get("pass_power_5", 0.0)) == 1.0
        and golden_hash_ok
    )
    checks.append(_check(
        "T02", "Formal Golden Set meets release thresholds",
        CheckStatus.PASS if golden_ok else CheckStatus.FAIL,
        f"Golden total={golden.get('total')}, pass^5={metrics.get('pass_power_5')}, source={golden.get('source')}.",
        evidence=golden,
    ))

    unsupported_ok = float(metrics.get("unsupported_rejection_accuracy", 0.0)) == 1.0 and float(metrics.get("false_accept_rate", 1.0)) == 0.0
    checks.append(_check(
        "T03", "Unsupported requests cannot produce accepted pseudo-plans",
        CheckStatus.PASS if unsupported_ok else CheckStatus.FAIL,
        f"Unsupported rejection={metrics.get('unsupported_rejection_accuracy')}; false accept={metrics.get('false_accept_rate')}.",
        evidence={"unsupported_rejection_accuracy": metrics.get("unsupported_rejection_accuracy"), "false_accept_rate": metrics.get("false_accept_rate")},
    ))

    overclaim_probe = _live_overclaim_guard()
    claim_ok = float(metrics.get("overclaim_rate", 1.0)) == 0.0 and bool(overclaim_probe.get("ok"))
    checks.append(_check(
        "T04", "Parameter profile and Claim level cannot be escalated",
        CheckStatus.PASS if claim_ok else CheckStatus.FAIL,
        f"Golden overclaim rate={metrics.get('overclaim_rate')}; live demo→ground-calibrated probe blocked={overclaim_probe.get('ok')}.",
        evidence=overclaim_probe,
    ))

    deterministic_ok = (
        float(metrics.get("same_input_same_taskspec_rate", 0.0)) == 1.0
        and float(metrics.get("same_input_same_plan_rate", 0.0)) == 1.0
        and float(metrics.get("routing_flip_rate", 1.0)) == 0.0
        and float(metrics.get("claim_flip_rate", 1.0)) == 0.0
    )
    checks.append(_check(
        "T05", "TaskSpec and ExecutionPlan generation is deterministic",
        CheckStatus.PASS if deterministic_ok else CheckStatus.FAIL,
        "TaskSpec/Plan equality rates are 1.0 and route/claim flip rates are 0.0." if deterministic_ok else "Determinism metrics are below release thresholds.",
        evidence={key: metrics.get(key) for key in ("same_input_same_taskspec_rate", "same_input_same_plan_rate", "routing_flip_rate", "claim_flip_rate")},
    ))

    rep_cases = {item.case_id: item for item in representative.cases} if representative else {}
    levels_ok = bool(representative and all(
        any(item.level == level and item.run_status == "SUCCEEDED" for item in representative.cases)
        for level in ("component", "subsystem", "whole_spacecraft")
    ))
    checks.append(_check(
        "T06", "Component, subsystem and whole-spacecraft representative runs execute",
        CheckStatus.PASS if levels_ok else CheckStatus.FAIL,
        "All three simulation levels completed registered Runner execution." if levels_ok else "One or more simulation levels did not complete.",
        evidence={"cases": [item.model_dump(mode="json") for item in (representative.cases if representative else [])]},
    ))

    scenario_ok = bool(representative and all(
        any(item.category == category and item.passed for item in representative.cases)
        for category in ("nominal", "fault", "degradation", "combined")
    ))
    checks.append(_check(
        "T07", "Nominal, fault, degradation and combined scenarios have representative evidence",
        CheckStatus.PASS if scenario_ok else CheckStatus.FAIL,
        "Representative scenario categories all produced the expected honest outcomes." if scenario_ok else "Scenario evidence is incomplete.",
        evidence={"categories": sorted({item.category for item in (representative.cases if representative else []) if item.passed})},
    ))

    injection_cases = [item for item in (representative.cases if representative else []) if item.category in {"fault", "degradation"}]
    injection_ok = bool(injection_cases) and all(
        item.validation_result == "PASS"
        and item.injection_delivery_results
        and all(value == "PASS" for value in item.injection_delivery_results)
        and all(value == "PASS" for value in item.injection_effect_results)
        for item in injection_cases
    ) and float(metrics.get("silent_degradation_rate", 1.0)) == 0.0
    checks.append(_check(
        "T08", "Runtime injection delivery and direct effect-specific evidence are truthful",
        CheckStatus.PASS if injection_ok else CheckStatus.FAIL,
        "Delivery and registered implementation effects are independently verified; proxy and out-of-scope boundaries remain explicit." if injection_ok else "Injection truth boundary failed.",
        evidence={"cases": [item.model_dump(mode="json") for item in injection_cases], "silent_degradation_rate": metrics.get("silent_degradation_rate")},
    ))

    mission_case = rep_cases.get("mission_fail_preserved")
    mission_ok = bool(mission_case and mission_case.run_status == "SUCCEEDED" and mission_case.validation_result == "FAIL" and mission_case.passed)
    checks.append(_check(
        "T09", "Mission FAIL remains FAIL and is not auto-repaired",
        CheckStatus.PASS if mission_ok else CheckStatus.FAIL,
        "A deliberately impossible assertion completed simulation execution and retained ValidationOutcome=FAIL." if mission_ok else "Mission failure preservation check failed.",
        evidence={} if mission_case is None else mission_case.model_dump(mode="json"),
    ))

    try:
        from .agent_cli import build_parser
        from .api import create_app
        subparsers = next(action for action in build_parser()._actions if action.dest == "command")
        commands = set(subparsers.choices)
        app = create_app(runs_root=root / "api-runs", artifacts_root=root / "api-artifacts")
        paths = set(app.openapi()["paths"])
        missing_commands = sorted(set(manifest["required_cli_commands"]) - commands)
        missing_paths = sorted(set(manifest["required_api_paths"]) - paths)
        product_ok = not missing_commands and not missing_paths
        product_evidence = {"missing_commands": missing_commands, "missing_api_paths": missing_paths, "command_count": len(commands), "api_path_count": len(paths)}
    except Exception as exc:
        product_ok = False
        product_evidence = {"error": str(exc)}
    checks.append(_check(
        "T10", "CLI and local API cover the complete workflow",
        CheckStatus.PASS if product_ok else CheckStatus.FAIL,
        "All release CLI commands and API paths are present." if product_ok else "CLI/API surface is incomplete.",
        evidence=product_evidence,
    ))

    artifact_ok = bool(representative and representative.ok and all(
        item.bundle_integrity_ok
        and len(item.task_spec_sha256) == 64
        and len(item.resolved_spec_sha256) == 64
        and len(item.execution_plan_sha256) == 64
        for item in representative.cases
    ))
    checks.append(_check(
        "T11", "Run outputs retain versioned specs, hashes, provenance and ClaimReport",
        CheckStatus.PASS if artifact_ok else CheckStatus.FAIL,
        "All representative Run Bundles passed integrity checks and retained the TaskSpec→ResolvedSpec→Plan hash chain." if artifact_ok else "Run Bundle evidence is incomplete.",
        evidence={"representative_ok": representative.ok if representative else False, "case_count": representative.total if representative else 0},
    ))

    docs_root = Path(source_root) if source_root is not None else None
    if docs_root is None:
        docs_ok = True
        missing_docs: list[str] = []
        docs_status = CheckStatus.WARN
        docs_summary = "Source root was not supplied; packaged release manifest lists required documents but filesystem presence was not rechecked."
    else:
        missing_docs = [str(item) for item in manifest["required_documents"] if not (docs_root / str(item)).is_file()]
        docs_ok = not missing_docs
        docs_status = CheckStatus.PASS if docs_ok else CheckStatus.FAIL
        docs_summary = "All release and handoff documents are present." if docs_ok else f"Missing documents: {missing_docs}"
    checks.append(_check(
        "T12", "Documentation, examples and release handoff are complete",
        docs_status,
        docs_summary,
        required=docs_root is not None,
        evidence={"source_root": str(docs_root.resolve()) if docs_root else None, "missing_documents": missing_docs},
    ))

    summary = _summary(checks)
    report = ReleaseClosureReport(
        generated_at_utc=_utc_now(),
        ok=doctor.ok and _report_ok(checks),
        environment_doctor=doctor,
        golden_evidence=golden,
        representative_suite=representative,
        termination_conditions=checks,
        summary=summary,
        known_boundaries=list(manifest.get("known_boundaries", [])),
    )
    (root / "environment_doctor.json").write_text(report.environment_doctor.model_dump_json(indent=2) + "\n", encoding="utf-8")
    if representative is not None:
        (root / "representative_suite.json").write_text(representative.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (root / "release_closure_report.json").write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return report


__all__ = [
    "RELEASE_VERSION",
    "RELEASE_ID",
    "ENVIRONMENT_REPORT_VERSION",
    "RELEASE_REPORT_VERSION",
    "CheckStatus",
    "ReleaseCheck",
    "EnvironmentDoctorReport",
    "RepresentativeRunResult",
    "RepresentativeSuiteReport",
    "ReleaseClosureReport",
    "release_manifest",
    "release_baseline",
    "environment_doctor_schema",
    "release_closure_report_schema",
    "run_environment_doctor",
    "representative_release_forms",
    "run_representative_release_suite",
    "write_representative_examples",
    "evaluate_release_closure",
]
