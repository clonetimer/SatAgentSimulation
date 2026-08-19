"""ADCS subsystem runner.

Unified entry point for ADCS simulation execution, combining
control, actuator, sensor, and command chain profiles.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .faults import default_fault_scenarios, build_fault_event_specs
from .degradation import ADCSDegradation, default_degradation_scenarios, build_degradation_update_specs
from ..runner_common import scenario_result, run_scenario_batch
from ..runtime_injection import attach_runtime_injection_events, runtime_injection_summary

from .builder import (
    build_nominal_adcs_control_config,
    build_nominal_adcs_sensor_config,
    build_adcs_command_chain_config,
    default_adcs_control_state,
    default_adcs_command_chain_state,
    default_control_profile_inputs,
    build_nominal_adcs_sensor_steps,
    default_command_chain_profile_inputs,
    run_adcs_actuator_basilisk_smoke,
    run_adcs_actuator_component_factory_smoke,
    build_adcs_basilisk_sim,
)
from .model import (
    run_control_profile,
    summarize_control_profile,
    run_actuator_profile,
    summarize_actuator_profile,
    simulate_adcs_sensor_profile,
    run_adcs_command_chain_profile,
    summarize_command_chain_profile,
    run_momentum_dump,
    run_magnetic_detumble,
    AdcsSensorState,
)
from .schemas import (
    MomentumDumpConfig,
    MagneticDetumbleConfig,
    ADCSBasiliskConfig,
)


def run_control_nominal_case() -> dict[str, object]:
    cfg = build_nominal_adcs_control_config()
    state = default_adcs_control_state()
    inputs = default_control_profile_inputs()
    profile = run_control_profile(state, cfg, inputs)
    return summarize_control_profile(profile)


def write_control_profile_csv(profile, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "time_s",
                "mode",
                "control_ready",
                "inhibition_reason",
                "ref_sigma_x",
                "ref_sigma_y",
                "ref_sigma_z",
                "sigma_br_x",
                "sigma_br_y",
                "sigma_br_z",
                "omega_br_x_rad_s",
                "omega_br_y_rad_s",
                "omega_br_z_rad_s",
                "torque_x_nm",
                "torque_y_nm",
                "torque_z_nm",
                "torque_norm_nm",
                "saturated",
            ]
        )
        for out in profile.outputs:
            writer.writerow(
                [
                    out.time_s,
                    out.mode,
                    out.control_ready,
                    out.inhibition_reason or "",
                    *out.reference_sigma_rn,
                    *out.sigma_br,
                    *out.omega_br_b_rad_s,
                    *out.requested_torque_b_nm,
                    out.torque_norm_nm,
                    out.saturated,
                ]
            )
    return path


def run_and_save_control_nominal_case(save_dir: str | Path) -> dict[str, object]:
    save_dir = Path(save_dir)
    cfg = build_nominal_adcs_control_config()
    profile = run_control_profile(default_adcs_control_state(), cfg, default_control_profile_inputs())
    csv_path = write_control_profile_csv(profile, save_dir / "adcs_control_profile.csv")
    summary = summarize_control_profile(profile)
    summary_path = save_dir / "adcs_control_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {**summary, "csv": str(csv_path), "summary_json": str(summary_path)}


def run_actuator_nominal_case() -> dict[str, object]:
    cfg = default_adcs_actuator_config()
    state = default_adcs_actuator_state()
    inputs = default_actuator_profile_inputs()
    profile = run_actuator_profile(state, cfg, inputs)
    return summarize_actuator_profile(profile)


def write_actuator_profile_csv(profile, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "time_s",
                "mode",
                "selected_actuator",
                "command_ready",
                "inhibition_reason",
                "requested_tx_nm",
                "requested_ty_nm",
                "requested_tz_nm",
                "rw_momentum_norm_nms",
                "mtb_dipole_x_am2",
                "mtb_dipole_y_am2",
                "mtb_dipole_z_am2",
                "cmg_gimbal_angle_rad",
            ]
        )
        for out in profile.outputs:
            writer.writerow(
                [
                    out.time_s,
                    out.mode,
                    out.selected_actuator or "",
                    out.command_ready,
                    out.inhibition_reason or "",
                    *out.requested_torque_b_nm,
                    out.rw_momentum_norm_nms,
                    *out.mtb_dipole_am2,
                    out.cmg_gimbal_angle_rad,
                ]
            )
    return path


def run_and_save_actuator_nominal_case(save_dir: str | Path) -> dict[str, object]:
    save_dir = Path(save_dir)
    cfg = default_adcs_actuator_config()
    profile = run_actuator_profile(default_adcs_actuator_state(), cfg, default_actuator_profile_inputs())
    csv_path = write_actuator_profile_csv(profile, save_dir / "adcs_actuator_profile.csv")
    summary = summarize_actuator_profile(profile)
    summary_path = save_dir / "adcs_actuator_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {**summary, "csv": str(csv_path), "summary_json": str(summary_path)}


def run_sensor_nominal_case() -> dict[str, object]:
    cfg = build_nominal_adcs_sensor_config()
    state = AdcsSensorState()
    steps = build_nominal_adcs_sensor_steps()
    final_state, profile, observations = simulate_adcs_sensor_profile(state, cfg, steps)
    return {
        "steps": len(observations),
        "final_time_s": final_state.time_s,
        "mode_ready_count": sum(1 for obs in observations if obs.mode_ready),
        "valid_attitude_count": sum(1 for obs in observations if obs.valid_attitude),
        "valid_rate_count": sum(1 for obs in observations if obs.valid_rate),
        "valid_sun_count": sum(1 for obs in observations if obs.valid_sun),
        "valid_magnetic_count": sum(1 for obs in observations if obs.valid_magnetic),
    }


def write_sensor_profile_csv(observations, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "time_s",
                "mode",
                "mode_ready",
                "valid_attitude",
                "valid_rate",
                "valid_sun",
                "valid_magnetic",
                "sun_intensity",
            ]
        )
        for obs in observations:
            writer.writerow(
                [
                    obs.time_s,
                    obs.mode,
                    obs.mode_ready,
                    obs.valid_attitude,
                    obs.valid_rate,
                    obs.valid_sun,
                    obs.valid_magnetic,
                    obs.sun_intensity,
                ]
            )
    return path


def run_and_save_sensor_nominal_case(save_dir: str | Path) -> dict[str, object]:
    save_dir = Path(save_dir)
    cfg = build_nominal_adcs_sensor_config()
    state = AdcsSensorState()
    steps = build_nominal_adcs_sensor_steps()
    final_state, profile, observations = simulate_adcs_sensor_profile(state, cfg, steps)
    csv_path = write_sensor_profile_csv(observations, save_dir / "adcs_sensor_profile.csv")
    summary = run_sensor_nominal_case()
    summary_path = save_dir / "adcs_sensor_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {**summary, "csv": str(csv_path), "summary_json": str(summary_path)}


def run_command_chain_nominal_case() -> dict[str, object]:
    cfg = build_adcs_command_chain_config()
    state = default_adcs_command_chain_state()
    inputs = default_command_chain_profile_inputs()
    profile = run_adcs_command_chain_profile(state, cfg, inputs)
    return summarize_command_chain_profile(profile)


def write_command_chain_profile_csv(profile, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "time_s",
                "mode",
                "mode_ready",
                "control_ready",
                "actuator_ready",
                "chain_ready",
                "inhibition_reason",
                "selected_actuator",
                "requested_tx_nm",
                "requested_ty_nm",
                "requested_tz_nm",
                "achieved_tx_nm",
                "achieved_ty_nm",
                "achieved_tz_nm",
                "valid_attitude",
                "valid_rate",
                "valid_sun",
                "valid_magnetic",
                "rw0_speed_rad_s",
                "mtb0_dipole_am2",
                "cmg_gimbal_angle_rad",
            ]
        )
        for out in profile.outputs:
            obs = out.sensor_observation
            ctrl = out.control_output
            act = out.actuator_output
            writer.writerow(
                [
                    out.time_s,
                    out.mode,
                    obs.mode_ready,
                    ctrl.control_ready,
                    act.command_ready,
                    out.chain_ready,
                    out.inhibition_reason or "",
                    out.selected_actuator or "",
                    *out.requested_torque_b_nm,
                    *out.actuator_command_torque_b_nm,
                    obs.valid_attitude,
                    obs.valid_rate,
                    obs.valid_sun,
                    obs.valid_magnetic,
                    act.rw_speed_rad_s[0] if act.rw_speed_rad_s else 0.0,
                    act.mtb_dipole_am2[0] if act.mtb_dipole_am2 else 0.0,
                    act.cmg_gimbal_angle_rad,
                ]
            )
    return path


def run_and_save_command_chain_nominal_case(save_dir: str | Path) -> dict[str, object]:
    save_dir = Path(save_dir)
    cfg = build_adcs_command_chain_config()
    state = default_adcs_command_chain_state()
    inputs = default_command_chain_profile_inputs()
    profile = run_adcs_command_chain_profile(state, cfg, inputs)
    csv_path = write_command_chain_profile_csv(profile, save_dir / "adcs_command_chain_profile.csv")
    summary = summarize_command_chain_profile(profile)
    summary_path = save_dir / "adcs_command_chain_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {**summary, "csv": str(csv_path), "summary_json": str(summary_path)}


def run_momentum_dump_nominal_case() -> dict[str, object]:
    result = run_momentum_dump(MomentumDumpConfig())
    return {
        "initial_momentum_nms": result.initial_momentum_norm_nms,
        "final_momentum_nms": result.final_momentum_norm_nms,
        "momentum_ratio": result.momentum_ratio,
        "converged": result.converged,
        "max_dipole_am2": result.max_abs_dipole_am2,
        "samples": len(result.samples),
    }


def run_magnetic_detumble_nominal_case() -> dict[str, object]:
    result = run_magnetic_detumble(MagneticDetumbleConfig())
    return {
        "initial_rate_rad_s": result.initial_rate_norm_rad_s,
        "final_rate_rad_s": result.final_rate_norm_rad_s,
        "rate_ratio": result.final_rate_norm_rad_s / max(result.initial_rate_norm_rad_s, 1e-12),
        "final_sigma_norm": result.final_sigma_norm,
        "final_dipole_am2": result.final_dipole_norm_am2,
        "samples": len(result.samples),
    }


def run_backend(backend: str = "python") -> dict[str, object]:
    if backend == "python":
        return {
            "backend": "python",
            "control": run_control_nominal_case(),
            "actuator": run_actuator_nominal_case(),
            "sensor": run_sensor_nominal_case(),
            "command_chain": run_command_chain_nominal_case(),
            "momentum_dump": run_momentum_dump_nominal_case(),
            "magnetic_detumble": run_magnetic_detumble_nominal_case(),
        }
    if backend == "basilisk":
        return {
            "backend": "basilisk",
            "actuator_smoke": run_adcs_actuator_basilisk_smoke(),
            "factory_smoke": run_adcs_actuator_component_factory_smoke(),
        }
    raise ValueError(f"unsupported ADCS backend: {backend}")


def run_all_nominal_cases(save_dir: str | Path) -> dict[str, object]:
    save_dir = Path(save_dir)
    results = {
        "control": run_and_save_control_nominal_case(save_dir),
        "actuator": run_and_save_actuator_nominal_case(save_dir),
        "sensor": run_and_save_sensor_nominal_case(save_dir),
        "command_chain": run_and_save_command_chain_nominal_case(save_dir),
    }
    summary = {
        "total_profiles": len(results),
        "control_steps": results["control"].get("steps", 0),
        "actuator_steps": results["actuator"].get("steps", 0),
        "sensor_steps": results["sensor"].get("steps", 0),
        "command_chain_steps": results["command_chain"].get("steps", 0),
    }
    summary_path = save_dir / "adcs_all_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {**results, "summary": summary}


def harness_available() -> bool:
    return True


def print_summary(data: dict[str, Any]) -> None:
    print("ADCS subsystem nominal case results:")
    for key, value in sorted(data.items()):
        if isinstance(value, dict):
            print(f"  {key}:")
            for k, v in sorted(value.items()):
                print(f"    {k}: {v}")
        else:
            print(f"  {key}: {value}")


def default_adcs_actuator_config():
    from .builder import default_adcs_actuator_config as _default
    return _default()


def default_adcs_actuator_state():
    from .builder import default_adcs_actuator_state as _default
    return _default()


def default_actuator_profile_inputs():
    from .builder import default_actuator_profile_inputs as _default
    return _default()


_NORMAL_SCENARIOS = {
    "detumble": "Basilisk ADCS assembly with magnetorquer command for detumble-like actuation.",
    "nadir_pointing": "Basilisk ADCS assembly with low reaction-wheel torque and sensors enabled.",
    "slew": "Basilisk ADCS assembly with higher reaction-wheel torque command.",
    "momentum_dump": "Basilisk ADCS assembly with reaction-wheel and magnetorquer activity for momentum dump coverage.",
    "sensor_only": "Basilisk ADCS assembly with sensors enabled and actuator commands near zero.",
}


def list_normal_scenarios() -> list[str]:
    """Return ADCS non-fault, non-degradation Basilisk operating scenarios."""

    return list(_NORMAL_SCENARIOS.keys())


def _execute_adcs_basilisk_config(
    config: ADCSBasiliskConfig,
    *,
    fault_event_specs: tuple[dict[str, Any], ...] = (),
    degradation_update_specs: tuple[dict[str, Any], ...] = (),
) -> dict[str, object]:
    """Execute an ADCS Basilisk builder context and summarize recorder outputs."""

    from Basilisk.utilities import macros

    ctx = build_adcs_basilisk_sim(config)
    sim = ctx.simulation
    registered_runtime_events = attach_runtime_injection_events(
        ctx,
        fault_event_specs=fault_event_specs,
        degradation_update_specs=degradation_update_specs,
    ) if (fault_event_specs or degradation_update_specs) else {"fault_events": (), "degradation_events": ()}
    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(float(ctx.config.duration_s)))
    sim.ExecuteSimulation()

    sc_rec = ctx.recorders.get("spacecraft")
    sample_count = len(sc_rec.times()) if sc_rec is not None and hasattr(sc_rec, "times") else 0
    final_sigma_norm = 0.0
    final_rate_norm = 0.0
    if sc_rec is not None and sample_count:
        try:
            final_sigma = [float(x) for x in sc_rec.sigma_BN[-1]]
            final_sigma_norm = float(sum(x * x for x in final_sigma) ** 0.5)
        except Exception:
            final_sigma_norm = 0.0
        try:
            final_rate = [float(x) for x in sc_rec.omega_BN_B[-1]]
            final_rate_norm = float(sum(x * x for x in final_rate) ** 0.5)
        except Exception:
            final_rate_norm = 0.0

    rw_samples = 0
    rw_rec = ctx.recorders.get("reaction_wheel_speed")
    if rw_rec is not None and hasattr(rw_rec, "times"):
        rw_samples = len(rw_rec.times())

    rw_command_norm_nm = float(sum(float(x) * float(x) for x in ctx.config.rw_motor_torques_nm) ** 0.5)
    mtb_command_norm_am2 = float(sum(float(x) * float(x) for x in ctx.config.mtb_dipoles_am2) ** 0.5)
    control_effort_proxy = float(rw_command_norm_nm + mtb_command_norm_am2)

    return {
        "backend": "basilisk_builder_context",
        "status": "PASS" if sample_count > 0 else "FAIL",
        "duration_s": float(ctx.config.duration_s),
        "step_s": float(ctx.config.step_s),
        "sample_s": float(ctx.config.sample_s),
        "sample_count": sample_count,
        "rw_sample_count": rw_samples,
        "final_sigma_norm": final_sigma_norm,
        "final_rate_norm_rad_s": final_rate_norm,
        "rw_command_norm_nm": rw_command_norm_nm,
        "mtb_command_norm_am2": mtb_command_norm_am2,
        "control_effort_proxy": control_effort_proxy,
        "modules": sorted(ctx.modules.keys()),
        "component_sources": dict(ctx.component_sources),
        "basilisk_builder": "subsystems.adcs.builder.build_adcs_basilisk_sim",
        "registered_runtime_events": registered_runtime_events,
        "runtime_injection_summary": runtime_injection_summary(ctx),
    }


def _adcs_normal_config(scenario: str) -> ADCSBasiliskConfig:
    if scenario == "detumble":
        return ADCSBasiliskConfig(duration_s=2.0, step_s=1.0, sample_s=1.0, include_cmg=False, rw_motor_torques_nm=(0.0, 0.0, 0.0), mtb_dipoles_am2=(0.02, -0.01, 0.01))
    if scenario == "nadir_pointing":
        return ADCSBasiliskConfig(duration_s=2.0, step_s=1.0, sample_s=1.0, include_cmg=False, rw_motor_torques_nm=(0.01, -0.005, 0.002), mtb_dipoles_am2=(0.0, 0.0, 0.0))
    if scenario == "slew":
        return ADCSBasiliskConfig(duration_s=2.0, step_s=1.0, sample_s=1.0, include_cmg=False, rw_motor_torques_nm=(0.04, -0.02, 0.015), mtb_dipoles_am2=(0.0, 0.0, 0.0))
    if scenario == "momentum_dump":
        return ADCSBasiliskConfig(duration_s=2.0, step_s=1.0, sample_s=1.0, include_cmg=False, rw_motor_torques_nm=(0.0, 0.0, 0.0), mtb_dipoles_am2=(0.04, 0.02, -0.02))
    if scenario == "sensor_only":
        return ADCSBasiliskConfig(duration_s=2.0, step_s=1.0, sample_s=1.0, include_cmg=False, include_sensors=True, rw_motor_torques_nm=(0.0, 0.0, 0.0), mtb_dipoles_am2=(0.0, 0.0, 0.0))
    raise ValueError(f"Unsupported ADCS normal scenario: {scenario}")


def run_normal_scenario(scenario: str = "nadir_pointing") -> dict[str, Any]:
    """Run one ADCS normal scenario through the subsystem Basilisk builder."""

    return scenario_result(
        "adcs",
        scenario,
        _NORMAL_SCENARIOS[scenario],
        _execute_adcs_basilisk_config(_adcs_normal_config(scenario)),
    )


def run_normal_scenarios() -> dict[str, Any]:
    """Run every ADCS non-fault, non-degradation Basilisk scenario."""

    return run_scenario_batch("adcs", _NORMAL_SCENARIOS, run_normal_scenario)


def _adcs_degradation_static_config(scenario_name: str) -> ADCSBasiliskConfig:
    scale_by_scenario = {
        "actuator_aging": 0.75,
        "sensor_aging": 1.0,
        "combined_adcs_aging": 0.7,
    }
    scale = scale_by_scenario.get(scenario_name, 0.8)
    return ADCSBasiliskConfig(
        duration_s=140.0,
        step_s=10.0,
        sample_s=10.0,
        include_cmg=False,
        include_sensors=True,
        rw_motor_torques_nm=(0.02 * scale, -0.01 * scale, 0.005 * scale),
        mtb_dipoles_am2=(0.02 * scale, 0.01 * scale, -0.01 * scale),
    )


def run_degradation_case(scenario_name: str | ADCSDegradation | None = None) -> dict[str, Any]:
    """Run an ADCS degradation scenario through the Basilisk builder.

    ``scenario_name`` may be a new subsystem scenario name or a legacy
    ``ADCSDegradation`` object.  Runtime event scheduling is deferred to
    SUBSYS-BSK-EVENT-1; this runner records the metadata and exercises an
    equivalent static builder configuration.
    """

    scenarios = default_degradation_scenarios()
    if isinstance(scenario_name, ADCSDegradation):
        scenario = scenarios["combined_adcs_aging"]
        scale = max(0.0, min(1.0, float(scenario_name.mtb_dipole_degradation_factor)))
        cfg = ADCSBasiliskConfig(
            duration_s=140.0,
            step_s=10.0,
            sample_s=10.0,
            include_cmg=False,
            include_sensors=True,
            rw_motor_torques_nm=(0.02 * scale, -0.01 * scale, 0.005 * scale),
            mtb_dipoles_am2=(0.02 * scale, 0.01 * scale, -0.01 * scale),
        )
    else:
        name = scenario_name or "combined_adcs_aging"
        scenario = scenarios[name]
        cfg = _adcs_degradation_static_config(scenario.name)

    update_specs = build_degradation_update_specs(scenario, update_period_s=10.0)
    result = _execute_adcs_basilisk_config(cfg, degradation_update_specs=update_specs)
    result["degradation_scenario"] = scenario.name
    result["degradation_update_specs"] = update_specs
    result["covered_components"] = sorted({binding.component for binding in scenario.component_degradations})
    result["runtime_injection"] = "basilisk_createNewEvent_direct"
    return scenario_result("adcs", scenario.name, scenario.description, result, mode="degradation")


def _adcs_fault_static_config(scenario_name: str) -> ADCSBasiliskConfig:
    if scenario_name == "rw_bearing_seizure":
        return ADCSBasiliskConfig(duration_s=430.0, step_s=10.0, sample_s=10.0, include_cmg=False, rw_motor_torques_nm=(0.0, 0.0, 0.0), mtb_dipoles_am2=(0.03, 0.0, -0.02))
    if scenario_name == "sensor_dropout_bias_fault":
        return ADCSBasiliskConfig(duration_s=430.0, step_s=10.0, sample_s=10.0, include_cmg=False, include_sensors=True, rw_motor_torques_nm=(0.01, 0.0, 0.0), mtb_dipoles_am2=(0.0, 0.0, 0.0))
    if scenario_name == "mtb_cmg_actuator_fault":
        return ADCSBasiliskConfig(duration_s=430.0, step_s=10.0, sample_s=10.0, include_cmg=False, rw_motor_torques_nm=(0.01, -0.005, 0.002), mtb_dipoles_am2=(0.0, 0.0, 0.0))
    return ADCSBasiliskConfig(duration_s=430.0, step_s=10.0, sample_s=10.0, include_cmg=False, include_sensors=True, rw_motor_torques_nm=(0.005, 0.0, 0.0), mtb_dipoles_am2=(0.02, 0.0, -0.01))


def run_fault_case(scenario_name: str | list[Any] | None = None) -> dict[str, Any]:
    """Run an ADCS component-aggregated fault scenario through the Basilisk builder."""

    scenarios = default_fault_scenarios()
    if isinstance(scenario_name, list):
        scenario = scenarios["rw_bearing_seizure"]
    else:
        scenario = scenarios[scenario_name or "rw_bearing_seizure"]
    event_specs = build_fault_event_specs(scenario)
    result = _execute_adcs_basilisk_config(_adcs_fault_static_config(scenario.name), fault_event_specs=event_specs)
    result["fault_scenario"] = scenario.name
    result["fault_event_specs"] = event_specs
    result["covered_components"] = sorted({binding.component for binding in scenario.component_faults})
    result["runtime_injection"] = "basilisk_createNewEvent_direct"
    return scenario_result("adcs", scenario.name, scenario.description, result, mode="fault")


def run_combined_case(
    fault_scenario_name: str | list[Any] | None = None,
    degradation_scenario_name: str | ADCSDegradation | None = None,
) -> dict[str, Any]:
    """Run ADCS static fault + degradation case through the Basilisk builder."""

    fault_scenarios = default_fault_scenarios()
    degradation_scenarios = default_degradation_scenarios()
    fault_scenario = fault_scenarios["sensor_dropout_bias_fault" if isinstance(fault_scenario_name, list) or fault_scenario_name is None else fault_scenario_name]
    degradation_scenario = degradation_scenarios["actuator_aging" if isinstance(degradation_scenario_name, ADCSDegradation) or degradation_scenario_name is None else degradation_scenario_name]
    cfg = ADCSBasiliskConfig(
        duration_s=430.0,
        step_s=10.0,
        sample_s=10.0,
        include_cmg=False,
        include_sensors=True,
        rw_motor_torques_nm=(0.005, 0.0, 0.0),
        mtb_dipoles_am2=(0.015, 0.0, -0.01),
    )
    event_specs = build_fault_event_specs(fault_scenario)
    update_specs = build_degradation_update_specs(degradation_scenario, update_period_s=10.0)
    result = _execute_adcs_basilisk_config(cfg, fault_event_specs=event_specs, degradation_update_specs=update_specs)
    result["fault_scenario"] = fault_scenario.name
    result["degradation_scenario"] = degradation_scenario.name
    result["fault_event_specs"] = event_specs
    result["degradation_update_specs"] = update_specs
    result["covered_fault_components"] = sorted({binding.component for binding in fault_scenario.component_faults})
    result["covered_degradation_components"] = sorted({binding.component for binding in degradation_scenario.component_degradations})
    result["combined_with_degradation"] = True
    result["runtime_injection"] = "basilisk_createNewEvent_direct"
    return scenario_result("adcs", "adcs_fault_degradation", "ADCS combined component fault/degradation scenario assembled through Basilisk builder.", result, mode="combined")


def run_nominal_case() -> dict[str, Any]:
    """Backward-compatible nominal case now routed through the Basilisk builder."""

    return run_normal_scenario("nadir_pointing")


def run_all_modes() -> dict[str, Any]:
    """Run ADCS normal, fault, degradation and combined Basilisk-builder modes."""

    return {
        "normal_scenarios": run_normal_scenarios(),
        "degradation": run_degradation_case(),
        "fault_rw_bearing_seizure": run_fault_case(),
        "combined_fault_degradation": run_combined_case(),
    }


__all__ = [
    "run_control_nominal_case",
    "write_control_profile_csv",
    "run_and_save_control_nominal_case",
    "run_actuator_nominal_case",
    "write_actuator_profile_csv",
    "run_and_save_actuator_nominal_case",
    "run_sensor_nominal_case",
    "write_sensor_profile_csv",
    "run_and_save_sensor_nominal_case",
    "run_command_chain_nominal_case",
    "write_command_chain_profile_csv",
    "run_and_save_command_chain_nominal_case",
    "run_backend",
    "run_normal_scenario",
    "run_normal_scenarios",
    "run_degradation_case",
    "run_fault_case",
    "run_combined_case",
    "run_all_modes",
    "run_all_nominal_cases",
    "harness_available",
    "print_summary",
]
