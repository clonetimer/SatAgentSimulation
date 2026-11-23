"""Run custom body rates, feedback driver, IMU walk/LSB and four environmental effects together."""
import json
import sys
from pathlib import Path
from sat_sim.run_bundle import prepare_run, execute_prepared_run, verify_run_bundle


def measure(source, root, lag):
    task = json.loads(source.read_bytes())
    task["simulation"].update(step_s=.25, sample_s=2.)
    cfg = task["parameters"]["values"]
    expected = [-.003, 0., .006]
    cfg.update(zip((f"initial_body_rate_{a}_rad_s" for a in "xyz"), expected))
    cfg.update(controller_k=3.5, controller_p=30., simulation_seed=811,
               rw_command_deadband_nm=.01 if lag else 0., rw_motor_response_time_constant_s=.25 if lag else 0.,
               imu_gyro_quantization_lsb_rad_s=1e-5, imu_gyro_bias_random_walk_std_rad_s_sqrt_s=2e-4)
    plan = prepare_run(task, output_root=root, run_id="lag" if lag else "ideal")
    res = execute_prepared_run(plan.bundle_root, expected_plan_sha256=plan.execution_plan_sha256,
                               max_attempts=1, hard_timeout=True)
    bundle = Path(plan.bundle_root)
    assert verify_run_bundle(bundle)["ok"] and res.validation.result.value == "PASS"
    rows = res.trace_rows
    first = [rows[0][f"attitude.truth.omega_BN_B_{a}"] for a in "xyz"]
    assert all(abs(x-y)<1e-14 for x,y in zip(expected,first,strict=True))
    assert len(rows)==16
    env = res.summary["orbital_environment"]
    assert env["gravity_model"]=="ggm03s_degree4" and env["magnetic_field_model_id"]=="IGRF14_2025_2030"
    assert env["atmosphere_model"]=="NRLMSISE-00" and res.summary["solar_radiation_pressure"]["status"]=="PASS"
    assert max(r["orbit.drag_force_norm_n"] for r in rows)>0.
    for r in rows[1:]:
        for a in "xyz":
            units = r[f"adcs.sensor.raw_gyro_rad_s_{a}"]/1e-5
            assert abs(units-round(units)) < 1e-7
    return dict(first_rate_rad_s=first, manifest=json.loads((bundle/"SEALED.json").read_bytes())["bundle_manifest_sha256"],
                gyro_walk=[r["adcs.sensor.gyro_bias_walk_rad_s_x"] for r in rows],
                wheel0_rad_s=[r["adcs.rw.speed_rad_s_0"] for r in rows])


if __name__ == "__main__":
    source, root = map(Path, sys.argv[1:])
    ideal, lag = [measure(source, root, flag) for flag in (False,True)]
    assert ideal["gyro_walk"] == lag["gyro_walk"] and max(map(abs,lag["gyro_walk"])) > 0
    delta = max(abs(x-y) for x,y in zip(ideal["wheel0_rad_s"],lag["wheel0_rad_s"],strict=True))
    assert delta > 1e-7
    (root/"joint.json").write_text(json.dumps(dict(ideal=ideal,lag=lag,max_wheel0_delta_rad_s=delta),indent=2)+"\n",encoding="utf-8")
    print(delta)
