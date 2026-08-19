"""RF Power Amplifier (TWT/SSPA) runner module.

This module provides runner functions for simulating TWT and SSPA amplifiers
following the standard runner pattern used in this project.

Usage:
    # Run TWT simulation
    result = run_twt_nominal_case()
    result = run_twt_power_sweep()

    # Run SSPA simulation
    result = run_sspa_nominal_case()
    result = run_sspa_power_sweep()

    # Run comparison
    result = run_amplifier_comparison()
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List

from .builder import (
    TransmitterConfig,
    TransmitterResult,
    compute_transmitter,
    build_twt_transmitter_config,
    build_sspa_transmitter_config,
    RfAmplifierBuilder,
)
from .degradation import TransmitterDegradation
from .faults import TransmitterFaultType
from .faults import FaultSpec

@dataclass
class AmplifierProfileRow:
    """Single time-step result from amplifier simulation."""
    time_s: float
    commanded_on: bool
    requested_rate_bps: float
    available_power_w: float
    tx_power_w: float
    power_draw_w: float
    effective_rate_bps: float
    efficiency: float
    saturation_level_dB: float

@dataclass
class AmplifierProfile:
    """Complete profile from amplifier simulation."""
    time_s: list[float] = field(default_factory=list)
    commanded_on: list[bool] = field(default_factory=list)
    requested_rate_bps: list[float] = field(default_factory=list)
    available_power_w: list[float] = field(default_factory=list)
    tx_power_w: list[float] = field(default_factory=list)
    power_draw_w: list[float] = field(default_factory=list)
    effective_rate_bps: list[float] = field(default_factory=list)
    efficiency: list[float] = field(default_factory=list)
    saturation_level_dB: list[float] = field(default_factory=list)

@dataclass
class AmplifierSummary:
    """Summary statistics from amplifier simulation."""
    amp_type: str
    steps: int
    duration_s: float
    initial_power: float
    final_power: float
    max_tx_power_w: float
    min_tx_power_w: float
    avg_tx_power_w: float
    max_power_draw_w: float
    min_power_draw_w: float
    avg_power_draw_w: float
    total_energy_wh: float
    avg_efficiency: float
    commanded_on_count: int

def run_amplifier_profile(
    config: TransmitterConfig,
    commanded_on: list[bool],
    requested_rate_bps: list[float],
    available_power_w: list[float],
    dt_s: float = 1.0,
) -> tuple[AmplifierProfile, AmplifierSummary]:
    """Run a complete amplifier profile simulation.

    Args:
        config: Amplifier configuration
        commanded_on: List of commanded-on states
        requested_rate_bps: List of requested data rates
        available_power_w: List of available DC power values
        dt_s: Time step in seconds

    Returns:
        Tuple of (AmplifierProfile, AmplifierSummary)
    """
    profile = AmplifierProfile()
    time = 0.0

    for on, rate, power in zip(commanded_on, requested_rate_bps, available_power_w):
        result = compute_transmitter(on, rate, power, config)

        profile.time_s.append(time)
        profile.commanded_on.append(result.transmitter_enabled)
        profile.requested_rate_bps.append(rate)
        profile.available_power_w.append(power)
        profile.tx_power_w.append(result.tx_power_w)
        profile.power_draw_w.append(result.power_draw_w)
        profile.effective_rate_bps.append(result.effective_rate_bps)
        profile.efficiency.append(result.efficiency)
        profile.saturation_level_dB.append(result.saturation_level_dB)

        time += dt_s

    summary = AmplifierSummary(
        amp_type=config.amp_type,
        steps=len(profile.time_s),
        duration_s=profile.time_s[-1] if profile.time_s else 0.0,
        initial_power=profile.power_draw_w[0] if profile.power_draw_w else 0.0,
        final_power=profile.power_draw_w[-1] if profile.power_draw_w else 0.0,
        max_tx_power_w=max(profile.tx_power_w) if profile.tx_power_w else 0.0,
        min_tx_power_w=min(profile.tx_power_w) if profile.tx_power_w else 0.0,
        avg_tx_power_w=sum(profile.tx_power_w) / len(profile.tx_power_w) if profile.tx_power_w else 0.0,
        max_power_draw_w=max(profile.power_draw_w) if profile.power_draw_w else 0.0,
        min_power_draw_w=min(profile.power_draw_w) if profile.power_draw_w else 0.0,
        avg_power_draw_w=sum(profile.power_draw_w) / len(profile.power_draw_w) if profile.power_draw_w else 0.0,
        total_energy_wh=sum(profile.power_draw_w) * dt_s / 3600.0 if profile.power_draw_w else 0.0,
        avg_efficiency=sum(profile.efficiency) / len(profile.efficiency) if profile.efficiency else 0.0,
        commanded_on_count=sum(1 for x in profile.commanded_on if x),
    )

    return profile, summary

def build_orbit_profile(
    dt_s: float = 10.0,
    duration_s: float = 7200.0,
    commanded_intervals: list[tuple[float, float]] | None = None,
) -> tuple[list[bool], list[float], list[float]]:
    """Build a typical orbit-based amplifier profile.

    Args:
        dt_s: Time step
        duration_s: Total duration
        commanded_intervals: List of (start, end) tuples for commanded-on periods

    Returns:
        Tuple of (commanded_on, requested_rate_bps, available_power_w)
    """
    if commanded_intervals is None:
        commanded_intervals = [(0, 1200), (2400, 3600)]

    n_steps = int(duration_s / dt_s)
    orbit_period = 5760.0
    eclipse_start = orbit_period * 0.3
    eclipse_duration = orbit_period * 0.35

    commanded_on: list[bool] = []
    requested_rate_bps: list[float] = []
    available_power_w: list[float] = []

    for i in range(n_steps):
        t = i * dt_s
        t_orbit = t % orbit_period
        in_eclipse = eclipse_start <= t_orbit < (eclipse_start + eclipse_duration)

        is_commanded = any(start <= t < end for start, end in commanded_intervals)

        commanded_on.append(is_commanded)
        requested_rate_bps.append(1_000_000.0 if is_commanded else 0.0)
        available_power_w.append(30.0 if in_eclipse else 150.0)

    return commanded_on, requested_rate_bps, available_power_w

def run_twt_nominal_case(
    max_power_w: float = 100.0,
    efficiency: float = 0.65,
    gain_db: float = 50.0,
    input_power_w: float = 0.003,
) -> tuple[AmplifierProfile, AmplifierSummary]:
    """Run a nominal TWT amplifier simulation.

    Args:
        max_power_w: Maximum output power
        efficiency: RF efficiency
        gain_db: Small signal gain
        input_power_w: Input RF power

    Returns:
        Tuple of (AmplifierProfile, AmplifierSummary)
    """
    config = build_twt_transmitter_config(
        max_power_w=max_power_w,
        efficiency=efficiency,
        gain_db=gain_db,
        input_power_w=input_power_w,
    )

    commanded_on, requested_rate, available_power = build_orbit_profile()

    return run_amplifier_profile(config, commanded_on, requested_rate, available_power)

def run_sspa_nominal_case(
    max_power_w: float = 12.0,
    efficiency: float = 0.45,
    gain_db: float = 35.0,
    input_power_w: float = 0.012,
) -> tuple[AmplifierProfile, AmplifierSummary]:
    """Run a nominal SSPA amplifier simulation.

    Args:
        max_power_w: Maximum output power
        efficiency: RF efficiency
        gain_db: Gain
        input_power_w: Input RF power

    Returns:
        Tuple of (AmplifierProfile, AmplifierSummary)
    """
    config = build_sspa_transmitter_config(
        max_power_w=max_power_w,
        efficiency=efficiency,
        gain_db=gain_db,
        input_power_w=input_power_w,
    )

    commanded_on, requested_rate, available_power = build_orbit_profile()

    return run_amplifier_profile(config, commanded_on, requested_rate, available_power)

def run_amplifier_comparison() -> dict[str, AmplifierSummary]:
    """Run comparison between TWT and SSPA amplifiers.

    Returns:
        Dictionary with summaries for each amplifier type
    """
    twt_profile, twt_summary = run_twt_nominal_case()
    sspa_profile, sspa_summary = run_sspa_nominal_case()

    return {
        "twt": twt_summary,
        "sspa": sspa_summary,
    }

def run_power_sweep(
    amp_type: str = "twt",
    power_levels: list[float] | None = None,
) -> list[AmplifierSummary]:
    """Run power sweep simulation.

    Args:
        amp_type: Amplifier type ("twt" or "sspa")
        power_levels: List of power levels to sweep

    Returns:
        List of summaries for each power level
    """
    if power_levels is None:
        if amp_type == "twt":
            power_levels = [20, 50, 75, 100, 150, 200]
        else:
            power_levels = [1, 5, 10, 15, 20, 30]

    summaries = []
    commanded_on, requested_rate, available_power = build_orbit_profile()

    for power_w in power_levels:
        if amp_type == "twt":
            config = build_twt_transmitter_config(max_power_w=power_w)
        else:
            config = build_sspa_transmitter_config(max_power_w=power_w)

        _, summary = run_amplifier_profile(config, commanded_on, requested_rate, available_power)
        summaries.append(summary)

    return summaries

def write_profile_csv(
    profile: AmplifierProfile,
    path: str | Path,
) -> Path:
    """Write amplifier profile to CSV file.

    Args:
        profile: Amplifier profile to write
        path: Output file path

    Returns:
        Path to written file
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "time_s",
            "commanded_on",
            "requested_rate_bps",
            "available_power_w",
            "tx_power_w",
            "power_draw_w",
            "effective_rate_bps",
            "efficiency",
            "saturation_level_dB",
        ])

        for i in range(len(profile.time_s)):
            writer.writerow([
                f"{profile.time_s[i]:.2f}",
                str(profile.commanded_on[i]),
                f"{profile.requested_rate_bps[i]:.0f}",
                f"{profile.available_power_w[i]:.2f}",
                f"{profile.tx_power_w[i]:.4f}",
                f"{profile.power_draw_w[i]:.4f}",
                f"{profile.effective_rate_bps[i]:.0f}",
                f"{profile.efficiency[i]:.4f}",
                f"{profile.saturation_level_dB[i]:.2f}",
            ])

    return path

