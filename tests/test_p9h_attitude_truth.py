"""P9-H attitude truth conversion and Basilisk recorder contract."""
import math
from pathlib import Path

import pytest

from sat_sim.bsk_engine.unified_native import _attitude_truth_from_mrp


@pytest.mark.parametrize(
    "sigma",
    [
        (0.0, 0.0, 0.0),
        (math.tan(math.pi / 8), 0.0, 0.0),
        (0.0, -math.tan(math.pi / 6), 0.0),
        (0.1, -0.2, 0.05),
        (0.0, 0.0, 1.0),
    ],
)
def test_mrp_truth_conversion_is_scalar_first_unit_quaternion(sigma):
    fields = _attitude_truth_from_mrp(sigma)
    quaternion = [fields[f"attitude.truth.quaternion_BN_q{i}"] for i in range(4)]
    assert fields["attitude.truth.valid"] == 1
    assert [fields[f"attitude.truth.mrp_BN_{axis}"] for axis in "xyz"] == list(sigma)
    assert quaternion == pytest.approx([
        (1 - sum(value * value for value in sigma)) / (1 + sum(value * value for value in sigma)),
        *(2 * value / (1 + sum(value * value for value in sigma)) for value in sigma),
    ])
    assert math.sqrt(sum(value * value for value in quaternion)) == pytest.approx(1.0, abs=1e-12)


@pytest.mark.parametrize("sigma", [(), (1.0, 2.0), (1.0, 2.0, 3.0, 4.0), (0.0, float("nan"), 0.0), (0.0, float("inf"), 0.0)])
def test_mrp_truth_conversion_rejects_malformed_input(sigma):
    with pytest.raises(ValueError):
        _attitude_truth_from_mrp(sigma)


def test_whole_orbit_stream_contains_atomic_position_and_attitude_truth_fields():
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "src/sat_sim/interactive/api.py"
    text = source.read_text(encoding="utf-8")
    required = [
        "orbit.position_m_x", "orbit.position_m_y", "orbit.position_m_z",
        "attitude.truth.mrp_BN_x", "attitude.truth.mrp_BN_y", "attitude.truth.mrp_BN_z",
        "attitude.truth.quaternion_BN_q0", "attitude.truth.quaternion_BN_q1",
        "attitude.truth.quaternion_BN_q2", "attitude.truth.quaternion_BN_q3",
        "attitude.truth.valid",
    ]
    stream = text.split('"stream_id": "whole.orbit"', 1)[1].split("]", 1)[0]
    for field in required:
        assert f'"{field}"' in stream, field


def test_native_row_builder_reads_spacecraft_truth_not_star_tracker_measurement():
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "src/sat_sim/bsk_engine/unified_native.py"
    text = source.read_text(encoding="utf-8")
    assert "_attitude_truth_from_mrp(state.sigma_BN[index])" in text
    assert '"attitude.truth.quaternion_BN_q0"' in text
    assert '"adcs.star_tracker.q0"' in text

def test_bounded_and_continuous_clients_require_atomic_position_attitude_stream():
    root = Path(__file__).resolve().parents[1]
    app = (root / "src/sat_sim/web/app.js").read_text(encoding="utf-8")
    continuous = (root / "src/sat_sim/web/continuous.js").read_text(encoding="utf-8")
    assert "const ORBIT_SCENE_FIELDS" in app
    assert "fields: ORBIT_SCENE_FIELDS" in app
    assert "sceneStateFields.every(field => (row.fields || []).includes(field))" in continuous
    for field in (
        "attitude.truth.quaternion_BN_q0",
        "attitude.truth.quaternion_BN_q1",
        "attitude.truth.quaternion_BN_q2",
        "attitude.truth.quaternion_BN_q3",
        "attitude.truth.valid",
    ):
        assert field in app and field in continuous


def test_sealed_run_scene_never_invents_missing_epoch_or_time_system():
    root = Path(__file__).resolve().parents[1]
    app = (root / "src/sat_sim/web/app.js").read_text(encoding="utf-8")
    assert "|| '2026-01-01T00:00:00Z'" not in app
    assert "|| null;" in app
    assert "runTimeSystem === 'UTC'" in app
    assert "typeof runEpoch === 'string' && runEpoch.length > 0" in app
