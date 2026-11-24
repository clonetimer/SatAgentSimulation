"""DT1-A2 — actual native solar radiation pressure, Sun/Eclipse/force A/B.

Run only under official Basilisk 2.11.0 with the pinned offline SPICE assets.
The 20 m², Cr=1.5 configuration is an explicit test fixture, not a spacecraft
CAD measurement, production default, or flight calibration.
"""
from dataclasses import replace
import math

import numpy as np
import pytest

from sat_sim.bsk_engine.srp_environment import NativeSrpSelection
from sat_sim.bsk_engine.unified_native import (
    UnifiedNativeRuntime, UnifiedRuntimeConfig, WHOLE_UNIFIED_CAPABILITY_ID
)

AU_M = 149_597_870_693.0
SOLAR_FLUX_EARTH_W_M2 = 1361.0
SPEED_LIGHT_M_S = 299_792_458.0


def _config(**values):
    return UnifiedRuntimeConfig(
        capability_id=WHOLE_UNIFIED_CAPABILITY_ID,
        duration_s=300., step_s=2., sample_s=10.,
        adcs_only=False, values=values, events=(),
    )


def _position(row):
    return np.array([row[f"orbit.position_m_{axis}"] for axis in "xyz"])


@pytest.mark.parametrize("values, reason", [
    ({"srp_model":"facet"}, "NATIVE_SRP_MODEL_UNSUPPORTED"),
    ({"srp_model":"cannonball"}, "NATIVE_SRP_REQUIRES_SPICE_WMM"),
    ({"srp_model":"cannonball","environment_profile":"spice_wmm"},
     "NATIVE_SRP_EXPLICIT_AREA_AND_COEFFICIENT_REQUIRED"),
    ({"srp_model":"cannonball","environment_profile":"spice_wmm",
      "srp_reference_area_m2":-2.,"srp_coefficient_reflection":1.5},
     "NATIVE_SRP_OPTICAL_PARAMETERS_INVALID"),
    ({"srp_model":"cannonball","environment_profile":"spice_wmm",
      "srp_reference_area_m2":20.,"srp_coefficient_reflection":float("nan")},
     "NATIVE_SRP_OPTICAL_PARAMETERS_INVALID"),
    ({"srp_model":"cannonball","environment_profile":"spice_wmm",
      "srp_reference_area_m2":True,"srp_coefficient_reflection":1.5},
     "NATIVE_SRP_OPTICAL_PARAMETERS_INVALID"),
])
def test_srp_request_contract_fails_closed_without_native_physics(values, reason):
    with pytest.raises(ValueError, match=reason):
        NativeSrpSelection.from_values(
            values, environment_profile=str(values.get("environment_profile", "simplified")))


def test_srp_disabled_by_default_is_a_true_no_change_selection():
    item = NativeSrpSelection.from_values({}, environment_profile="simplified")
    assert item.model == "disabled"
    assert item.enabled is False
    assert item.reference_area_m2 is None


def test_opt_in_srp_rejects_adcs_only():
    with pytest.raises(ValueError, match="NATIVE_SRP_REQUIRES_WHOLE_SPACECRAFT"):
        NativeSrpSelection.from_values(
            {"srp_model":"cannonball","srp_reference_area_m2":20.,
             "srp_coefficient_reflection":1.5},
            environment_profile="spice_wmm",adcs_only=True)


