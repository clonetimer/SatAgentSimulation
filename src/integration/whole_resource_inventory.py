"""Bind whole-spacecraft component references to the shared authority census."""
from __future__ import annotations

from typing import Any

from .power_authority_inventory import PowerAuthorityBinding, build_power_authority_inventory


def _message_key(message: Any) -> int:
    native = getattr(message, "this", None)
    return int(native) if native is not None else id(message)


def _assemble(sim, runtime, declarations, recorders, thermal_graph, adapters):
    network = getattr(thermal_graph, "network", thermal_graph)
    bridges = getattr(network, "power_bridges", {})
    destinations: dict[int, list[str]] = {}
    for node, bridge in bridges.items():
        for message in bridge.source_messages:
            destinations.setdefault(_message_key(message), []).append(node)
    bindings = []
    heaters = getattr(thermal_graph, "heaters", {})
    for model, attr, role, recorder_names in declarations:
        output = getattr(model, attr)
        targets = list(destinations.get(_message_key(output), ()))
        assumptions = ["Native implementation origin does not establish hardware calibration."]
        for field in ("nodePowerOut", "basePowerNeed", "elecToMechEfficiency", "mechToElecEfficiency"):
            value = getattr(model, field, None)
            if isinstance(value, (int, float)):
                assumptions.append(f"Configured {field}={float(value):g}; parameter/model assumption.")
        for node, heater in heaters.items():
            if heater is model:
                targets.append(node)
                efficiency = getattr(getattr(heater, "params", None), "efficiency", 1.0)
                assumptions.append(f"Project heater owns actual electrical draw; electrical-to-heat efficiency={float(efficiency):g}.")
        if role == "comm_load":
            assumptions.append("Native RF power or configured active/idle state profile; no measured flight calibration.")
        if role == "propulsion_load":
            assumptions.append("Fixed auxiliary load per actually active thruster; burn-start permission does not terminate an ongoing burn load.")
        if role == "solar_generation" and type(model).__name__ == "SimplePowerSink":
            assumptions.append("Explicit constant generation boundary; no live incidence/eclipse response in this selected variant.")
        bindings.append(PowerAuthorityBinding(
            model=model, output_attr=attr, role=role,
            recorder_names=tuple(name for name in recorder_names if name in recorders),
            thermal_destinations=tuple(sorted(set(targets))), assumptions=tuple(assumptions),
        ))
    return build_power_authority_inventory(
        runtime, bindings, status_adapters=tuple(model for model in adapters if model is not None),
        thermal_bridges=tuple(bridges.values()),
        scheduled_models=tuple(model for task in sim.TaskList for model in task.TaskModels),
    )


def composite_power_inventory(graph) -> dict[str, Any]:
    registry = graph.component_registry
    eps = graph.eps_graph
    # The constant-generation variant retains an unwired demonstration panel;
    # solar_source is the actual scheduled battery input in both EPS builders.
    solar = eps.solar_source
    declarations = [(solar, "nodePowerOutMsg", "solar_generation", ("solar_power",))]
    declarations.extend((model, "nodePowerOutMsg", "eps_bus_load", ()) for model in eps.load_sinks.values())
    declarations.append((registry["payload_power_node"], "nodePowerOutMsg", "payload_load", ("payload_power",)))
    for index, model in enumerate(registry["adcs_power_nodes"]):
        declarations.append((model, "nodePowerOutMsg", "adcs_load", (f"adcs_power_{index}",)))
    for index, model in enumerate(registry["comm_power_nodes"]):
        names = []
        if index == 0:
            names.append("comm_power_tx")
        if model is registry.get("comm_base_power_node"):
            names.append("comm_power_base")
        if model is registry.get("comm_idle_power_node"):
            names.append("comm_power_idle")
        declarations.append((model, "nodePowerOutMsg", "comm_load", names))
    for index, model in enumerate(registry["propulsion_power_nodes"]):
        declarations.append((model, "nodePowerOutMsg", "propulsion_load", (f"propulsion_power_{index}",)))
    for name, model in registry["heater_power_sources"].items():
        declarations.append((model, "electricalPowerOutMsg", "heater_load", (f"heater_eps_power_{name}",)))
    return _assemble(graph.sim, "basilisk.composite_digital_twin_graph", declarations,
        graph.recorders, graph.thermal_graph,
        [registry["payload_status_adapter"], registry["comm_status_adapter"], *registry["propulsion_status_adapters"]])


def unified_power_inventory(sim, modules, recorders) -> dict[str, Any]:
    declarations = [
        (modules["panel"], "nodePowerOutMsg", "solar_generation", ("panel",)),
        (modules["bus_power"], "nodePowerOutMsg", "eps_bus_load", ("bus_power",)),
        (modules["payload_power_node"], "nodePowerOutMsg", "payload_load", ("payload_power",)),
        (modules["comm_power_node"], "nodePowerOutMsg", "comm_load", ("downlink_power",)),
        (modules["comm_base_power_node"], "nodePowerOutMsg", "comm_load", ("comm_base_power",)),
    ]
    for index, model in enumerate(modules["adcs_power_nodes"]):
        declarations.append((model, "nodePowerOutMsg", "adcs_load", (f"adcs_power_{index}",)))
    for index, model in enumerate(modules.get("propulsion_power_nodes", ())):
        declarations.append((model, "nodePowerOutMsg", "propulsion_load", (f"propulsion_power_{index}", f"propulsion_health_power_{index}")))
    for name, model in modules["thermal_graph"].heaters.items():
        declarations.append((model, "electricalPowerOutMsg", "heater_load", (f"thermal_heater_electrical_{name}",)))
    return _assemble(sim, "basilisk.unified_native", declarations, recorders, modules["thermal_graph"],
        [modules["payload_status_adapter"], modules["comm_status_adapter"], *modules.get("propulsion_status_adapters", ())])