def print_summary(summary: AmplifierSummary) -> None:
    """Print amplifier summary in a formatted way.

    Args:
        summary: Summary to print
    """
    print(f"\n{'='*60}")
    print(f"RF Power Amplifier Simulation Summary: {summary.amp_type.upper()}")
    print(f"{'='*60}")
    print(f"  Duration:    {summary.duration_s:.0f} s ({summary.duration_s/60:.1f} min)")
    print(f"  Steps:       {summary.steps}")
    print(f"  On Time:     {summary.commanded_on_count} steps")
    print(f"")
    print(f"  TX Power:")
    print(f"    Max:       {summary.max_tx_power_w:.2f} W")
    print(f"    Min:       {summary.min_tx_power_w:.4f} W")
    print(f"    Avg:       {summary.avg_tx_power_w:.2f} W")
    print(f"")
    print(f"  Power Draw:")
    print(f"    Max:       {summary.max_power_draw_w:.2f} W")
    print(f"    Min:       {summary.min_power_draw_w:.4f} W")
    print(f"    Avg:       {summary.avg_power_draw_w:.2f} W")
    print(f"    Total:     {summary.total_energy_wh:.2f} Wh")
    print(f"")
    print(f"  Efficiency:")
    print(f"    Avg:       {summary.avg_efficiency*100:.1f}%")
    print(f"{'='*60}\n")

