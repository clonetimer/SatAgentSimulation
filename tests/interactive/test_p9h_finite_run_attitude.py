"""P9-H: real finite Basilisk Run preserves attitude through sealing and overview."""

from __future__ import annotations

import math

import pytest

from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle
from sat_sim.scenario_templates import instantiate_scenario_template
from sat_sim.telemetry_overview import read_telemetry_overview


SCENE_FIELDS = (
    "orbit.position_m_x", "orbit.position_m_y", "orbit.position_m_z",
    "attitude.truth.mrp_BN_x", "attitude.truth.mrp_BN_y", "attitude.truth.mrp_BN_z",
    "attitude.truth.quaternion_BN_q0", "attitude.truth.quaternion_BN_q1",
    "attitude.truth.quaternion_BN_q2", "attitude.truth.quaternion_BN_q3",
    "attitude.truth.valid",
)


def test_real_finite_basilisk_run_keeps_atomic_attitude_truth_after_seal(tmp_path):
    """Exercise actual Run Bundle execution, writer filtering, seal, and overview API."""
    spec = instantiate_scenario_template(
        "whole_spacecraft_unified_native", task_id="p9h_finite_attitude_e2e"
    )
    spec["simulation"].update({"duration_s": 2.0, "step_s": 0.1, "sample_s": 0.5})
    spec.setdefault("parameters", {}).setdefault("values", {})["initial_pointing_error_deg"] = 17.0

    # Select only an unrelated chart output. Scene support fields must remain
    # in the archived trace without being added to the user's plot selection.
    outputs = spec.setdefault("outputs", {})
    outputs["plots"] = ["eps.battery.soc"]
    prepared = prepare_run(spec, output_root=tmp_path / "runs", run_id="p9h_finite_attitude")
    execution = execute_prepared_run(
        prepared.bundle_root,
        expected_plan_sha256=prepared.execution_plan_sha256,
        max_attempts=1,
    )
    assert execution.run_record.status.value == "SUCCEEDED"
    assert execution.run_record.sealed is True

    integrity = verify_run_bundle(prepared.bundle_root)
    assert integrity["ok"] is True, integrity
    run_root = tmp_path / "runs" / "p9h_finite_attitude"
    telemetry_path = run_root / "results" / "telemetry.jsonl"
    assert telemetry_path.is_file()

    overview = read_telemetry_overview(
        run_root, max_points=100, fields=SCENE_FIELDS,
    )
    assert overview["source"] == "results/telemetry.jsonl"
    assert overview["total"] >= 4
    assert overview["sampled"] is False
    assert all(set(SCENE_FIELDS) <= set(row) for row in overview["rows"])

    for row in overview["rows"]:
        assert row["attitude.truth.valid"] == 1
        quaternion = [
            row[f"attitude.truth.quaternion_BN_q{i}"] for i in range(4)
        ]
        assert all(math.isfinite(value) for value in quaternion)
        assert math.hypot(*quaternion) == pytest.approx(1.0, abs=1e-10)
        assert all(math.isfinite(row[field]) for field in SCENE_FIELDS[:-1])

    # Position and attitude must be archived on precisely the same samples.
    with telemetry_path.open("r", encoding="utf-8") as stream:
        archived = [__import__("json").loads(line) for line in stream if line.strip()]
    assert [row["time_s"] for row in overview["rows"]] == [
        row["time_s"] for row in archived
    ]