def test_same_real_spice_sun_and_eclipse_drive_native_cannonball_force(capsys):
    base = {
        "environment_profile": "spice_wmm",
        "epoch_utc": "2026-01-01T00:00:00Z",
        "inclination_deg": 0.,
        "eccentricity": 0.,
        "orbit_radius_m": 7_000_000.,
        "spacecraft_mass_kg": 100.,
        "controller_k": 0.,
        "controller_p": 0.,
    }
    probe = UnifiedNativeRuntime(replace(_config(**base), duration_s=10.)).run()
    assert probe.summary["orbital_environment"]["ephemeris"] == "JPL_DE430_SPICE"
    sun_vector = np.array([
        probe.trace_rows[0][f"orbit.sun_position_m_{axis}"] for axis in "xyz"
    ])
    sun_azimuth = math.atan2(float(sun_vector[1]),float(sun_vector[0]))
    # Select sunlit and shadowed orbital directions from the *actual* SPICE
    # Sun vector, not a guessed historical solar time series.
    cases = (
        ("sunlit",sun_azimuth%(2*math.pi),True),
        ("earth_umbra",(sun_azimuth + math.pi)%(2*math.pi),False),
    )
    for name,anomaly,lit in cases:
        run = _config(**{**base,"initial_true_anomaly_rad":anomaly})
        nominal = UnifiedNativeRuntime(run).run()
        with_srp = UnifiedNativeRuntime(replace(run,values={
            **run.values,
            "srp_model": "cannonball",
            "srp_reference_area_m2": 20.,
            "srp_coefficient_reflection": 1.5,
        })).run()
        actual = with_srp.summary["solar_radiation_pressure"]
        assert actual["status"] == "PASS"
        assert actual["model"] == "Basilisk.simulation.radiationPressure.RadiationPressure"
        assert actual["sun_source"] == "JPL_DE430_SPICE"
        assert actual["eclipse_source"] == "Basilisk.simulation.eclipse.Eclipse"
        assert actual["force_frame"] == "N_INERTIAL"
        assert actual["engineering_assumption_only"] is True
        assert actual["flight_calibrated"] is False
        assert actual["reference_area_m2"] == pytest.approx(20.)
        assert actual["coefficient_reflection"] == pytest.approx(1.5)
        assert "solar_radiation_pressure" not in nominal.summary
        assert "spice_eclipse_cannonball_srp" in [
            x["tag"] for x in with_srp.metadata["runtime_manifest"]["instantiated_modules"]
        ]
        assert "spice_eclipse_cannonball_srp" not in [
            x["tag"] for x in nominal.metadata["runtime_manifest"]["instantiated_modules"]
        ]
        rows = with_srp.trace_rows
        factors = [row["orbit.eclipse_factor"] for row in rows]
        forces = [row["orbit.srp_force_norm_n"] for row in rows]
        deltar = float(np.linalg.norm(_position(rows[-1]) - _position(nominal.trace_rows[-1])))
        assert all(0. <= f <= 1. for f in factors)
        assert all(math.isfinite(f) and f >= 0. for f in forces)
        print(
            "DT1_SRP_SPICE_ECLIPSE_MEASURED "
            f"case={name} samples={len(rows)} "
            f"sun_shadow_min={min(factors):.9e} "
            f"sun_shadow_max={max(factors):.9e} "
            f"srp_force_max_n={max(forces):.9e} "
            f"srp_force_min_n={min(forces):.9e} "
            f"with_minus_without_position_m={deltar:.9e} "
            "fixture_area_m2=20 fixture_cr=1.5 fixture_mass_kg=100",
            flush=True,
        )
        if lit:
            assert min(factors) > 0.99
            assert min(forces) > 0.
            assert deltar > 0.001
            for row in rows:
                rel_sun = np.array([
                    row[f"orbit.sun_position_m_{axis}"]-row[f"orbit.position_m_{axis}"]
                    for axis in "xyz"
                ])
                force = np.array([row[f"orbit.srp_force_n_{axis}"] for axis in "xyz"])
                # Source in the Sun direction; radiation force points away.
                assert float(force @ rel_sun) < 0.
                expected = (
                    SOLAR_FLUX_EARTH_W_M2 / SPEED_LIGHT_M_S * 1.5 * 20.
                    * (AU_M/row["orbit.srp_sun_distance_m"])**2
                    * row["orbit.eclipse_factor"]
                )
                assert row["orbit.srp_force_norm_n"] == pytest.approx(expected, rel=.03)
        else:
            assert max(factors) < 0.001
            assert max(forces) < 1.0e-10
            assert deltar < 1.0e-4

def test_eclipse_input_timestamps_and_positions_survive_sparse_output():
    values = dict(environment_profile="spice_wmm", srp_model="cannonball",
                  srp_reference_area_m2=20., srp_coefficient_reflection=1.5)
    cfg = replace(_config(**values), duration_s=20., step_s=2.)
    dense = UnifiedNativeRuntime(replace(cfg, sample_s=2.)).run().trace_rows
    sparse = UnifiedNativeRuntime(replace(cfg, sample_s=10.)).run().trace_rows
    for row in sparse:
        i = round(row["time_s"]/2.)
        assert row["orbit.eclipse_spacecraft_source_time_s"] == pytest.approx(max(0., 2.*(i-1)))
        xyz = [row[f"orbit.eclipse_spacecraft_position_m_{axis}"] for axis in "xyz"]
        assert row["orbit.eclipse_spacecraft_geometry_valid"]
        np.testing.assert_array_equal(xyz, _position(dense[max(i-1, 0)]))
