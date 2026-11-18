from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle
from sat_sim.scenario_templates import instantiate_scenario_template


@pytest.mark.parametrize(
    "star_mount,imu_mount,mag_mount",
    [
        pytest.param((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), id="identity-mounts"),
        pytest.param((13.0, -7.0, 5.0), (-11.0, 6.0, 9.0), (8.0, -4.0, 12.0), id="non-identity-mounts"),
    ],
)
def test_adcs_native_subsystem_sealed_run_bundle_and_frame_contract(
    tmp_path: Path,
    star_mount: tuple[float, float, float],
    imu_mount: tuple[float, float, float],
    mag_mount: tuple[float, float, float],
) -> None:
    """Verify measured sensor frames, closed-loop response and sealed evidence in official Basilisk."""
    pytest.importorskip("Basilisk")
    run_id = "native_adcs_frame_contract"
    spec = instantiate_scenario_template("adcs_unified_native", task_id=run_id)
    spec["simulation"].update(duration_s=30.0, step_s=0.2, sample_s=1.0)
    values = spec["parameters"]["values"]
    values.update(
        initial_pointing_error_deg=12.0,
        star_tracker_mount_yaw_deg=star_mount[0],
        star_tracker_mount_pitch_deg=star_mount[1],
        star_tracker_mount_roll_deg=star_mount[2],
        imu_mount_yaw_deg=imu_mount[0],
        imu_mount_pitch_deg=imu_mount[1],
        imu_mount_roll_deg=imu_mount[2],
        magnetometer_mount_yaw_deg=mag_mount[0],
        magnetometer_mount_pitch_deg=mag_mount[1],
        magnetometer_mount_roll_deg=mag_mount[2],
        star_tracker_noise_std_arcsec=0.0,
        gyro_base_noise_std_rad_s=0.0,
    )
    spec["assurance"]["allow_proxy"] = False

    runs_root = tmp_path / "runs"
    prepared = prepare_run(spec, output_root=runs_root, run_id=run_id)
    assert prepared.primary_capability_id == "subsystem.adcs_unified_native.v1"
    executed = execute_prepared_run(
        prepared.bundle_root,
        expected_plan_sha256=prepared.execution_plan_sha256,
        max_attempts=1,
        hard_timeout=True,
    )

    assert executed.run_record.status.value == "SUCCEEDED", executed.run_record.model_dump(mode="json")
    assert executed.validation.result.value == "PASS", executed.validation.model_dump(mode="json")
    integrity = verify_run_bundle(prepared.bundle_root)
    assert integrity["ok"] is True, integrity

    summary = executed.summary
    assert summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
    assert summary["official_module_count"] >= 8
    assert summary["project_native_module_count"] >= 4
    assert summary["external_or_proxy_module_count"] == 0
    assert summary["adcs_closed_loop_improved"] is True
    fusion_evidence = summary["adcs_fusion"]
    assert fusion_evidence["attitude_frame"] == "body_B_relative_to_inertial_N"
    assert fusion_evidence["body_rate_frame"] == "body_B"
    assert fusion_evidence["magnetometer_used_sample_count"] > 0
    assert fusion_evidence["estimator_class"] == "deterministic_engineering_estimator_not_mekf_or_kalman"

    rows = executed.trace_rows
    assert len(rows) >= 20
    assert rows[-1]["adcs.pointing_error_deg"] < rows[0]["adcs.pointing_error_deg"]
    assert any(row["adcs.fusion.magnetometer_used"] == 1 for row in rows)
    for row in rows:
        assert all(
            key in row
            for key in (
                "adcs.fusion.estimated_mrp_BN_x",
                "adcs.fusion.estimated_omega_BN_B_x",
                "adcs.fusion.magnetometer_used",
                "attitude.truth.omega_BN_B_x",
            )
        )
    first = rows[0]
    # The first recorder row is the Reset snapshot: truth and star attitude
    # are available, but the scheduled IMU has not yet emitted its first tick.
    measured_rate = rows[1]
    for axis in "xyz":
        assert first[f"adcs.fusion.estimated_mrp_BN_{axis}"] == pytest.approx(
            first[f"attitude.truth.mrp_BN_{axis}"], abs=2.0e-4
        )
        assert measured_rate[f"adcs.fusion.estimated_omega_BN_B_{axis}"] == pytest.approx(
            measured_rate[f"attitude.truth.omega_BN_B_{axis}"], abs=2.0e-6
        )

    assert executed.dataset is not None
    dataset_root = Path(executed.dataset.output_root)
    manifest = json.loads((dataset_root / "manifest.json").read_text(encoding="utf-8"))
    boundary = manifest["runtime_evidence"]["model_source_boundary"]
    official_sources = " ".join(str(item) for item in boundary["basilisk_official_native"]).lower()
    assert "star_tracker" in official_sources
    assert "imu" in official_sources
    assert "magnetometer" in official_sources
    assert "project_adcs_sensor_fusion" in boundary["basilisk_project_native"]
    assert boundary["external_or_proxy"] == []

    app = create_app(
        runs_root=runs_root,
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=False,
        auth_mode="disabled",
    )
    with TestClient(app) as client:
        detail = client.get(f"/runs/{run_id}")
        telemetry = client.get(f"/runs/{run_id}/telemetry?limit=500")
        streams = client.get(f"/runs/{run_id}/telemetry-streams")
        plots = client.get(f"/runs/{run_id}/plots")
        fmea = client.get(f"/runs/{run_id}/fmea")
        bundle = client.get(f"/runs/{run_id}/bundle/download")

    for response in (detail, telemetry, streams, plots, fmea):
        assert response.status_code == 200, response.text
    durable = detail.json()["run"]
    assert durable["sealed"] is True
    assert durable["integrity"]["ok"] is True
    assert durable["validation_outcome"]["result"] == "PASS"
    assert durable["summary"]["adcs_fusion"]["magnetometer_used_sample_count"] > 0
    assert telemetry.json()["rows"]
    assert plots.json()["plot_manifest"]["series"]
    stream_ids = {item["stream_id"] for item in streams.json()["multi_rate_telemetry"]["streams"]}
    assert {"adcs_fast", "adcs_housekeeping"} <= stream_ids

    assert bundle.status_code == 200, bundle.text
    assert bundle.headers["X-Sat-Sim-Run-ID"] == run_id
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        manifest_name = f"{run_id}/bundle_manifest.json"
        seal_name = f"{run_id}/SEALED.json"
        manifest_bytes = archive.read(manifest_name)
        sealed = json.loads(archive.read(seal_name))
        archived = set(archive.namelist())
        expected = {f"{run_id}/{relative}" for relative in sealed_manifest_files(manifest_bytes)}
        assert archived == expected | {manifest_name, seal_name}
        assert sealed["run_id"] == run_id
        assert sealed["bundle_manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
        assert bundle.headers["X-Sat-Sim-Run-Bundle-Manifest-SHA256"] == sealed["bundle_manifest_sha256"]
        manifest_doc = json.loads(manifest_bytes)
        for relative, entry in manifest_doc["files"].items():
            assert hashlib.sha256(archive.read(f"{run_id}/{relative}")).hexdigest() == entry["sha256"]


def sealed_manifest_files(manifest_bytes: bytes) -> set[str]:
    manifest = json.loads(manifest_bytes)
    return set(manifest["files"])
