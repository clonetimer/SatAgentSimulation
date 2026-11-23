"""Verify a downloaded guided whole-spacecraft wheel-loss bundle without extracting it."""
import hashlib
import json
import sys
from pathlib import Path
from zipfile import ZipFile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def audit(path):
    metadata = json.loads(path.with_suffix(".json").read_bytes())
    with path.open("rb") as stream:
        archive_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    assert archive_hash == metadata["bundle_zip_sha256"]
    prefix = metadata["run_id"] + "/"
    with ZipFile(path) as archive:
        def read(name):
            return archive.read(prefix + name)
        manifest = read("bundle_manifest.json")
        seal = json.loads(read("SEALED.json"))
        assert seal["run_id"] == metadata["run_id"]
        assert digest(manifest) == metadata["bundle_manifest_sha256"] == seal["bundle_manifest_sha256"]
        members = json.loads(manifest)["files"]
        expected = {prefix + name for name in (*members, "bundle_manifest.json", "SEALED.json")}
        assert len(archive.namelist()) == len(expected) and set(archive.namelist()) == expected
        for name, entry in members.items():
            assert digest(read(name)) == entry["sha256"]
        spec = json.loads(read("input/task_spec.json"))
        event, = spec["events"]["degradations"]
        assert event["effect"] == metadata["event_effect"] == "adcs_rw_torque_authority_loss"
        assert not spec["events"]["faults"] and not spec["events"]["constraints"]
        idx, scale = event["parameters"]["wheel_index"], event["parameters"]["torque_scale"]
        assert idx == 0 and scale == .25 and (event["start_s"],event["end_s"]) == (4.,10.)
        result = json.loads(read("validation/validation_outcome.json"))
        item, = result["injection_evidence"]
        assert result["result"] == item["delivery_result"] == item["effect_result"] == "PASS"
        assert item["event_id"] == event["id"]
        rows = [json.loads(line) for line in read("results/telemetry.jsonl").splitlines()]
        active = [r for r in rows if event["effect"] in r["event.active_effects"]]
        assert len(active) == item["active_sample_count"] and active
        for row in rows:
            ratio = row[f"adcs.rw.effective_torque_ratio_{idx}"]
            assert abs(ratio - (scale if event["effect"] in row["event.active_effects"] else 1.)) < 1e-12
            assert all(abs(row[f"adcs.rw.effective_torque_ratio_{i}"]-1.)<1e-12 for i in (1,2))
        return dict(run_id=metadata["run_id"], archive_sha256=archive_hash, manifest_sha256=digest(manifest),
                    verified_files=len(members), rows=len(rows), event_id=item["event_id"],
                    active_times_s=[r["time_s"] for r in active], wheel_index=idx, remaining_ratio=scale)


if __name__ == "__main__":
    Path(sys.argv[2]).write_text(json.dumps(audit(Path(sys.argv[1])),indent=2)+"\n")