def run_degradation_case(degradation: TransmitterDegradation) -> dict[str, Any]:
    """Run degradation case for transmitter and return JSON-friendly summary."""
    from .degradation import apply_transmitter_degradation
    
    config = build_sspa_transmitter_config()
    config = apply_transmitter_degradation(config, degradation)
    commanded_on, requested_rate, available_power = build_orbit_profile(dt_s=100.0, duration_s=1000.0)
    profile, summary = run_amplifier_profile(config, commanded_on, requested_rate, available_power)

    return {
        "mode": "degradation",
        "degradation": vars(degradation) if hasattr(degradation, "__dict__") else str(degradation),
        "config": {
            "max_tx_power_w": config.max_tx_power_w,
            "efficiency": config.efficiency,
            "gain_dB": config.gain_dB,
        },
        "result": {
            "duration_s": summary.duration_s,
            "total_energy_wh": summary.total_energy_wh,
            "avg_efficiency": summary.avg_efficiency,
            "max_tx_power_w": summary.max_tx_power_w,
        },
    }

def run_fault_case(fault_specs: List[FaultSpec]) -> dict[str, Any]:
    """Run fault case for transmitter and return JSON-friendly summary."""
    from .faults import apply_transmitter_faults
    
    config = build_sspa_transmitter_config()
    config = apply_transmitter_faults(config, fault_specs)
    commanded_on, requested_rate, available_power = build_orbit_profile(dt_s=100.0, duration_s=1000.0)
    profile, summary = run_amplifier_profile(config, commanded_on, requested_rate, available_power)

    return {
        "mode": "fault",
        "faults": [
            {
                "fault_type": spec.fault_type.value if hasattr(spec.fault_type, "value") else str(spec.fault_type),
                "onset_time_s": spec.onset_time_s,
                "duration_s": spec.duration_s,
                "magnitude": spec.magnitude,
                "target_id": spec.target_id,
            }
            for spec in fault_specs
        ],
        "config": {
            "max_tx_power_w": config.max_tx_power_w,
            "efficiency": config.efficiency,
            "gain_dB": config.gain_dB,
        },
        "result": {
            "duration_s": summary.duration_s,
            "total_energy_wh": summary.total_energy_wh,
            "avg_efficiency": summary.avg_efficiency,
            "max_tx_power_w": summary.max_tx_power_w,
        },
    }

