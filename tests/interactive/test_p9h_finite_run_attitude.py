"""P9-H: real finite Basilisk attitude survives Run trace filtering and overview."""

from __future__ import annotations

import math
import json

from sat_sim.bsk_engine.unified_native import (
    WHOLE_UNIFIED_CAPABILITY_ID,
    UnifiedNativeRuntime,
    UnifiedRuntimeConfig,
)
from sat_sim.dataset_writer import _filter_rows, write_jsonl
from sat_sim.telemetry_overview import read_telemetry_overview


SCENE_FIELDS = (
    "orbit.position_m_x", "orbit.position_m_y", "orbit.position_m_z",
    "attitude.truth.mrp_BN_x", "attitude.truth.mrp_BN_y", "attitude.truth.mrp_BN_z",
    "attitude.truth.quaternion_BN_q0", "attitude.truth.quaternion_BN_q1",
    "attitude.truth.quaternion_BN_q2", "attitude.truth.quaternion_BN_q3",
    "attitude.truth.valid",
)


def test_real_finite_basilisk_run_keeps_atomic_attitude_truth_in_run_overview(tmp_path):
    """Exercise real spacecraft state -> trace writer filter -> bounded Run overview."""
    runtime = UnifiedNativeRuntime(UnifiedRuntimeConfig(
        capability_id=WHOLE_UNIFIED_CAPABILITY_ID,
        duration_s=2.0,
        step_s=0.1,
        sample_s=0.5,
        adcs_only=False,
        values={"initial_pointing_error_deg": 17.0},
        events=(),
    )).run()

    assert runtime.summary["overall_status"] == "complete"
    raw_rows = list(runtime.trace_rows)
    assert len(raw_rows) >= 4
    assert all("attitude.truth.quaternion_BN_q0" in row for row in raw_rows)

    # Mimic a user selecting only an unrelated chart curve. The dataset writer
    # must still preserve scene support channels in the same immutable rows.
    selected_rows = _filter_rows(
        raw_rows,
        ("eps.battery.soc",),
        include_fields=SCENE_FIELDS,
    )
    assert len(selected_rows) == len(raw_rows)
    assert all(set(SCENE_FIELDS) <= set(row) for row in selected_rows)

    run_root = tmp_path / "finite-run"
    telemetry_path = run_root / "results" / "telemetry.jsonl"
    write_jsonl(telemetry_path, selected_rows)
    overview = read_telemetry_overview(
        run_root, max_points=100, fields=SCENE_FIELDS,
    )

    assert overview["source"] == "results/telemetry.jsonl"
    assert overview["total"] == len(raw_rows)
    assert overview["sampled"] is False
    assert [row["time_s"] for row in overview["rows"]] == [
        row["time_s"] for row in raw_rows
    ]
    for row in overview["rows"]:
        assert row["attitude.truth.valid"] == 1
        quaternion = [
            row[f"attitude.truth.quaternion_BN_q{i}"] for i in range(4)
        ]
        assert all(math.isfinite(value) for value in quaternion)
        assert math.hypot(*quaternion) == pytest.approx(1.0, abs=1e-10)
        assert all(math.isfinite(row[field]) for field in SCENE_FIELDS[:-1])
    # Position and attitude must be present on exactly the same sample.
    assert all(
        all(field in row for field in SCENE_FIELDS)
        for row in overview["rows"]
    )
