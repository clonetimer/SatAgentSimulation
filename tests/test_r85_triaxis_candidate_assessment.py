"""R85 verdict must remain fail-closed when a receipt is missing or tampered."""

import json
from pathlib import Path

from scripts.assess_r85_triaxis_candidate import assess


def _fixture(tmp_path: Path):
    prereg = {"status": "FROZEN_BEFORE_EXECUTION", "design": {
        "unique_native_cases": 12, "imu_noise_sigmas_rad_s": [0.00005, 0.0001, 0.0002],
        "primary_imu_sigma_rad_s": 0.0001}, "wheel_measurement_profiles": [
        {"name": "nominal", "basis": "uncalibrated assumption"},
        {"name": "stressed", "basis": "uncalibrated assumption"}]}
    paths = [tmp_path / name for name in ("prereg.json", "manifest.json", "audit.json", "analysis.json")]
    paths[0].write_text(json.dumps(prereg), encoding="utf-8")
    import hashlib
    digest = hashlib.sha256(paths[0].read_bytes()).hexdigest()
    manifest = {"status": "PASS", "preregistration_sha256": digest, "unique_native_cases": 12,
                "prior_seed_overlap": 0, "artifact_sha256": {"raw": "abc"}, "formal_dataset_eligible": False}
    audit = {**manifest, "source_and_qa_isolation": True, "all_three_axes_observed": True}
    analysis = {"status": "ANALYZED", "preregistration_sha256": digest,
                "native_cases": 12, "formal_dataset_eligible": False, "overall": []}
    for noise in prereg["design"]["imu_noise_sigmas_rad_s"]:
        for profile in ("nominal", "stressed"):
            for cohort, positive in (("healthy_control", 1), ("loss50_test", 1), ("loss75_test", 3)):
                analysis["overall"].append({"noise_sigma_rad_s": noise, "wheel_profile": profile,
                    "method": "fusion", "cohort": cohort, "total": 4, "positive": positive,
                    "negative": 4-positive, "abstain": 0})
    for path, item in zip(paths[1:], (manifest, audit, analysis)):
        path.write_text(json.dumps(item), encoding="utf-8")
    return paths


def test_valid_chain_yields_research_only_verdict(tmp_path):
    result = assess(*_fixture(tmp_path))
    assert result["status"] == "NOT_QUALIFIED"
    assert result["formal_dataset_eligible"] is False
    assert result["gates"]["native_verification_and_lineage"]["status"] == "PASS"


def test_missing_or_tampered_evidence_fails_closed(tmp_path):
    paths = _fixture(tmp_path)
    paths[2].unlink()
    assert assess(*paths)["status"] == "INSUFFICIENT_EVIDENCE"
    paths = _fixture(tmp_path)
    item = json.loads(paths[3].read_text(encoding="utf-8"))
    item["overall"][0]["positive"] = 99
    paths[3].write_text(json.dumps(item), encoding="utf-8")
    assert assess(*paths)["status"] == "INSUFFICIENT_EVIDENCE"
