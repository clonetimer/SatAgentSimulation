"""BSK-GATE-1 Basilisk runtime benchmark consolidation and claim gate.

This module intentionally separates *runtime evidence* from *claims*.  Runtime
smoke or partial benchmark coverage is useful engineering evidence, but it is
not a high-fidelity or flight-validation claim unless separate external truth
correlation and validation evidence is supplied.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv
import datetime as _dt
import json
from typing import Any, Mapping

from .basilisk_orbit import run_basilisk_orbit_benchmark
from .basilisk_adcs import run_basilisk_adcs_benchmark
from .basilisk_6dof import run_basilisk_6dof_integration_benchmark

BASILISK_RUNTIME_GATE_SCHEMA_VERSION = "bsk_gate1.runtime_claim_gate.v1"

CLAIM_LEVEL_ORDER: tuple[str, ...] = (
    "no_evidence",
    "config_prepared",
    "runtime_smoke_passed",
    "benchmark_partial_passed",
    "benchmark_full_passed",
    "high_fidelity_candidate",
    "flight_validated",
)

DOMAIN_SPECS: tuple[dict[str, str], ...] = (
    {
        "domain": "orbit",
        "capability_id": "orbit_environment.basilisk_hf.v1",
        "benchmark_key": "basilisk_benchmark_status",
        "report_filename": "basilisk_orbit_benchmark_report.json",
        "case_pass_status": "basilisk_benchmark_passed",
    },
    {
        "domain": "adcs_fsw",
        "capability_id": "subsystem.adcs_basilisk_fsw.v1",
        "benchmark_key": "basilisk_adcs_benchmark_status",
        "report_filename": "basilisk_adcs_benchmark_report.json",
        "case_pass_status": "basilisk_adcs_benchmark_passed",
    },
    {
        "domain": "whole_spacecraft_6dof",
        "capability_id": "whole_spacecraft.basilisk_6dof.v1",
        "benchmark_key": "basilisk_6dof_benchmark_status",
        "report_filename": "basilisk_6dof_integration_benchmark_report.json",
        "case_pass_status": "basilisk_6dof_runtime_passed",
    },
)

DEFAULT_EXTERNAL_EVIDENCE: dict[str, dict[str, bool]] = {
    spec["capability_id"]: {
        "external_truth_correlation_passed": False,
        "requirements_trace_reviewed": False,
        "flight_validated": False,
        "remote_ci_executed": False,
    }
    for spec in DOMAIN_SPECS
}


def _timestamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _as_bool(value: Any) -> bool:
    return bool(value) if value is not None else False


def _evidence_level_index(level: str) -> int:
    try:
        return CLAIM_LEVEL_ORDER.index(level)
    except ValueError:
        return 0


def _min_evidence_level(levels: list[str]) -> str:
    if not levels:
        return "no_evidence"
    return min(levels, key=_evidence_level_index)


def _max_evidence_level(levels: list[str]) -> str:
    if not levels:
        return "no_evidence"
    return max(levels, key=_evidence_level_index)


def _contains_no_implicit_fallback_policy(report: Mapping[str, Any]) -> bool:
    policy = str(report.get("fallback_policy") or "").lower()
    if "never implicit fallback" in policy or "no implicit fallback" in policy:
        return True
    rows = report.get("local_proxy_comparisons") or []
    if isinstance(rows, list) and rows:
        return all(str(row.get("fallback_policy") or "").lower() == "explicit_comparison_only_not_implicit_fallback" for row in rows)
    return False


@dataclass(frozen=True)
class RuntimeDomainInput:
    """One domain's benchmark report plus external validation evidence."""

    domain: str
    capability_id: str
    benchmark_key: str
    report: Mapping[str, Any]
    external_evidence: Mapping[str, Any]


