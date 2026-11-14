"""Audit the R84-R3B observation-only replay against pinned R3A telemetry.

用途：只读核验固定 R3A 遥测与测量模型的零扰动和敏感性回放。
参数：--source-dir、--identity-dir、--sensitivity-dir、--report。
输出：机器可读收据；不执行 Basilisk，不训练模型，不授予数据资格。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_bytes().splitlines() if line.strip()]


def audit(source_dir: Path, identity_dir: Path, sensitivity_dir: Path) -> dict:
    source = source_dir / "r84r3_multichannel_telemetry.jsonl"
    native = records(source)
    ideal_path = identity_dir / "r84r3b_measured_telemetry.jsonl"
    trial_path = sensitivity_dir / "r84r3b_measured_telemetry.jsonl"
    ideal, trial = records(ideal_path), records(trial_path)
    assert len(native) == len(ideal) == len(trial) == 13
    speed_keys = {f"sensor.adcs.rw.speed_rad_s_{i}" for i in range(3)}
    changed = missing = 0
    for original, unchanged, measured in zip(native, ideal, trial):
        assert original["record_id"] == unchanged["record_id"] == measured["record_id"]
        assert len(original["rows"]) == len(unchanged["rows"]) == 31
        assert len(measured["rows"]) == 16
        for reference, control in zip(original["rows"], unchanged["rows"]):
            assert all(control[key] == value for key, value in reference.items())
            assert all(control[f"wheel_{wheel}_valid"] is True for wheel in range(3))
        for index, observation in enumerate(measured["rows"]):
            reference = original["rows"][index * 2]
            assert all(observation[key] == value for key, value in reference.items() if key not in speed_keys)
            assert not any("QA_ONLY" in key or "fault" in key or "label" in key for key in observation)
            for wheel in range(3):
                key = f"sensor.adcs.rw.speed_rad_s_{wheel}"
                valid = observation[f"wheel_{wheel}_valid"]
                assert valid is (observation[key] is not None)
                assert observation[f"wheel_{wheel}_receipt_time_s"] >= observation["time_s"]
                missing += not valid
                changed += valid and observation[key] != reference[key]
    replay_idx = next(index for index, record in enumerate(native) if record["record_id"].startswith("r84r3_seeded_replay"))
    assert native[replay_idx]["rows"] == native[11]["rows"]
    assert trial[replay_idx]["rows"] == trial[11]["rows"]
    assert changed > 0 and missing > 0
    for root, file in ((identity_dir, ideal_path), (sensitivity_dir, trial_path)):
        manifest = json.loads((root / "r84r3b_measurement_manifest.json").read_text(encoding="utf-8"))
        assert manifest["output_sha256"] == sha(file)
        assert manifest["source_telemetry_sha256"] == sha(source)
        assert manifest["formal_dataset_eligible"] is False
    return {"status": "PASS", "qualification": "sensitivity_assumptions_only",
            "source_telemetry_sha256": sha(source), "cases": 13,
            "identity_rows": 403, "sensitivity_rows": 208,
            "changed_wheel_values": changed, "missing_wheel_values": missing,
            "nonwheel_channels_unchanged": True, "truth_excluded": True,
            "replay_exact": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--identity-dir", type=Path, required=True)
    parser.add_argument("--sensitivity-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    receipt = audit(args.source_dir, args.identity_dir, args.sensitivity_dir)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
