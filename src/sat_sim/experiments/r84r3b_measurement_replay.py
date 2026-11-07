"""Read-only R3A evidence replay through an explicit R3B wheel-speed model."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sat_sim.experiments.native_imu_rw_multichannel_pilot_r84r3 import TELEMETRY_FIELDS
from sat_sim.experiments.r84r3b_wheel_speed_measurement import (
    WheelSpeedMeasurementAssumptions, observe_wheel_speeds,
)

SOURCE_NAME = "r84r3_multichannel_telemetry.jsonl"
SPEED = {f"sensor.adcs.rw.speed_rad_s_{i}" for i in range(3)}
INPUT_KEYS = {"time_s", *TELEMETRY_FIELDS}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def replay(evidence_dir: Path, profile: Path, output_dir: Path) -> dict:
    manifest_bytes = (evidence_dir / "r84r3_execution_manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("native_execution_count_including_replay") != 13:
        raise ValueError("R3A execution count mismatch")
    for name, expected in manifest["artifact_sha256"].items():
        candidate = (evidence_dir / name).resolve()
        if not candidate.is_relative_to(evidence_dir.resolve()) or digest(candidate.read_bytes()) != expected:
            raise ValueError(f"R3A evidence hash mismatch: {name}")
    profile_bytes = profile.read_bytes()
    config = json.loads(profile_bytes)
    if config.get("qualification") != "sensitivity_assumptions_only":
        raise ValueError("profile must disclose research-only qualification")
    if config.get("source_manifest_sha256") != digest(manifest_bytes):
        raise ValueError("R3A source manifest does not match pinned profile")
    params = WheelSpeedMeasurementAssumptions(**config["parameters"])
    source_bytes = (evidence_dir / SOURCE_NAME).read_bytes()
    cases = [json.loads(line) for line in source_bytes.splitlines() if line.strip()]
    case_manifest = [json.loads(line) for line in (evidence_dir / "r84r3_case_manifest.jsonl").read_bytes().splitlines() if line.strip()]
    replay_of = {r["record_id"]: r.get("replay_of_record_id", r["record_id"]) for r in case_manifest}
    if len(cases) != 13 or len(replay_of) != 13 or len(set(replay_of.values())) != 12:
        raise ValueError("R3A unique case or replay mismatch")
    records = []
    for case in cases:
        record_id = case["record_id"]
        rows = case["rows"]
        if record_id not in replay_of or len(rows) != 31 or any(set(row) != INPUT_KEYS for row in rows):
            raise ValueError(f"R3A telemetry schema mismatch: {record_id}")
        seed = int.from_bytes(hashlib.sha256((config["seed_namespace"] + ":" + replay_of[record_id]).encode()).digest()[:7], "big")
        measured = observe_wheel_speeds(rows, params, seed=seed)
        output_rows = []
        for sample_index, item in enumerate(measured):
            original = rows[sample_index * params.sample_every_n_native_points]
            output = {key: value for key, value in original.items() if key not in SPEED}
            output.update(item)
            if any("QA_ONLY" in key or "fault" in key or "label" in key for key in output):
                raise ValueError("truth entered model observation")
            if any(output[key] != original[key] for key in INPUT_KEYS - SPEED):
                raise ValueError("non-wheel channel changed")
            output_rows.append(output)
        records.append({"record_id": record_id, "rows": output_rows,
                        "repeatability_control": bool(case.get("repeatability_control", False))})
    data = "".join(json.dumps(record, sort_keys=True, allow_nan=False) + "\n" for record in records).encode()
    output_dir.mkdir(parents=True, exist_ok=False)
    destination = output_dir / "r84r3b_measured_telemetry.jsonl"
    destination.write_bytes(data)
    result = {"schema": "sat-sim.r84r3b.measurement-replay.v1",
              "qualification": "sensitivity_assumptions_only", "formal_dataset_eligible": False,
              "source_manifest_sha256": digest(manifest_bytes), "source_telemetry_sha256": digest(source_bytes),
              "profile_sha256": digest(profile_bytes), "output_sha256": digest(data),
              "case_count": len(records), "unique_case_count": 12,
              "row_count": sum(len(record["rows"]) for record in records),
              "missing_wheel_values": sum(not row[f"wheel_{i}_valid"] for record in records for row in record["rows"] for i in range(3))}
    (output_dir / "r84r3b_measurement_manifest.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(replay(args.evidence_dir, args.profile, args.output_dir), indent=2))


if __name__ == "__main__":
    main()