def classify_runtime_domain(domain_input: RuntimeDomainInput) -> dict[str, Any]:
    """Classify one benchmark report into the BSK-GATE-1 claim taxonomy."""

    report = domain_input.report
    case_count = _as_int(report.get("case_count"))
    runtime_pass_count = _as_int(report.get("runtime_pass_count"))
    prepared_count = _as_int(report.get("prepared_count"))
    configured_not_executed_count = _as_int(report.get("configured_not_executed_count"))
    runtime_unavailable_count = _as_int(report.get("runtime_unavailable_count"))
    fail_count = _as_int(report.get("fail_count"))
    benchmark_status = str(report.get(domain_input.benchmark_key) or "unknown")
    report_status = str(report.get("status") or "unknown")
    benchmark_passed = report_status == "pass" and fail_count == 0
    no_implicit_fallback_confirmed = _contains_no_implicit_fallback_policy(report)

    config_prepared = case_count > 0 and report_status == "pass"
    runtime_smoke_passed = benchmark_passed and runtime_pass_count > 0
    benchmark_full_passed = benchmark_passed and case_count > 0 and runtime_pass_count == case_count
    benchmark_partial_passed = benchmark_passed and runtime_pass_count > 0 and runtime_pass_count < case_count

    external_truth_correlation_passed = _as_bool(domain_input.external_evidence.get("external_truth_correlation_passed"))
    requirements_trace_reviewed = _as_bool(domain_input.external_evidence.get("requirements_trace_reviewed"))
    flight_validated = _as_bool(domain_input.external_evidence.get("flight_validated"))
    remote_ci_executed = _as_bool(domain_input.external_evidence.get("remote_ci_executed"))

    # High-fidelity candidacy is intentionally stricter than a benchmark pass.
    # A local benchmark must be full, an explicit no-fallback policy must be in
    # force, and independent external truth/requirements evidence must be present.
    high_fidelity_candidate = bool(
        benchmark_full_passed
        and no_implicit_fallback_confirmed
        and external_truth_correlation_passed
        and requirements_trace_reviewed
    )

    if flight_validated:
        evidence_level = "flight_validated"
    elif high_fidelity_candidate:
        evidence_level = "high_fidelity_candidate"
    elif benchmark_full_passed:
        evidence_level = "benchmark_full_passed"
    elif benchmark_partial_passed:
        evidence_level = "benchmark_partial_passed"
    elif runtime_smoke_passed:
        evidence_level = "runtime_smoke_passed"
    elif config_prepared or prepared_count > 0 or configured_not_executed_count > 0:
        evidence_level = "config_prepared"
    else:
        evidence_level = "no_evidence"

    suspicious_report_claim = report.get("can_claim_high_fidelity") is True and not high_fidelity_candidate
    false_claim_blocked = not high_fidelity_candidate and not flight_validated
    can_claim_high_fidelity = bool(high_fidelity_candidate)
    can_claim_flight_validated = bool(flight_validated)

    allowed_claims: list[str] = []
    if config_prepared:
        allowed_claims.append("configuration prepared and benchmark harness available")
    if runtime_smoke_passed:
        allowed_claims.append("at least one Basilisk runtime smoke case executed")
    if benchmark_partial_passed:
        allowed_claims.append("partial Basilisk benchmark runtime coverage")
    if benchmark_full_passed:
        allowed_claims.append("full local Basilisk benchmark case coverage")
    if high_fidelity_candidate:
        allowed_claims.append("high-fidelity candidate pending governance/release approval")
    if flight_validated:
        allowed_claims.append("flight-validated for the supplied validation evidence scope")

    disallowed_claims = []
    if not high_fidelity_candidate:
        disallowed_claims.append("package-level or capability-level high-fidelity claim")
    if not flight_validated:
        disallowed_claims.append("flight validation")
    if not remote_ci_executed:
        disallowed_claims.append("remote CI runtime pass")
    if runtime_pass_count < case_count:
        disallowed_claims.append("full benchmark runtime coverage")

    return {
        "domain": domain_input.domain,
        "capability_id": domain_input.capability_id,
        "schema_version": report.get("schema_version"),
        "report_status": report_status,
        "benchmark_status": benchmark_status,
        "case_count": case_count,
        "runtime_pass_count": runtime_pass_count,
        "prepared_count": prepared_count,
        "configured_not_executed_count": configured_not_executed_count,
        "runtime_unavailable_count": runtime_unavailable_count,
        "fail_count": fail_count,
        "config_prepared": config_prepared,
        "runtime_smoke_passed": runtime_smoke_passed,
        "benchmark_partial_passed": benchmark_partial_passed,
        "benchmark_full_passed": benchmark_full_passed,
        "high_fidelity_candidate": high_fidelity_candidate,
        "flight_validated": flight_validated,
        "external_truth_correlation_passed": external_truth_correlation_passed,
        "requirements_trace_reviewed": requirements_trace_reviewed,
        "remote_ci_executed": remote_ci_executed,
        "no_implicit_fallback_confirmed": no_implicit_fallback_confirmed,
        "suspicious_report_claim": suspicious_report_claim,
        "false_claim_blocked": false_claim_blocked,
        "can_claim_high_fidelity": can_claim_high_fidelity,
        "can_claim_flight_validated": can_claim_flight_validated,
        "evidence_level": evidence_level,
        "allowed_claims": allowed_claims,
        "disallowed_claims": disallowed_claims,
    }


