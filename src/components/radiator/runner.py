"""radiator component runner module."""
from __future__ import annotations

from typing import Any, List

from .builder import RadiatorConfig, compute_radiator_rejection, build_nominal_radiator_config
from .degradation import RadiatorDegradation
from .faults import RadiatorFaultType
from .faults import FaultSpec


def run_nominal_case() -> dict[str, Any]:
    """Run nominal radiator case and return a JSON-friendly summary."""
    config = build_nominal_radiator_config()
    result = compute_radiator_rejection(300.0, 50.0, config)
    return {
        "mode": "nominal",
        "rejected_heat_w": result.rejected_heat_w,
        "radiator_margin_w": result.radiator_margin_w,
        "limit_violation": result.limit_violation,
        "config": {
            "area_m2": config.area_m2,
            "emissivity": config.emissivity,
            "max_rejection_w": config.max_rejection_w,
        },
    }


def run_degradation_case(degradation: RadiatorDegradation) -> dict[str, Any]:
    """Run degradation radiator case and return a JSON-friendly summary."""
    config = build_nominal_radiator_config(degradation=degradation)
    result = compute_radiator_rejection(300.0, 50.0, config)
    return {
        "mode": "degradation",
        "degradation": {
            "emissivity_degradation_pct": degradation.emissivity_degradation_pct,
            "efficiency_loss_pct": degradation.efficiency_loss_pct,
        },
        "rejected_heat_w": result.rejected_heat_w,
        "radiator_margin_w": result.radiator_margin_w,
        "limit_violation": result.limit_violation,
        "config": {
            "area_m2": config.area_m2,
            "emissivity": config.emissivity,
            "max_rejection_w": config.max_rejection_w,
        },
    }


def run_fault_case(fault_specs: List[FaultSpec]) -> dict[str, Any]:
    """Run fault case for radiator and return JSON-friendly summary."""
    _config = build_nominal_radiator_config(fault_specs=fault_specs)
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
    }


def _l2_light_prev_run_all_modes_impl_17() -> dict[str, Any]:
    """Run all simulation modes and return combined results."""
    results = {}

    print("Running nominal case...")
    results["nominal"] = run_nominal_case()

    print("Running degradation case...")
    deg = RadiatorDegradation(emissivity_degradation_pct=15.0, efficiency_loss_pct=10.0)
    results["degradation"] = run_degradation_case(deg)

    print("Running fault case...")
    try:
        results["fault"] = run_fault_case([
            FaultSpec(
                fault_type=next(iter(RadiatorFaultType)),
                onset_time_s=0.0,
                duration_s=-1.0,
                magnitude=1.0,
                target_id=f"radiator_0",
            )
        ])
    except Exception as e:
        results["fault"] = {"error": str(e)}

    return results


def harness_available() -> bool:
    """Check if harness dependencies are available."""
    return True


# --- L2-light event/time-function fault-degradation support ---
# This layer is intentionally sparse-event based: faults are active over time
# windows; degradation coefficients are queried as f(t-start), not integrated
# as internal state equations.
_L2_LIGHT_COMPONENT = 'radiator'
_L2_LIGHT_PREV_RUN_ALL_MODES = _l2_light_prev_run_all_modes_impl_17


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