def _legacy_run_all_modes_impl_20() -> dict[str, Any]:
    """Run all simulation modes for transmitter."""
    results = {}
    print(f"Running nominal case for transmitter...")
    results["nominal"] = run_nominal_case()
    print(f"Running degradation case for transmitter...")
    try:
        results["degradation"] = run_degradation_case(TransmitterDegradation())
    except Exception as e:
        results["degradation"] = {"error": str(e)}
    print(f"Running fault case for transmitter...")
    try:
        results["fault"] = run_fault_case([
            FaultSpec(
                fault_type=next(iter(TransmitterFaultType)),
                onset_time_s=0.0,
                duration_s=-1.0,
                magnitude=1.0,
                target_id=f"transmitter_0",
            )
        ])
    except Exception as e:
        results["fault"] = {"error": str(e)}
    return results

def harness_available() -> bool:
    """Check if harness dependencies are available."""
    return True

def run_nominal_case() -> dict[str, Any]:
    """Run nominal case and return JSON-friendly summary."""
    summaries = run_amplifier_comparison()

    return {
        "twt": {
            "type": summaries["twt"].amp_type,
            "duration_s": summaries["twt"].duration_s,
            "steps": summaries["twt"].steps,
            "total_energy_wh": summaries["twt"].total_energy_wh,
            "avg_efficiency": summaries["twt"].avg_efficiency,
        },
        "sspa": {
            "type": summaries["sspa"].amp_type,
            "duration_s": summaries["sspa"].duration_s,
            "steps": summaries["sspa"].steps,
            "total_energy_wh": summaries["sspa"].total_energy_wh,
            "avg_efficiency": summaries["sspa"].avg_efficiency,
        },
    }


# --- Component normal working-scenario support ---
# Nominal is retained for backward compatibility; normal_scenarios exercises
# multiple non-fault, non-degradation operating conditions.
_LEGACY_RUN_ALL_MODES = _legacy_run_all_modes_impl_20
_LEGACY_RUN_NOMINAL_CASE = run_nominal_case
_WORKING_SCENARIOS = {
    'nominal': 'reference TWT/SSPA comparison',
    'twt_downlink': 'TWT downlink profile',
    'sspa_downlink': 'SSPA downlink profile',
    'burst_contact': 'short burst-contact transmitter profile',
}


def list_working_scenarios() -> list[str]:
    """Return supported non-fault, non-degradation component operating scenarios."""
    return list(_WORKING_SCENARIOS.keys())