def _synthetic_report(*, case_count: int, runtime_pass_count: int, can_claim_high_fidelity: bool = False) -> dict[str, Any]:
    return {
        "schema_version": "synthetic.false_claim_regression.v1",
        "status": "pass",
        "basilisk_benchmark_status": "basilisk_runtime_passed" if runtime_pass_count == case_count else "partial_basilisk_runtime_coverage",
        "case_count": case_count,
        "runtime_pass_count": runtime_pass_count,
        "prepared_count": 0,
        "configured_not_executed_count": max(case_count - runtime_pass_count, 0),
        "runtime_unavailable_count": 0,
        "fail_count": 0,
        "can_claim_high_fidelity": can_claim_high_fidelity,
        "fallback_policy": "local proxy comparisons are explicit baselines, never implicit fallback for synthetic capability",
    }


def run_false_claim_regressions() -> list[dict[str, Any]]:
    """Return deterministic false-claim regression checks for the gate itself."""

    checks: list[tuple[str, RuntimeDomainInput, str]] = [
        (
            "runtime_smoke_only_does_not_equal_high_fidelity",
            RuntimeDomainInput("synthetic_runtime_smoke", "synthetic.runtime_smoke.v1", "basilisk_benchmark_status", _synthetic_report(case_count=6, runtime_pass_count=1), {}),
            "benchmark_partial_passed",
        ),
        (
            "partial_benchmark_does_not_equal_flight_validation",
            RuntimeDomainInput("synthetic_partial", "synthetic.partial.v1", "basilisk_benchmark_status", _synthetic_report(case_count=6, runtime_pass_count=3), {}),
            "benchmark_partial_passed",
        ),
        (
            "benchmark_full_without_external_truth_is_not_high_fidelity_candidate",
            RuntimeDomainInput("synthetic_full_no_truth", "synthetic.full_no_truth.v1", "basilisk_benchmark_status", _synthetic_report(case_count=3, runtime_pass_count=3), {}),
            "benchmark_full_passed",
        ),
        (
            "malicious_report_high_fidelity_flag_is_rejected_without_truth",
            RuntimeDomainInput("synthetic_malicious", "synthetic.malicious.v1", "basilisk_benchmark_status", _synthetic_report(case_count=3, runtime_pass_count=3, can_claim_high_fidelity=True), {}),
            "benchmark_full_passed",
        ),
    ]
    results: list[dict[str, Any]] = []
    for check_id, domain_input, expected_level in checks:
        result = classify_runtime_domain(domain_input)
        passed = (
            result["evidence_level"] == expected_level
            and result["can_claim_high_fidelity"] is False
            and result["can_claim_flight_validated"] is False
        )
        if check_id == "malicious_report_high_fidelity_flag_is_rejected_without_truth":
            passed = passed and result["suspicious_report_claim"] is True
        results.append({
            "check_id": check_id,
            "status": "pass" if passed else "fail",
            "expected_evidence_level": expected_level,
            "observed_evidence_level": result["evidence_level"],
            "can_claim_high_fidelity": result["can_claim_high_fidelity"],
            "can_claim_flight_validated": result["can_claim_flight_validated"],
            "suspicious_report_claim": result["suspicious_report_claim"],
        })
    return results


def _default_external_evidence(overrides: Mapping[str, Mapping[str, Any]] | None) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {cap: dict(values) for cap, values in DEFAULT_EXTERNAL_EVIDENCE.items()}
    for capability_id, values in (overrides or {}).items():
        evidence.setdefault(capability_id, {})
        evidence[capability_id].update(dict(values))
    return evidence


