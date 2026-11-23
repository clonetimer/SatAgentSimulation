"""Compare sealed runs that differ in their external space-weather snapshot."""
import argparse
import csv
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_bytes())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect(bundle):
    manifest = read(bundle / "bundle_manifest.json")
    manifest_hash = digest(bundle / "bundle_manifest.json")
    assert manifest_hash == read(bundle / "SEALED.json")["bundle_manifest_sha256"]
    for name, entry in manifest["files"].items():
        path = (bundle / name).resolve()
        assert path.is_relative_to(bundle.resolve()) and digest(path) == entry["sha256"]
    env = read(bundle / "results/summary.json")["orbital_environment"]
    weather = env["space_weather"]
    source = Path(weather["path"])
    assert digest(source) == weather["sha256"]
    assert digest(source.with_name("space_weather_manifest.json")) == weather["provenance_manifest_sha256"]
    assert weather["coverage_status"] == "PASS" and manifest["validation_result"] == "PASS"
    trace = list(map(json.loads, (bundle / "results/telemetry.jsonl").read_bytes().splitlines()))
    return weather, trace, dict(manifest_sha256=manifest_hash, members=len(manifest["files"]),
                                spec_sha256=digest(bundle / "input/task_spec.json"))


def compare(fixture_root, observed_root, case):
    fixture, before, first = inspect(fixture_root / case)
    observed, after, second = inspect(observed_root / case)
    assert first["spec_sha256"] == second["spec_sha256"] and fixture["sha256"] != observed["sha256"]
    assert len(before) == len(after) == 16
    assert observed["source_url"] == "https://celestrak.org/SpaceData/SW-Last5Years.csv"
    assert observed["retrieved_at_utc"] and not observed["retrieval_time_unknown"]
    with Path(observed["path"]).open(encoding="utf-8-sig") as f:
        records = {r["DATE"]: r for r in csv.DictReader(f)}
    begin = date.fromisoformat(observed["coverage_start_utc_date"])
    end = date.fromisoformat(observed["coverage_end_utc_date"])
    days = [(begin + timedelta(days=i)).isoformat() for i in range((end-begin).days+1)]
    assert len(days) == 4 and all(records[d]["F10.7_DATA_TYPE"] == "OBS" for d in days)
    metrics = []
    for a,b in zip(before, after, strict=True):
        assert a["time_s"] == b["time_s"]
        item = {"time_s": a["time_s"]}
        for key in ("environment.atmosphere_density_kg_m3", "orbit.drag_force_norm_n"):
            item[key] = dict(fixture=a[key], observed=b[key])
            if a["time_s"] > 0:
                assert 0 < b[key] < a[key]
        metrics.append(item)
    return dict(case=case, fixture=first, observed=second, fixture_sha256=fixture["sha256"],
                observed_sha256=observed["sha256"], provenance_sha256=observed["provenance_manifest_sha256"],
                retrieved_at=observed["retrieved_at_utc"], observed_dates=days,
                first_invalid_source_row=observed["first_invalid_row"], samples=metrics)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("fixture", "observed", "output"):
        p.add_argument(name, type=Path)
    args = p.parse_args()
    result = [compare(args.fixture, args.observed, case) for case in ("combined-control", "combined-burn")]
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for item in result:
        tail = item["samples"][-1]
        print(item["case"], item["observed"]["members"], tail)