def run_normal_scenario(scenario: str = "nominal") -> dict[str, Any]:
    """Alias for run_working_scenario; kept for terminology consistency."""
    return run_working_scenario(scenario)


def run_working_scenario(scenario: str = "nominal") -> dict[str, Any]:
    from ..runner_common import scenario_result
    if scenario == "nominal":
        return scenario_result("transmitter", scenario, _WORKING_SCENARIOS[scenario], _LEGACY_RUN_NOMINAL_CASE())
    if scenario == "twt_downlink":
        _profile, summary = run_twt_nominal_case()
    elif scenario == "sspa_downlink":
        _profile, summary = run_sspa_nominal_case()
    elif scenario == "burst_contact":
        config = build_sspa_transmitter_config(max_power_w=20.0, efficiency=0.5)
        commanded_on = [False, True, True, False, True]
        requested_rate_bps = [0.0, 2_000_000.0, 2_000_000.0, 0.0, 1_000_000.0]
        available_power_w = [50.0, 80.0, 80.0, 50.0, 60.0]
        _profile, summary = run_amplifier_profile(config, commanded_on, requested_rate_bps, available_power_w, dt_s=10.0)
    else:
        raise ValueError(f"Unsupported transmitter working scenario: {scenario}")
    return scenario_result(
        "transmitter",
        scenario,
        _WORKING_SCENARIOS[scenario],
        {"amp_type": summary.amp_type, "steps": summary.steps, "duration_s": summary.duration_s, "total_energy_wh": summary.total_energy_wh, "avg_efficiency": summary.avg_efficiency, "commanded_on_count": summary.commanded_on_count, "max_tx_power_w": summary.max_tx_power_w},
    )


def run_working_scenarios() -> dict[str, Any]:
    """Run every supported normal operating scenario for this component."""
    from ..runner_common import scenario_error
    results: dict[str, Any] = {}
    for scenario in list_working_scenarios():
        try:
            results[scenario] = run_working_scenario(scenario)
        except Exception as exc:  # keep all scenarios observable in batch runs
            results[scenario] = scenario_error("transmitter", scenario, _WORKING_SCENARIOS.get(scenario, ""), exc)
    return results


def _l2_light_prev_run_all_modes_impl_24() -> dict[str, Any]:
    """Run legacy modes plus all normal operating scenarios."""
    results = _LEGACY_RUN_ALL_MODES()
    results["normal_scenarios"] = run_working_scenarios()
    return results


# --- L2-light event/time-function fault-degradation support ---
# This layer is intentionally sparse-event based: faults are active over time
# windows; degradation coefficients are queried as f(t-start), not integrated
# as internal state equations.
_L2_LIGHT_COMPONENT = 'transmitter'
_L2_LIGHT_PREV_RUN_ALL_MODES = _l2_light_prev_run_all_modes_impl_24


def _l2_light_sample_times(duration_s: float) -> list[float]:
    """Return explicit runner-owned sample times for L2-light cases.

    These fixed review points replace the deleted public sampler
    helper.  They cover baseline, degradation onset, fault onset/active window,
    and late/post-fault behavior without introducing a fixed-step integrator.
    """
    duration = float(duration_s)
    candidates = (0.0, 60.0, 120.0, 180.0, 240.0, 300.0, 360.0, duration)
    return sorted({round(float(t), 6) for t in candidates if 0.0 <= float(t) <= duration})


def _default_l2_light_output(component: str, params, t_s: float):
    availability = float(params.get("availability", params.get("string_availability", 1.0)))
    factors = [
        float(v) for k, v in params.items()
        if (k.endswith("_factor") or k in {"efficiency", "cell_efficiency", "pa_efficiency", "responsivity", "sensitivity_factor"})
        and isinstance(v, (int, float))
    ]
    performance_factor = availability
    for value in factors[:6]:
        performance_factor *= max(0.0, value)
    return {
        "component": component,
        "t_s": float(t_s),
        "availability": availability,
        "performance_factor": performance_factor,
        "quality_metric": max(0.0, min(1.5, performance_factor)),
        "effective_params": dict(params),
    }