def _benchmark_reports_from_runners(report_dir: Path) -> dict[str, Mapping[str, Any]]:
    benchmark_root = report_dir / "benchmarks"
    return {
        "orbit": run_basilisk_orbit_benchmark(benchmark_root / "orbit"),
        "adcs_fsw": run_basilisk_adcs_benchmark(benchmark_root / "adcs_fsw"),
        "whole_spacecraft_6dof": run_basilisk_6dof_integration_benchmark(benchmark_root / "whole_spacecraft_6dof"),
    }


def build_basilisk_runtime_claim_gate_report(
    report_dir: str | Path,
    *,
    benchmark_reports: Mapping[str, Mapping[str, Any]] | None = None,
    external_evidence: Mapping[str, Mapping[str, Any]] | None = None,
    run_benchmarks: bool = True,
) -> dict[str, Any]:
    """Build and write the consolidated BSK-GATE-1 claim-gate report."""

    out = Path(report_dir)
    out.mkdir(parents=True, exist_ok=True)
    reports = dict(benchmark_reports or {})
    if run_benchmarks and not benchmark_reports:
        reports = _benchmark_reports_from_runners(out)
    elif not reports:
        raise ValueError("benchmark_reports must be supplied when run_benchmarks=False")

    evidence = _default_external_evidence(external_evidence)
    domains: list[dict[str, Any]] = []
    for spec in DOMAIN_SPECS:
        domain = spec["domain"]
        if domain not in reports:
            raise KeyError(f"Missing benchmark report for domain {domain!r}")
        domains.append(
            classify_runtime_domain(
                RuntimeDomainInput(
                    domain=domain,
                    capability_id=spec["capability_id"],
                    benchmark_key=spec["benchmark_key"],
                    report=reports[domain],
                    external_evidence=evidence.get(spec["capability_id"], {}),
                )
            )
        )

    false_claim_regressions = run_false_claim_regressions()
    total_case_count = sum(_as_int(row["case_count"]) for row in domains)
    total_runtime_pass_count = sum(_as_int(row["runtime_pass_count"]) for row in domains)
    total_configured_not_executed_count = sum(_as_int(row["configured_not_executed_count"]) for row in domains)
    total_runtime_unavailable_count = sum(_as_int(row["runtime_unavailable_count"]) for row in domains)
    total_fail_count = sum(_as_int(row["fail_count"]) for row in domains)
    evidence_levels = [str(row["evidence_level"]) for row in domains]
    package_can_claim_high_fidelity = bool(domains) and all(row["high_fidelity_candidate"] for row in domains)
    package_flight_validated = bool(domains) and all(row["flight_validated"] for row in domains)
    suspicious_claim_count = sum(1 for row in domains if row["suspicious_report_claim"])
    no_implicit_fallback_pass_count = sum(1 for row in domains if row["no_implicit_fallback_confirmed"])
    regression_fail_count = sum(1 for row in false_claim_regressions if row["status"] != "pass")
    gate_status = "pass" if total_fail_count == 0 and suspicious_claim_count == 0 and regression_fail_count == 0 else "fail"

    report = {
        "schema_version": BASILISK_RUNTIME_GATE_SCHEMA_VERSION,
        "generated_at_utc": _timestamp(),
        "status": gate_status,
        "gate_version": "BSK-GATE-1",
        "claim_taxonomy": list(CLAIM_LEVEL_ORDER),
        "aggregate": {
            "domain_count": len(domains),
            "case_count": total_case_count,
            "runtime_pass_count": total_runtime_pass_count,
            "configured_not_executed_count": total_configured_not_executed_count,
            "runtime_unavailable_count": total_runtime_unavailable_count,
            "fail_count": total_fail_count,
            "runtime_coverage_ratio": (total_runtime_pass_count / total_case_count) if total_case_count else 0.0,
            "minimum_domain_evidence_level": _min_evidence_level(evidence_levels),
            "maximum_domain_evidence_level": _max_evidence_level(evidence_levels),
            "no_implicit_fallback_pass_count": no_implicit_fallback_pass_count,
            "suspicious_claim_count": suspicious_claim_count,
            "false_claim_regression_fail_count": regression_fail_count,
        },
        "domains": domains,
        "false_claim_regressions": false_claim_regressions,
        "package_claim_decision": {
            "can_claim_package_high_fidelity": package_can_claim_high_fidelity,
            "flight_validated": package_flight_validated,
            "remote_ci_executed": all(row["remote_ci_executed"] for row in domains) if domains else False,
            "claim_guardrail": "Runtime smoke or partial benchmark coverage is not a high-fidelity, flight-validation, or remote-CI claim.",
            "allowed_package_claims": [
                "Basilisk runtime smoke evidence exists for orbit, ADCS/FSW, and 6-DOF domains",
                "Basilisk benchmark coverage is partial and explicitly scoped by domain",
                "Local proxy comparisons are explicit baselines, not implicit fallbacks",
            ],
            "disallowed_package_claims": [
                "package-level high fidelity",
                "flight validation",
                "external truth correlation",
                "remote CI runtime pass",
            ],
        },
    }

    _write_reports(out, report)
    return report


