"""Runner helpers for the battery component."""
from __future__ import annotations

from typing import Any, List

from .model import (
    apply_battery_config_faults,
    build_nominal_battery_config,
    initialize_battery,
    simulate_battery_power_profile,
)
from .degradation import BatteryDegradation
from .faults import BatteryFaultType
from .faults import FaultSpec


def harness_available() -> bool:
    return True


def run_nominal_case() -> dict[str, Any]:
    config = build_nominal_battery_config()
    state = initialize_battery(config)
    result = simulate_battery_power_profile(state, config, [25, -25], 3600)
    return {
        "mode": "nominal",
        "capacity_wh": config.capacity_wh,
        "initial_soc": config.initial_soc,
        "steps": len(result.time_s),
        "final_soc": result.soc[-1],
        "config": {
            "capacity_wh": config.capacity_wh,
            "charge_efficiency": config.charge_efficiency,
            "discharge_efficiency": config.discharge_efficiency,
        },
    }


def run_degradation_case(degradation: BatteryDegradation) -> dict[str, Any]:
    config = build_nominal_battery_config(degradation=degradation)
    state = initialize_battery(config)
    result = simulate_battery_power_profile(state, config, [25, -25], 3600)
    return {
        "mode": "degradation",
        "degradation": {
            "capacity_loss_pct": degradation.capacity_loss_pct,
            "internal_resistance_increase_pct": degradation.internal_resistance_increase_pct,
        },
        "capacity_wh": config.capacity_wh,
        "initial_soc": config.initial_soc,
        "steps": len(result.time_s),
        "final_soc": result.soc[-1],
        "config": {
            "capacity_wh": config.capacity_wh,
            "charge_efficiency": config.charge_efficiency,
            "discharge_efficiency": config.discharge_efficiency,
        },
    }


def run_fault_case(fault_specs: List[FaultSpec]) -> dict[str, Any]:
    config = build_nominal_battery_config(fault_specs=fault_specs)
    state = initialize_battery(config)
    result = simulate_battery_power_profile(state, config, [25, -25], 3600)
    return {
        "mode": "fault",
        "faults": [
            {
                "fault_type": spec.fault_type.value,
                "onset_time_s": spec.onset_time_s,
                "duration_s": spec.duration_s,
                "magnitude": spec.magnitude,
                "target_id": spec.target_id,
            }
            for spec in fault_specs
        ],
        "capacity_wh": config.capacity_wh,
        "initial_soc": config.initial_soc,
        "steps": len(result.time_s),
        "final_soc": result.soc[-1],
        "config": {
            "capacity_wh": config.capacity_wh,
            "charge_efficiency": config.charge_efficiency,
            "discharge_efficiency": config.discharge_efficiency,
        },
    }


def _legacy_run_all_modes_impl_2() -> dict[str, Any]:
    results = {}
    print("Running nominal case...")
    results["nominal"] = run_nominal_case()
    print("Running degradation case...")
    results["degradation"] = run_degradation_case(
        BatteryDegradation(capacity_loss_pct=30.0, internal_resistance_increase_pct=50.0)
    )
    print("Running fault case (sudden_capacity_loss)...")
    results["fault_sudden_capacity_loss"] = run_fault_case(
        [
            FaultSpec(
                fault_type=BatteryFaultType.SuddenCapacityLoss,
                onset_time_s=0.0,
                duration_s=-1.0,
                magnitude=0.5,
                target_id="battery_0",
            )
        ]
    )
    print("Running fault case (open_circuit)...")
    results["fault_open_circuit"] = run_fault_case(
        [
            FaultSpec(
                fault_type=BatteryFaultType.OpenCircuit,
                onset_time_s=0.0,
                duration_s=-1.0,
                magnitude=1.0,
                target_id="battery_0",
            )
        ]
    )
    return results


# --- Component normal working-scenario support ---
# Nominal is retained for backward compatibility; normal_scenarios exercises
# multiple non-fault, non-degradation operating conditions.
_LEGACY_RUN_ALL_MODES = _legacy_run_all_modes_impl_2
_LEGACY_RUN_NOMINAL_CASE = run_nominal_case
_WORKING_SCENARIOS = {
    'nominal': 'reference charge/discharge profile',
    'charge_window': 'battery charging while solar power is available',
    'eclipse_discharge': 'battery discharging during eclipse loads',
    'orbit_cycle': 'charge-discharge-charge orbit power cycle',
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
        return scenario_result("battery", scenario, _WORKING_SCENARIOS[scenario], _LEGACY_RUN_NOMINAL_CASE())
    profiles = {
        "charge_window": [60.0, 60.0, 20.0],
        "eclipse_discharge": [-80.0, -80.0, -40.0],
        "orbit_cycle": [70.0, 50.0, -90.0, -60.0, 25.0],
    }
    if scenario not in profiles:
        raise ValueError(f"Unsupported battery working scenario: {scenario}")
    config = build_nominal_battery_config(initial_soc=0.5)
    state = initialize_battery(config)
    result = simulate_battery_power_profile(state, config, profiles[scenario], 600.0)
    return scenario_result(
        "battery",
        scenario,
        _WORKING_SCENARIOS[scenario],
        {
            "power_profile_w": profiles[scenario],
            "time_s": list(result.time_s),
            "soc": list(result.soc),
            "final_soc": result.soc[-1],
            "min_soc": min(result.soc),
            "max_soc": max(result.soc),
            "shunt_dissipated_wh": list(result.shunt_dissipated_wh),
            "config": {"capacity_wh": config.capacity_wh, "initial_soc": config.initial_soc},
        },
    )


def run_working_scenarios() -> dict[str, Any]:
    """Run every supported normal operating scenario for this component."""
    from ..runner_common import scenario_error
    results: dict[str, Any] = {}
    for scenario in list_working_scenarios():
        try:
            results[scenario] = run_working_scenario(scenario)
        except Exception as exc:  # keep all scenarios observable in batch runs
            results[scenario] = scenario_error("battery", scenario, _WORKING_SCENARIOS.get(scenario, ""), exc)
    return results


def _l2_light_prev_run_all_modes_impl_2() -> dict[str, Any]:
    """Run legacy modes plus all normal operating scenarios."""
    results = _LEGACY_RUN_ALL_MODES()
    results["normal_scenarios"] = run_working_scenarios()
    return results


# --- L2-light event/time-function fault-degradation support ---
# This layer is intentionally sparse-event based: faults are active over time
# windows; degradation coefficients are queried as f(t-start), not integrated
# as internal state equations.
_L2_LIGHT_COMPONENT = 'battery'
_L2_LIGHT_PREV_RUN_ALL_MODES = _l2_light_prev_run_all_modes_impl_2


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
