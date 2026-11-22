import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from compare_battery_five_point import compare, digest, SAMPLES, STEP_S


def fixture(tmp_path, *, flags=False, anticorrelated=False):
    reference = tmp_path / "10.csv"
    model = tmp_path / "model.csv"
    headers = ["time_s", *(f"T{i}" for i in range(1, 6))]
    if flags:
        headers += [f"valid{i}" for i in range(1, 6)]
    with reference.open("w", newline="", encoding="utf-8") as f, model.open("w", newline="", encoding="utf-8") as g:
        a, b = csv.writer(f), csv.writer(g)
        a.writerow(headers)
        b.writerow(["time_s", "thermal.battery_temp_c"])
        for i in range(SAMPLES):
            t = round(i * STEP_S, 9)
            swing = (1 if i % 2 else -1) if anticorrelated else 0
            a.writerow([t, 14 + swing, 14.5 - swing, 15, 15.5, 16,
                        *([1, 1, 1, 1, 1] if flags else [])])
            b.writerow([t, 15.25])
    model_stats = tmp_path / "model_statistics.json"
    model_stats.write_text(json.dumps({"model_telemetry": {"sha256": digest(model)}}), encoding="utf-8")
    mapping = {
        "schema_version": "battery-five-point-map.v1",
        "source_kind": "simulation_output_not_hardware_or_flight",
        "comparison_target": "thermal.battery_temp_c",
        "reference_csv_sha256": digest(reference),
        "status": "CONFIRMED_BY_REFERENCE_OWNER",
        "mapping_evidence_uri": "test-fixture://explicit-mapping",
        "time_column": "time_s",
        "quality_policy": "per_channel_valid_flags" if flags else "all_rows_reviewed_valid",
        "channels": [
            {"column": f"T{i}", "package_id": "BAT-A", "measurement_location": f"fixture point {i}",
             "temperature_definition": "synthetic sensor temperature", "weight_basis": "fixture equal masses",
             "unit": "degC", "weight": .2, "valid_column": f"valid{i}" if flags else None}
            for i in range(1, 6)
        ],
    }
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
    return reference, mapping_path, model, model_stats


def test_timewise_weighted_temperature_compares_on_frozen_full_window(tmp_path):
    result = compare(*fixture(tmp_path))
    assert result["status"] == "WITHIN_FROZEN_TOLERANCE"
    assert result["common_valid_count"] == SAMPLES
    assert result["reference"]["mean_c"] == pytest.approx(15.)
    assert result["model"]["mean_c"] == pytest.approx(15.25)
    assert result["difference_model_minus_reference"]["mean_c"] == pytest.approx(.25)


def test_anticorrelated_channels_are_aggregated_before_population_std(tmp_path):
    result = compare(*fixture(tmp_path, anticorrelated=True))
    assert result["reference"]["std_population_c"] == pytest.approx(0., abs=1e-14)
    assert result["status"] == "WITHIN_FROZEN_TOLERANCE"


def test_unconfirmed_mapping_fails_closed(tmp_path):
    paths = fixture(tmp_path)
    obj = json.loads(paths[1].read_text())
    obj["status"] = "PENDING"
    paths[1].write_text(json.dumps(obj))
    with pytest.raises(ValueError, match="MAPPING_NOT_CONFIRMED"):
        compare(*paths)


def test_mixed_battery_packages_are_not_aggregated(tmp_path):
    paths = fixture(tmp_path)
    obj = json.loads(paths[1].read_text())
    obj["channels"][4]["package_id"] = "BAT-B"
    paths[1].write_text(json.dumps(obj))
    with pytest.raises(ValueError, match="CHANNEL_DUPLICATE_OR_MIXED_PACKAGE"):
        compare(*paths)


def test_declared_validity_mask_is_common_and_blocks_qualification(tmp_path):
    paths = fixture(tmp_path, flags=True)
    lines = paths[0].read_text().splitlines()
    fields = lines[50].split(",")
    fields[-1] = "0"
    lines[50] = ",".join(fields)
    paths[0].write_text("\n".join(lines) + "\n")
    obj = json.loads(paths[1].read_text())
    obj["reference_csv_sha256"] = digest(paths[0])
    paths[1].write_text(json.dumps(obj))
    result = compare(*paths)
    assert result["status"] == "INCONCLUSIVE_INVALID_SAMPLES"
    assert result["common_valid_count"] == SAMPLES - 1
    assert result["invalid_or_excluded_count"] == 1


def test_changed_reference_bytes_or_time_grid_fail_closed(tmp_path):
    paths = fixture(tmp_path)
    lines = paths[0].read_text().splitlines()
    fields = lines[25].split(",")
    fields[0] = "999"
    lines[25] = ",".join(fields)
    paths[0].write_text("\n".join(lines) + "\n")
    with pytest.raises(ValueError, match="REFERENCE_SHA256_MISMATCH"):
        compare(*paths)
    obj = json.loads(paths[1].read_text())
    obj["reference_csv_sha256"] = digest(paths[0])
    paths[1].write_text(json.dumps(obj))
    with pytest.raises(ValueError, match="TIME_GRID_MISMATCH"):
        compare(*paths)