def _run_l2_light_profile(*, component: str, base_params, faults=(), degradations=(), sample_times_s=None, duration_s: float = 600.0, evaluator=None, context=None):
    """Run this component's L2-light scenario from runner-owned logic."""
    from copy import deepcopy
    from ..runner_common import to_jsonable

    if sample_times_s is None:
        sample_times_s = _l2_light_sample_times(duration_s)
    evaluator = evaluator or _default_l2_light_output
    trace = []
    for t_s in sorted(float(t) for t in sample_times_s):
        params = deepcopy(dict(base_params))
        degradation_evidence = []
        fault_evidence = []
        for degradation in degradations:
            params, evidence = degradation.apply(params, t_s, context)
            degradation_evidence.append(evidence)
        for fault in faults:
            params, evidence = fault.apply(params, t_s, context)
            fault_evidence.append(evidence)
        output = evaluator(component, params, t_s)
        trace.append({
            "t_s": t_s,
            "active_degradations": [ev["name"] for ev in degradation_evidence if ev.get("active")],
            "active_faults": [ev["name"] for ev in fault_evidence if ev.get("active")],
            "effective_params": to_jsonable(params),
            "output": to_jsonable(output),
            "degradation_evidence": to_jsonable(degradation_evidence),
            "fault_evidence": to_jsonable(fault_evidence),
        })
    return {
        "mode": "l2_light_event_parameterized",
        "component": component,
        "runtime_style": "runner_owned_sparse_time_query_not_fixed_step_integration",
        "composition_order": "base_params -> degradation.apply(t) -> fault.apply(t) -> component evaluation",
        "sample_times_s": list(sample_times_s),
        "fault_count": len(faults),
        "degradation_count": len(degradations),
        "faults_participated": any(row["active_faults"] for row in trace),
        "degradations_participated": any(row["active_degradations"] for row in trace),
        "trace": trace,
        "final_output": trace[-1]["output"] if trace else {},
    }


def run_l2_light_fault_case(*, duration_s: float = 600.0, sample_times_s=None):
    """Run local component faults as time-window parameter modifications."""
    from .faults import default_faults
    from .degradation import base_effective_params, evaluate_effective_params

    faults = default_faults()
    return _run_l2_light_profile(
        component=_L2_LIGHT_COMPONENT,
        base_params=base_effective_params(),
        faults=faults,
        degradations=[],
        sample_times_s=sample_times_s,
        duration_s=duration_s,
        evaluator=evaluate_effective_params,
    )


def run_l2_light_degradation_case(*, duration_s: float = 600.0, sample_times_s=None):
    """Run local component degradation as time-dependent coefficient modifications."""
    from .degradation import base_effective_params, default_degradations, evaluate_effective_params

    degradations = default_degradations()
    return _run_l2_light_profile(
        component=_L2_LIGHT_COMPONENT,
        base_params=base_effective_params(),
        faults=[],
        degradations=degradations,
        sample_times_s=sample_times_s,
        duration_s=duration_s,
        evaluator=evaluate_effective_params,
    )


def run_l2_light_combined_case(*, duration_s: float = 600.0, sample_times_s=None):
    """Run local faults and degradations together with degradation-before-fault composition."""
    from .faults import default_faults
    from .degradation import base_effective_params, default_degradations, evaluate_effective_params

    return _run_l2_light_profile(
        component=_L2_LIGHT_COMPONENT,
        base_params=base_effective_params(),
        faults=default_faults(),
        degradations=default_degradations(),
        sample_times_s=sample_times_s,
        duration_s=duration_s,
        evaluator=evaluate_effective_params,
    )


def run_l2_light_cases(*, duration_s: float = 600.0):
    """Run all L2-light sparse-event cases for this component."""
    return {
        "fault": run_l2_light_fault_case(duration_s=duration_s),
        "degradation": run_l2_light_degradation_case(duration_s=duration_s),
        "combined": run_l2_light_combined_case(duration_s=duration_s),
    }


def run_all_modes() -> dict[str, Any]:
    """Run existing modes plus L2-light event/time-function cases."""
    results = _L2_LIGHT_PREV_RUN_ALL_MODES()
    results["l2_light"] = run_l2_light_cases()
    return results
