"""Measured electrical-to-thermal transfer, separate from source ownership."""
from __future__ import annotations

import math
from typing import Any


def build_thermal_resource_evidence(thermal_graph: Any) -> dict[str, Any]:
    """Compare published heat with energy consumed by each actual thermal node.

    External dynamics samples integrate held native outputs. A thermal update
    consumes its completed interval; a final partial interval remains pending.
    Passive nodes without a bridge have no electrical-to-heat transfer to audit.
    This evidence does not claim calibration of heat fractions or account for
    independent solar, radiation, conduction, or reduced-model cooling terms.
    """
    network = getattr(thermal_graph, "network", thermal_graph)
    bridges = getattr(network, "power_bridges", {})
    nodes = getattr(network, "nodes", {})
    records: list[dict[str, Any]] = []
    for name, bridge in sorted(bridges.items()):
        node = nodes.get(name)
        received = getattr(node, "cumulative_external_heat_energy_j", None)
        delivered = float(bridge.cumulative_heat_energy_j)
        pending = float(bridge.pending_heat_energy_j)
        errors = list(getattr(bridge, "read_errors", ()))
        if node is None or received is None:
            errors.append("receiving thermal node has no measured input-energy evidence")
        elif not math.isfinite(float(received)):
            errors.append("receiving thermal node energy is nonfinite")
        if not all(math.isfinite(value) for value in (delivered, pending)):
            errors.append("thermal bridge energy is nonfinite")
        if getattr(node, "input_read_status", "VALID") == "ERROR":
            errors.append(str(getattr(node, "input_read_error", "thermal input read failed")))
        if int(getattr(bridge, "input_error_count", 0)):
            errors.append("a native power input was invalid during the observed interval")
        residual = None if received is None else delivered - float(received)
        tolerance = max(1e-7, abs(delivered) * 1e-10)
        if residual is not None and (not math.isfinite(residual) or abs(residual) > tolerance):
            errors.append("published heat differs from actual receiving-node input energy")
        source_electric = dict(bridge.cumulative_source_electrical_energy_j)
        source_heat = dict(bridge.cumulative_source_heat_energy_j)
        pending_electric = dict(bridge.pending_source_electrical_energy_j)
        pending_heat = dict(bridge.pending_source_heat_energy_j)
        sources = [{
            "label": label,
            "consumed_input_energy_j": float(source_electric.get(label, 0.0)),
            "deposited_heat_energy_j": float(source_heat.get(label, 0.0)),
            "pending_input_energy_j": float(pending_electric.get(label, 0.0)),
            "pending_heat_energy_j": float(pending_heat.get(label, 0.0)),
            # Local heater powerOutMsg is already thermal output; it must not
            # be described as pre-efficiency electrical consumption.
            "input_quantity": "already_converted_heater_heat" if label.startswith("heater.") else "electrical_power_magnitude",
        } for label in sorted(set(source_electric) | set(source_heat) | set(pending_electric))]
        sampler = getattr(bridge, "power_sampler", None)
        records.append({
            "node": name,
            "bridge_class": f"{type(bridge).__module__}.{type(bridge).__name__}",
            "status": "FAIL" if errors else "PASS",
            "electrical_sampling": "native_dynamics_zero_order_hold" if sampler is not None else "thermal_task_current_value",
            "native_power_sample_count": int(getattr(sampler, "sample_count", 0)),
            "power_observed_until_s": None if sampler is None or sampler.last_ns is None else sampler.last_ns * 1e-9,
            "thermal_consumed_until_s": None if bridge._last_ns is None else bridge._last_ns * 1e-9,
            "published_heat_energy_j": delivered,
            "received_heat_energy_j": None if received is None else float(received),
            "pending_heat_energy_j": pending,
            "transfer_residual_j": residual,
            "transfer_tolerance_j": tolerance,
            "input_error_count": int(getattr(bridge, "input_error_count", 0)),
            "errors": errors[:10],
            "sources": sources,
        })
    # Local transfer equality alone cannot detect the same electrical input
    # being fully deposited into several nodes. Group by the actual native
    # message, including SWIG aliases, and enforce its whole-graph heat budget.
    routes: dict[int, dict[str, Any]] = {}
    for node_name, bridge in sorted(bridges.items()):
        for message, fraction, label in zip(bridge.source_messages, bridge.efficiency_factors, bridge.source_labels):
            pointer = getattr(message, "this", None)
            identity = int(pointer) if pointer is not None else id(message)
            route = routes.setdefault(identity, {
                "source_id": f"thermal_input_{len(routes)}", "heat_fraction_sum": 0.0, "destinations": [],
            })
            route["heat_fraction_sum"] += float(fraction)
            route["destinations"].append({"node": node_name, "label": label, "heat_fraction": float(fraction)})
    routing_errors = [
        f"{route['source_id']}: summed heat fraction {route['heat_fraction_sum']:g} exceeds one source's energy"
        for route in routes.values()
        if not math.isfinite(route["heat_fraction_sum"]) or not 0.0 <= route["heat_fraction_sum"] <= 1.0 + 1e-10
    ]
    heater_records: list[dict[str, Any]] = []
    observed_end_s = max((record["power_observed_until_s"] or record["thermal_consumed_until_s"] or 0.0 for record in records), default=0.0)
    for name, heater in sorted(getattr(network, "heaters", {}).items()):
        electrical = getattr(heater, "cumulative_heater_electrical_energy_j", None)
        deposited = getattr(heater, "cumulative_heater_heat_energy_j", None)
        efficiency = float(getattr(getattr(heater, "params", None), "efficiency", 1.0))
        errors = []
        if electrical is None or deposited is None:
            errors.append("heater lacks completed-interval electrical/deposition evidence")
        elif not all(math.isfinite(float(value)) for value in (electrical, deposited)):
            errors.append("heater completed-interval energy is nonfinite")
        residual = None if electrical is None or deposited is None else float(deposited) - float(electrical) * efficiency
        if residual is not None and (not math.isfinite(residual) or abs(residual) > max(1e-7, abs(float(deposited)) * 1e-10)):
            errors.append("heater deposited heat differs from completed-interval electrical input times efficiency")
        # Network heaters deposit through their node bridge; reduced owners
        # integrate the local heater directly and expose its actual energy.
        bridge = bridges.get(name)
        bridge_heat = None
        if bridge is not None and f"heater.{name}" in bridge.cumulative_source_heat_energy_j:
            bridge_heat = float(bridge.cumulative_source_heat_energy_j[f"heater.{name}"])
            if deposited is not None and abs(bridge_heat - float(deposited)) > max(1e-7, abs(bridge_heat) * 1e-10):
                errors.append("heater output energy differs from the receiving bridge's heater channel")
        if int(getattr(heater, "input_error_count", 0)):
            errors.append("heater had invalid native enable or power input")
        last_ns = getattr(heater, "_power_last_ns", getattr(heater, "last_ns", None))
        last_s = float(last_ns) * 1e-9 if last_ns is not None else 0.0
        pending_electrical = float(heater.last_electrical_power_w) * max(0.0, observed_end_s - last_s)
        heater_records.append({
            "node": name, "status": "FAIL" if errors else "PASS", "errors": errors,
            "source": "actual owner held electrical output over completed thermal intervals",
            "electrical_energy_j": None if electrical is None else float(electrical),
            "deposited_heat_energy_j": None if deposited is None else float(deposited),
            "efficiency": efficiency, "conversion_residual_j": residual,
            "bridge_received_heater_heat_j": bridge_heat,
            "completed_until_s": last_s, "power_observed_until_s": observed_end_s,
            "pending_electrical_energy_j": pending_electrical,
            "pending_heat_energy_j": pending_electrical * efficiency,
        })
    evidence_records = records + heater_records
    pending_external = sum(record["pending_heat_energy_j"] for record in records)
    pending_heater = sum(record["pending_heat_energy_j"] for record in heater_records)
    return {
        "schema_version": "sat-sim.thermal-resource-evidence.v1",
        "status": "FAIL" if routing_errors else "PASS" if evidence_records and all(record["status"] == "PASS" for record in evidence_records) else "FAIL" if evidence_records else "NOT_APPLICABLE",
        "scope": "actual electrical/converted-heater inputs through bridges into receiving thermal integrators",
        "pending_semantics": "sampled energy after the last actual thermal update; not yet deposited",
        "heat_fraction_semantics": "declared engineering assumptions, not flight-calibrated conversion",
        "nodes": records,
        "heaters": heater_records,
        "source_routes": list(routes.values()),
        "routing_errors": routing_errors,
        "published_heat_energy_j": sum(record["published_heat_energy_j"] for record in records),
        "received_heat_energy_j": sum(record["received_heat_energy_j"] or 0.0 for record in records),
        "pending_external_heat_energy_j": pending_external,
        "pending_heater_heat_energy_j": pending_heater,
        "pending_heat_energy_j": pending_external + pending_heater,
    }
