"""Check sealed nominal/candidate runs for loss or change of pre-existing telemetry."""
import argparse
import hashlib
import json
from pathlib import Path

from sat_sim.run_bundle import verify_run_bundle

EXTRA = {"orbit.eclipse_spacecraft_source_time_s", "orbit.eclipse_spacecraft_geometry_valid"}
EXTRA.update(f"orbit.eclipse_spacecraft_position_m_{axis}" for axis in "xyz")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(base, candidate):
    manifests = {}
    for folder in (base, candidate):
        assert verify_run_bundle(folder)["ok"]
        manifests[str(folder)] = digest(folder / "bundle_manifest.json")
    spec = Path("input/task_spec.json")
    assert (base/spec).read_bytes() == (candidate/spec).read_bytes()
    summary = Path("results/summary.json")
    old_env, new_env = [json.loads((p/summary).read_bytes())["orbital_environment"] for p in (base, candidate)]
    assert old_env == new_env
    assert (base/"validation/validation_outcome.json").read_bytes() == (candidate/"validation/validation_outcome.json").read_bytes()
    trace = Path("results/telemetry.jsonl")
    total = count = 0
    sizes = set()
    with (base/trace).open("rb") as left, (candidate/trace).open("rb") as right:
        for first, second in zip(left, right, strict=True):
            a,b = json.loads(first), json.loads(second)
            assert set(b)-set(a) == EXTRA
            for name in EXTRA:
                del b[name]
            assert a == b, a["time_s"]
            sizes.add(len(a))
            total += len(a)
            count += 1
    assert count == 2701
    return dict(rows=count, existing_fields_per_row=sorted(sizes), identical_field_values=total,
                extra_fields=sorted(EXTRA), spec_sha256=digest(base/spec),
                weather_sha256=old_env["space_weather"]["sha256"],
                baseline_manifest=manifests[str(base)], candidate_manifest=manifests[str(candidate)],
                validation_sha256=digest(base/"validation/validation_outcome.json"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("base","candidate","output"):
        parser.add_argument(key,type=Path)
    args = parser.parse_args()
    result = audit(args.base,args.candidate)
    args.output.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print(result)