def _write_reports(out: Path, report: Mapping[str, Any]) -> None:
    (out / "basilisk_runtime_claim_gate_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "readiness_matrix.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    with (out / "runtime_coverage_matrix.csv").open("w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "domain",
            "capability_id",
            "report_status",
            "benchmark_status",
            "case_count",
            "runtime_pass_count",
            "prepared_count",
            "configured_not_executed_count",
            "runtime_unavailable_count",
            "fail_count",
            "evidence_level",
            "benchmark_partial_passed",
            "benchmark_full_passed",
            "high_fidelity_candidate",
            "flight_validated",
            "can_claim_high_fidelity",
            "no_implicit_fallback_confirmed",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in report["domains"]:
            writer.writerow({key: row.get(key) for key in fieldnames})

    with (out / "claim_decision_matrix.csv").open("w", newline="", encoding="utf-8") as f:
        fieldnames = ["domain", "capability_id", "allowed_claims", "disallowed_claims"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in report["domains"]:
            writer.writerow({
                "domain": row.get("domain"),
                "capability_id": row.get("capability_id"),
                "allowed_claims": "; ".join(row.get("allowed_claims") or []),
                "disallowed_claims": "; ".join(row.get("disallowed_claims") or []),
            })

    lines = [
        "# BSK-GATE-1 Basilisk Runtime Claim Gate Report",
        "",
        f"schema_version: `{report['schema_version']}`",
        f"status: `{report['status']}`",
        "",
        "## Aggregate",
        "",
        f"- case_count: `{report['aggregate']['case_count']}`",
        f"- runtime_pass_count: `{report['aggregate']['runtime_pass_count']}`",
        f"- configured_not_executed_count: `{report['aggregate']['configured_not_executed_count']}`",
        f"- runtime_unavailable_count: `{report['aggregate']['runtime_unavailable_count']}`",
        f"- runtime_coverage_ratio: `{report['aggregate']['runtime_coverage_ratio']:.6f}`",
        f"- minimum_domain_evidence_level: `{report['aggregate']['minimum_domain_evidence_level']}`",
        f"- maximum_domain_evidence_level: `{report['aggregate']['maximum_domain_evidence_level']}`",
        "",
        "## Package claim decision",
        "",
        f"- can_claim_package_high_fidelity: `{report['package_claim_decision']['can_claim_package_high_fidelity']}`",
        f"- flight_validated: `{report['package_claim_decision']['flight_validated']}`",
        f"- remote_ci_executed: `{report['package_claim_decision']['remote_ci_executed']}`",
        "",
        "## Domain matrix",
        "",
        "| Domain | Runtime pass | Cases | Evidence level | High fidelity candidate | Flight validated |",
        "|---|---:|---:|---|---:|---:|",
    ]
    for row in report["domains"]:
        lines.append(
            f"| {row['domain']} | {row['runtime_pass_count']} | {row['case_count']} | {row['evidence_level']} | {row['high_fidelity_candidate']} | {row['flight_validated']} |"
        )
    lines.extend([
        "",
        "## False-claim regressions",
        "",
        "| Check | Status | Observed level |",
        "|---|---:|---|",
    ])
    for row in report["false_claim_regressions"]:
        lines.append(f"| {row['check_id']} | {row['status']} | {row['observed_evidence_level']} |")
    lines.append("")
    (out / "basilisk_runtime_claim_gate_report.md").write_text("\n".join(lines), encoding="utf-8")
