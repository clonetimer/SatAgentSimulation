"""Convert real native Basilisk 3/4 RW cluster samples to the TaskSpec trace.

No surrogate propagation, inferred applied torque, or silent fault fallback.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from sat_sim.adapter_base import SimulationResult
from sat_sim.bsk_engine.native_rw_cluster import run_native_rw_cluster


def run_native_cluster_component(spec: Mapping[str, Any]) -> SimulationResult:
    params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
    sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
    count = params["num_wheels"]
    if type(count) is not int or count not in (3, 4):
        raise ValueError("NATIVE_RW_CLUSTER_WHEEL_COUNT_UNSUPPORTED")
    native = run_native_rw_cluster(
        configuration="orthogonal_3" if count == 3 else "pyramid_4",
        duration_s=sim.get("duration_s", 3.),
        step_s=sim.get("sample_s", .1),
        inertia_kg_m2=params.get("wheel_inertia_kg_m2", .1),
        initial_speed_rad_s=params.get("initial_wheel_speeds_rad_s", 100.),
        command_torque_nm=params.get("command_torque_nm", .01),
        max_motor_torque_nm=params.get("max_motor_torque_nm", .02),
        max_speed_rad_s=params.get("max_speed_rad_s", 1000.),
        damping_nms=params.get("damping_nms", 1e-5),
    )

    commands = params.get("command_torque_nm", .01)
    if not isinstance(commands, (tuple, list)):
        commands = (commands,) * count
    task_obj = spec.get("task")
    task = task_obj if isinstance(task_obj, Mapping) else {}
    meta_obj = spec.get("metadata")
    meta = meta_obj if isinstance(meta_obj, Mapping) else {}
    task_id = str(spec.get("task_id") or task.get("id") or "rw_native_cluster_task")
    case_id = str(meta.get("case_id") or "case_000")
    rows: list[dict[str, Any]] = []
    for sample_i, (time_s, speeds, torques, friction) in enumerate(zip(
        native.times_s, native.wheel_speed_rad_s, native.applied_motor_torque_nm,
        native.measured_friction_torque_nm,
    )):
        h_b = [
            sum(native.factory_js_kg_m2[i] * speeds[i] * native.axes_b[i][axis]
                for i in range(count))
            for axis in range(3)
        ]
        norm_h = math.sqrt(sum(x * x for x in h_b))
        energy = .5 * sum(native.factory_js_kg_m2[i] * speeds[i]**2 for i in range(count))
        row: dict[str, Any] = {
            "task_id": task_id, "case_id": case_id, "time_s": time_s, "sample_index": sample_i,
            "target_level": "component", "target_name": "reaction_wheel",
            "mode": "nominal", "adcs.reaction_wheel.num_wheels": count,
            "adcs.reaction_wheel.max_abs_speed_rad_s": max(abs(x) for x in speeds),
            "adcs.reaction_wheel.momentum_norm_nms": norm_h,
            "adcs.reaction_wheel.rotational_energy_j": energy,
            "label.health_state": "nominal", "label.fault_active": False,
            "label.degradation_active": False, "label.constraint_active": False,
        }
        for i in range(count):
            row.update({
                f"adcs.reaction_wheel.speed_rad_s_{i}": speeds[i],
                f"adcs.reaction_wheel.command_torque_nm_{i}": float(commands[i]),
                f"adcs.reaction_wheel.applied_torque_nm_{i}": torques[i],
                f"adcs.reaction_wheel.friction_torque_nm_{i}": friction[i],
                f"adcs.reaction_wheel.nominal_max_torque_nm_{i}": native.factory_u_max_nm[i],
                f"adcs.reaction_wheel.effective_max_torque_nm_{i}": native.factory_u_max_nm[i],
                f"adcs.reaction_wheel.nominal_max_speed_rad_s_{i}": native.factory_omega_max_rad_s[i],
                f"adcs.reaction_wheel.effective_max_speed_rad_s_{i}": native.factory_omega_max_rad_s[i],
                f"adcs.reaction_wheel.nominal_damping_nms_{i}": native.viscous_damping_nms[i],
                f"adcs.reaction_wheel.damping_nms_{i}": native.viscous_damping_nms[i],
            })
        rows.append(row)
    r0, rf = rows[0], rows[-1]
    capability_id = "component.reaction_wheel.v1"
    qoi = {
        "adcs.reaction_wheel.initial_speed_rad_s_0": r0["adcs.reaction_wheel.speed_rad_s_0"],
        "adcs.reaction_wheel.final_speed_rad_s_0": rf["adcs.reaction_wheel.speed_rad_s_0"],
        "adcs.reaction_wheel.max_abs_speed_rad_s": max(
            row["adcs.reaction_wheel.max_abs_speed_rad_s"] for row in rows),
        "adcs.reaction_wheel.final_momentum_norm_nms": rf["adcs.reaction_wheel.momentum_norm_nms"],
        "adcs.reaction_wheel.final_rotational_energy_j": rf["adcs.reaction_wheel.rotational_energy_j"],
    }
    summary = {
        "task_id": task_id, "case_id": case_id, "status": "complete",
        "duration_s": float(sim.get("duration_s", 3.)),
        "sample_s": float(sim.get("sample_s", .1)),
        "target_level": "component", "target_name": "reaction_wheel",
        "capability_id": capability_id, "mode": "nominal", "trace_rows": len(rows),
        "physics_backend": "basilisk_native",
        "runtime_truth_status": "real_native_rw_spacecraft_recorded",
        "num_wheels": count, "qoi": qoi,
    }
    return SimulationResult(
        summary=summary, trace_rows=tuple(rows),
        labels={"run_labels": [{"task_id": task_id, "capability_id": capability_id,
                                "mode": "nominal", "physics_backend": "basilisk_native"}]},
        metadata={
            "capability_id": capability_id, "physics_backend": "basilisk_native",
            "modifiers_applied_by_adapter": True,
            "basilisk_official_native": ["spacecraft", "reactionWheelStateEffector"],
            "native_provenance": native.model_source,
            "native_axes_b": [list(x) for x in native.axes_b],
            "limits": ["nominal only", "fixed motor torque", "no faults/degradation",
                       "no FSW/EPS cross-level physical parity", "no restart acceptance"],
        },
    )
