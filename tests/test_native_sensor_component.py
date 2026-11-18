from __future__ import annotations

import pytest


def test_native_star_tracker_probe_uses_official_sensor_and_records_truth_response():
    pytest.importorskip("Basilisk")
    from sat_sim.bsk_engine.native_sensor_component import run_native_star_tracker_probe

    result = run_native_star_tracker_probe(
        duration_s=1.0,
        sample_s=0.1,
        truth_sigma_bn=(0.04, -0.01, 0.02),
        truth_omega_bn_b_rad_s=(0.01, 0.0, -0.005),
        noise_std_rad=0.0,
        random_walk_std_rad_s=0.0,
    )

    assert result.model_source == "Basilisk.simulation.starTracker.StarTracker"
    assert result.input_source == "explicit_SCStatesMsg_truth_fixture"
    assert result.claim_scope.endswith("without_optical_star_catalog")
    assert len(result.times_s) == len(result.q_inrtl_to_case) == len(result.omega_inrtl_to_case_rad_s)
    assert len(result.times_s) >= 2
    assert result.times_s[0] >= 0.0
    assert result.times_s[-1] >= 1.0
    assert all(abs(sum(value * value for value in q) - 1.0) < 0.02 for q in result.q_inrtl_to_case)


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"duration_s": 0.0}, "DURATION"),
        ({"sample_s": 0.0}, "SAMPLE"),
        ({"truth_sigma_bn": (0.0, 1.0)}, "TRUTH_SIGMA"),
        ({"truth_omega_bn_rad_s": (0.0, float("nan"), 0.0)}, "TRUTH_OMEGA"),
        ({"seed": -1}, "SEED"),
        ({"noise_std_rad": float("inf")}, "NOISE"),
    ],
)
def test_native_star_tracker_probe_rejects_invalid_inputs_before_basilisk_import(kwargs, reason):
    from sat_sim.bsk_engine.native_sensor_component import run_native_star_tracker_probe

    with pytest.raises(ValueError, match=reason):
        run_native_star_tracker_probe(**kwargs)
