"""Fail-closed R85 qualification assessment of the frozen R84-R4 triaxis cohort.

This checks evidence consistency and reports research-use boundaries. It does not
infer a flight-hardware measurement model or an approved diagnostic threshold.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


EXPECTED_COHORTS = {"healthy_control", "loss50_test", "loss75_test"}


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _count(analysis: dict, noise: float, profile: str, cohort: str) -> dict:
    matches = [
        row for row in analysis.get("overall", [])
        if row.get("method") == "fusion"
        and row.get("noise_sigma_rad_s") == noise
        and row.get("wheel_profile") == profile
        and row.get("cohort") == cohort
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one fusion row for {noise}/{profile}/{cohort}, got {len(matches)}")
    row = matches[0]
    total, positive, negative, abstain = (row[key] for key in ("total", "positive", "negative", "abstain"))
    if any(type(value) is not int or value < 0 for value in (total, positive, negative, abstain)):
        raise ValueError("fusion counts must be nonnegative integers")
    if total == 0 or total != positive + negative + abstain:
        raise ValueError("fusion counts are incomplete")
    return {"total": total, "positive": positive, "negative": negative, "abstain": abstain}


def assess(prereg_path: Path, manifest_path: Path, audit_path: Path, analysis_path: Path) -> dict:
    evidence = {name: path for name, path in (
        ("preregistration", prereg_path), ("manifest", manifest_path),
        ("independent_audit", audit_path), ("blind_analysis", analysis_path),
    )}
    try:
        hashes = {name: _sha256(path) for name, path in evidence.items()}
        prereg, manifest, audit, analysis = (_load(path) for path in evidence.values())
        expected = prereg["design"]["unique_native_cases"]
        linked = hashes["preregistration"]
        if any(item.get("preregistration_sha256") != linked for item in (manifest, audit, analysis)):
            raise ValueError("preregistration digest mismatch")
        if prereg.get("status") != "FROZEN_BEFORE_EXECUTION":
            raise ValueError("preregistration not frozen")
        if (manifest.get("status"), audit.get("status"), analysis.get("status")) != ("PASS", "PASS", "ANALYZED"):
            raise ValueError("native evidence chain has a failed or unknown stage")
        if any(item.get(key) != expected for item, key in (
            (manifest, "unique_native_cases"), (audit, "unique_native_cases"), (analysis, "native_cases"),
        )):
            raise ValueError("native case count mismatch")
        if manifest.get("artifact_sha256") != audit.get("artifact_sha256"):
            raise ValueError("raw artifact digest mismatch")
        if audit.get("prior_seed_overlap") != 0 or manifest.get("prior_seed_overlap") != 0:
            raise ValueError("native seed overlap")
        if audit.get("source_and_qa_isolation") is not True or audit.get("all_three_axes_observed") is not True:
            raise ValueError("source or QA isolation is unverified")
        noise_levels = prereg["design"]["imu_noise_sigmas_rad_s"]
        profiles = {item["name"]: item for item in prereg["wheel_measurement_profiles"]}
        if set(profiles) != {"nominal", "stressed"} or len(noise_levels) != 3:
            raise ValueError("unexpected noise or wheel profile design")
        counts = {}
        for noise in noise_levels:
            for profile in profiles:
                rows = {cohort: _count(analysis, noise, profile, cohort) for cohort in EXPECTED_COHORTS}
                if len({row["total"] for row in rows.values()}) != 1:
                    raise ValueError("cohort denominators differ")
                counts[f"{noise:g}/{profile}"] = rows
        if any(item.get("formal_dataset_eligible") is not False for item in (manifest, audit, analysis)):
            raise ValueError("upstream R84 qualification boundary changed")
        if any("uncalibrated" not in item.get("basis", "") for item in profiles.values()):
            raise ValueError("wheel measurement provenance changed; reassess manually")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return {"schema": "sat-sim.r85.candidate-assessment.v1", "status": "INSUFFICIENT_EVIDENCE",
                "formal_dataset_eligible": False, "reason": str(exc),
                "evidence_paths": {name: str(path) for name, path in evidence.items()}}

    primary = prereg["design"]["primary_imu_sigma_rad_s"]
    primary_counts = counts[f"{primary:g}/nominal"]
    healthy = primary_counts["healthy_control"]
    loss50 = primary_counts["loss50_test"]
    stressed = counts[f"{max(noise_levels):g}/stressed"]
    gates = {
        "native_verification_and_lineage": {"status": "PASS", "evidence": ["manifest", "independent_audit"]},
        "measurement_validation_for_target_hardware": {
            "status": "HOLD", "reason": "Wheel sample rate, resolution, bias, noise, dropout and timing are uncalibrated sensitivity assumptions."},
        "independent_healthy_false_alarm": {
            "status": "HOLD", "reason": "No approved false-alarm acceptance limit; report exact independent healthy count.",
            "primary_nominal": healthy},
        "loss50_distinguishability": {
            "status": "HOLD", "reason": "Current frozen fusion probe does not reliably separate loss50 from independent healthy controls.",
            "primary_nominal": loss50,
            "positive_rate_difference": round(loss50["positive"] / loss50["total"] - healthy["positive"] / healthy["total"], 8)},
        "loss75_robustness": {
            "status": "HOLD", "reason": "High-IMU-noise and stressed wheel measurement reduce detection and vary by context.",
            "high_noise_stressed": stressed["loss75_test"]},
        "cross_platform_reproducibility": {
            "status": "NOT_ASSESSED", "reason": "This assessment accepts one local evidence chain; cloud evidence requires a separate linked receipt."},
    }
    return {
        "schema": "sat-sim.r85.candidate-assessment.v1",
        "context_of_use": "generic LEO smallsat synthetic RW-fault diagnostic dataset candidate; no flight qualification",
        "status": "NOT_QUALIFIED",
        "formal_dataset_eligible": False,
        "native_cases": expected,
        "evidence_sha256": hashes,
        "fusion_counts_by_noise_and_wheel_profile": counts,
        "gates": gates,
        "next_evidence": [
            "selected wheel hardware specification or bench calibration for measurement parameters",
            "preapproved false-alarm and fault-detection acceptance criteria with independent controls",
            "independent loss50 study and high-noise context-level loss75 replication",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = assess(args.prereg, args.manifest, args.audit, args.analysis)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{result['status']}: {args.report}")
    if result["status"] == "INSUFFICIENT_EVIDENCE":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
