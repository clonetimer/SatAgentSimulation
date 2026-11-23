"""Verify two evidence manifests, then extract per-case causal outcomes for review."""
import argparse
import hashlib
import json
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summarize(root):
    manifest = root / "evidence_manifest.json"
    members = json.loads(manifest.read_bytes())["files"]
    for name, expected in members.items():
        member = root / name
        if member.stat().st_size != expected["size_bytes"] or digest(member) != expected["sha256"]:
            raise ValueError(f"Evidence member mismatch: {name}")
    report_path = root / "coupling_causality_suite_report.json"
    report = json.loads(report_path.read_bytes())
    return {
        "report_sha256": digest(report_path),
        "manifest_sha256": digest(manifest),
        "verified_member_count": len(members),
        "runtime_coupling_coverage": report["runtime_coupling_coverage"],
        **{field: report[field] for field in (
            "status", "matrix_sha256", "baseline_spec_sha256", "case_count",
            "pass_count", "fail_count", "inconclusive_count",
        )},
        "cases": [{
            "case_id": case["case_id"],
            "status": case["status"],
            "reason_codes": case["reason_codes"],
            "checks": case["causal_evaluation"]["checks"],
        } for case in report["cases"]],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("original", "faster_recording", "final", "output"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    summary = {name: summarize(getattr(args, name)) for name in ("original", "faster_recording", "final")}
    args.output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
