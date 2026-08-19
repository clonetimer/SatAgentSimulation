"""Thermal subsystem builder helpers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from components.heater.builder import build_nominal_heater_config
from components.thermal_node.builder import build_nominal_thermal_node_config
from components.fault_spec import FaultSpec
from components.heater.faults import HeaterFaultType

from .schemas import ThermalBasiliskConfig, ThermalConfig
from .degradation import ThermalDegradation, apply_thermal_degradation


@dataclass(frozen=True)
class ThermalBasiliskAssemblyGraph:
    heat_input: Any
    evaluator: Any
    config: Any
    shadow_converter: Any | None = None
    nodes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ThermalNetworkAssemblyGraph:
    network: Any
    evaluator: Any
    config: Any
    solar_input: Any | None = None
    nodes: dict[str, Any] = field(default_factory=dict)
    heaters: dict[str, Any] = field(default_factory=dict)
    radiators: dict[str, Any] = field(default_factory=dict)
    power_bridges: dict[str, Any] = field(default_factory=dict)


def build_nominal_thermal_config(degradation: Optional[ThermalDegradation] = None) -> ThermalConfig:
    """Build a deterministic multi-node thermal configuration."""

    cfg = ThermalConfig(
        nodes={
            "battery": build_nominal_thermal_node_config(ambient_k=285.0, tau_s=600.0, heat_gain_k_per_w=1.5, min_temp_k=220.0, max_temp_k=360.0),
            "electronics": build_nominal_thermal_node_config(ambient_k=295.0, tau_s=300.0, heat_gain_k_per_w=2.0, min_temp_k=220.0, max_temp_k=380.0),
            "payload": build_nominal_thermal_node_config(ambient_k=290.0, tau_s=450.0, heat_gain_k_per_w=2.5, min_temp_k=220.0, max_temp_k=390.0),
        },
        initial_temp_k_by_node={
            "battery": 290.0,
            "electronics": 295.0,
            "payload": 292.0,
        },
        component_node_map={
            "eps": "electronics",
            "obc": "electronics",
            "comm": "electronics",
            "payload": "payload",
            "battery_heater": "battery",
        },
        heat_efficiency_by_component={
            "eps": 0.8,
            "obc": 1.0,
            "comm": 0.9,
            "payload": 1.0,
        },
        mode_power_w_by_node={
            "safePoint": {"electronics": 6.0},
            "payload": {"electronics": 10.0, "payload": 25.0},
            "downlink": {"electronics": 20.0},
        },
        sunlight_heat_w_by_node={
            "battery": 2.0,
            "electronics": 3.0,
            "payload": 5.0,
        },
        heaters={
            "battery": build_nominal_heater_config(max_power_w=12.0, setpoint_k=286.0, hysteresis_k=5.0),
        },
        under_temp_limit_k_by_node={"battery": 285.0, "electronics": 270.0, "payload": 270.0},
        over_temp_limit_k_by_node={"battery": 320.0, "electronics": 330.0, "payload": 340.0},
        safe_request_under_temp_nodes=("battery",),
        safe_request_over_temp_nodes=("electronics", "payload"),
    )

    if degradation is not None:
        cfg = apply_thermal_degradation(cfg, degradation)

    return cfg


def apply_thermal_config_faults(cfg: ThermalConfig, fault_specs: list[FaultSpec]) -> ThermalConfig:
    """Apply faults to thermal config."""

    from dataclasses import replace

    degraded_heaters = dict(cfg.heaters)
    for fault in fault_specs:
        if isinstance(fault.fault_type, HeaterFaultType):
            for name, heater in degraded_heaters.items():
                if fault.fault_type == HeaterFaultType.Failure:
                    degraded_heaters[name] = replace(heater, max_power_w=0.0)
                elif fault.fault_type == HeaterFaultType.Overheating:
                    degraded_heaters[name] = replace(heater, setpoint_k=heater.setpoint_k * (1.0 + fault.magnitude * 0.1))

    return replace(cfg, heaters=degraded_heaters)


def build_nominal_thermal_steps() -> tuple:
    """Build a deterministic thermal profile.

    The profile covers safe sunlight, payload high-power heating, eclipse
    cooling, heater activation, and downlink electronics heating.
    """

    from .schemas import ThermalStepInput

    return (
        ThermalStepInput(dt_s=120.0, mode="safePoint", shadow_factor=1.0, component_power_w={"eps": 5.0}),
        ThermalStepInput(dt_s=120.0, mode="payload", shadow_factor=1.0, component_power_w={"payload": 20.0, "obc": 8.0}),
        ThermalStepInput(dt_s=120.0, mode="payload", shadow_factor=1.0, component_power_w={"payload": 25.0, "obc": 8.0}),
        ThermalStepInput(dt_s=120.0, mode="safePoint", shadow_factor=0.0, component_power_w={"eps": 3.0}, ambient_k_by_node={"battery": 270.0, "electronics": 285.0, "payload": 280.0}),
        ThermalStepInput(dt_s=120.0, mode="safePoint", shadow_factor=0.0, component_power_w={"eps": 3.0}, ambient_k_by_node={"battery": 270.0, "electronics": 285.0, "payload": 280.0}),
        ThermalStepInput(dt_s=120.0, mode="downlink", shadow_factor=1.0, component_power_w={"comm": 15.0, "obc": 8.0}),
    )


def build_thermal_subsystem(config: ThermalConfig | None = None, *, backend: str = "basilisk") -> ThermalConfig | dict[str, object]:
    """Build and run a Basilisk-scheduled thermal subsystem.
    
    Args:
        config: Thermal configuration. If None, uses nominal config.
        backend: Backend to use. Only "basilisk" is supported.
    
    Returns:
        dict: Thermal subsystem simulation summary including backend, status,
            sample count, final temperature, heater activation count, and
            unsafe sample count.
    
    Raises:
        ValueError: If backend is not "basilisk"
    """
    if backend != "basilisk":
        raise ValueError("Thermal Python backend has been removed; use backend='basilisk'")
    from .basilisk_scheduled import ThermalScheduledConfig, run_thermal_scheduled_scenario

    cfg = config or build_nominal_thermal_config()
    heater_power_w = max((float(heater.max_power_w) for heater in cfg.heaters.values()), default=0.0)
    summary, _ = run_thermal_scheduled_scenario(
        ThermalScheduledConfig(
            initial_temp_c=float(cfg.initial_temp_k_by_node.get("electronics", 295.0) - 273.15),
            ambient_temp_c=float(cfg.nodes["electronics"].ambient_k - 273.15) if "electronics" in cfg.nodes else 20.0,
            min_safe_temp_c=float(cfg.under_temp_limit_k_by_node.get("electronics", 270.0) - 273.15),
            max_safe_temp_c=float(cfg.over_temp_limit_k_by_node.get("electronics", 330.0) - 273.15),
            heat_power_w=float(cfg.mode_power_w_by_node.get("payload", {}).get("electronics", 10.0)),
            heater_power_w=heater_power_w,
        )
    )
    return {
        "backend": summary.backend,
        "status": summary.status,
        "sample_count": summary.sample_count,
        "final_temp_c": summary.final_temp_c,
        "heater_activation_count": summary.heater_activation_count,
        "unsafe_sample_count": summary.unsafe_sample_count,
    }


def build_thermal_basilisk_assembly_graph(
    *,
    duration_s: float = 300.0,
    step_s: float = 10.0,
    heat_power_w: float = 25.0,
    initial_temp_c: float = 22.0,
    ambient_temp_c: float = 18.0,
    power_inputs: list[tuple] | None = None,
    shadow_factor_msg=None,
    thermal_cfg: ThermalConfig | None = None,
) -> ThermalBasiliskAssemblyGraph:
    """Build Basilisk thermal assembly graph for scheduled thermal simulation.
    
    Creates a graph containing heat input, thermal nodes, and status evaluator.
    
    Args:
        duration_s: Simulation duration in seconds
        step_s: Time step in seconds
        heat_power_w: Constant heat power input in watts (used if power_inputs is None)
        power_inputs: List of (power_message, efficiency_factor) tuples for dynamic power
        shadow_factor_msg: Optional shadow factor message for eclipse simulation
        thermal_cfg: Optional thermal configuration with nodes and heaters
    
    Returns:
        ThermalBasiliskAssemblyGraph: Assembly graph containing all thermal components
    """
    from .basilisk_scheduled import ConstantHeatInput, DynamicThermalPowerInput, ThermalScheduledConfig, ThermalStatusEvaluator, ThermalNodeScheduledSysModel, ThermalNodeScheduledConfig

    cfg = ThermalScheduledConfig(
        duration_s=float(duration_s),
        step_s=float(step_s),
        heat_power_w=float(heat_power_w),
        initial_temp_c=float(initial_temp_c),
        ambient_temp_c=float(ambient_temp_c),
    )
    
    if power_inputs:
        heat_input = DynamicThermalPowerInput()
        for item in power_inputs:
            if len(item) < 2:
                raise ValueError("power_inputs entries must include a message and efficiency")
            power_msg, efficiency_factor = item[0], item[1]
            heat_input.add_power_input(power_msg, efficiency_factor)
        if shadow_factor_msg is not None:
            heat_input.set_shadow_factor_input(shadow_factor_msg)
    else:
        heat_input = ConstantHeatInput(cfg.heat_power_w)
    
    nodes: dict[str, Any] = {}
    
    if thermal_cfg and thermal_cfg.nodes:
        for node_name, node_config in thermal_cfg.nodes.items():
            node_sched_cfg = ThermalNodeScheduledConfig(
                node_name=node_name,
                initial_temp_c=float(thermal_cfg.initial_temp_k_by_node.get(node_name, node_config.ambient_k) - 273.15),
                ambient_temp_c=float(node_config.ambient_k - 273.15),
                min_safe_temp_c=float(thermal_cfg.under_temp_limit_k_by_node.get(node_name, 270.0) - 273.15),
                max_safe_temp_c=float(thermal_cfg.over_temp_limit_k_by_node.get(node_name, 330.0) - 273.15),
                thermal_capacity_j_per_c=float(node_config.tau_s * node_config.heat_gain_k_per_w),
                conductance_w_per_c=float(1.0 / (node_config.tau_s * node_config.heat_gain_k_per_w)),
            )
            
            heater = thermal_cfg.heaters.get(node_name)
            if heater:
                setpoint_c = float(heater.setpoint_k - 273.15)
                hysteresis_c = float(heater.hysteresis_k)
                node_sched_cfg = node_sched_cfg.__class__(
                    **{**node_sched_cfg.__dict__, 
                       'heater_power_w': float(heater.max_power_w),
                       'heater_on_below_c': setpoint_c - hysteresis_c / 2.0,
                       'heater_off_above_c': setpoint_c + hysteresis_c / 2.0}
                )
            
            node = ThermalNodeScheduledSysModel(node_sched_cfg)
            node.heatInMsg.subscribeTo(heat_input.heatOutMsg)
            nodes[node_name] = node
    
    if nodes:
        evaluator = nodes.get("battery") or list(nodes.values())[0]
    else:
        evaluator = ThermalStatusEvaluator(cfg)
        evaluator.heatInMsg.subscribeTo(heat_input.heatOutMsg)
    
    return ThermalBasiliskAssemblyGraph(heat_input=heat_input, evaluator=evaluator, config=cfg, nodes=nodes)


def attach_thermal_basilisk_graph_to_task(sim: Any, task_name: str, graph: ThermalBasiliskAssemblyGraph) -> None:
    """Attach thermal Basilisk assembly graph to a simulation task.
    
    Args:
        sim: Basilisk SimulationBaseClass instance
        task_name: Name of the task to attach to
        graph: Thermal Basilisk assembly graph
    """
    sim.AddModelToTask(task_name, graph.heat_input)
    if graph.shadow_converter is not None:
        sim.AddModelToTask(task_name, graph.shadow_converter)
    if graph.nodes:
        for node in graph.nodes.values():
            sim.AddModelToTask(task_name, node)
    else:
        sim.AddModelToTask(task_name, graph.evaluator)


def build_thermal_network_assembly_graph(
    *,
    duration_s: float = 3600.0,
    step_s: float = 10.0,
    power_inputs: list[tuple] | None = None,
    shadow_factor_msg=None,
    eclipse_msg=None,
    thermal_network_cfg=None,
    thermal_degradation: ThermalDegradation | None = None,
) -> ThermalNetworkAssemblyGraph:
    """Build multi-node thermal network assembly graph.
    
    Creates a comprehensive thermal network with conduction paths,
    heaters, radiators, and solar heating.
    
    Args:
        duration_s: Simulation duration in seconds
        step_s: Time step in seconds
        power_inputs: Node-routed tuples ``(message, efficiency, node, label)``.
            Legacy two-tuples are routed to the electronics node.
        shadow_factor_msg: Deprecated compatibility input; use ``eclipse_msg``.
        eclipse_msg: Native Basilisk EclipseMsg consumed by the solar input.
        thermal_network_cfg: Optional thermal network configuration.
            If None, uses nominal configuration.
    
    Returns:
        ThermalNetworkAssemblyGraph: Assembly graph with thermal network
    """
    from .thermal_network import ThermalNetworkSysModel
    from .network_config import (
        apply_degradation_to_network_config,
        build_nominal_thermal_network_config,
    )

    if thermal_network_cfg is None:
        network_config = build_nominal_thermal_network_config()
    else:
        network_config = thermal_network_cfg

    if thermal_degradation is not None:
        network_config = apply_degradation_to_network_config(
            network_config,
            heater_efficiency_loss_pct=float(thermal_degradation.heater_degradation.efficiency_loss_pct),
            radiator_efficiency_loss_pct=float(thermal_degradation.radiator_degradation.efficiency_loss_pct),
            radiator_emissivity_loss_pct=float(thermal_degradation.radiator_degradation.emissivity_degradation_pct),
        )

    network_config = network_config.__class__(
        **{**network_config.__dict__,
           'duration_s': max(float(duration_s), float(step_s)),
           'step_s': float(step_s)}
    )

    inputs_by_node: dict[str, list[tuple[object, float, str]]] = {}
    for item in power_inputs or []:
        if len(item) >= 4:
            power_msg, efficiency, node_name, source_label = item[:4]
        elif len(item) == 3:
            power_msg, efficiency, node_name = item
            source_label = str(node_name)
        elif len(item) == 2:
            power_msg, efficiency = item
            node_name = "electronics"
            source_label = "legacy_power_input"
        else:
            raise ValueError(f"invalid thermal power input tuple: {item!r}")
        node_name = str(node_name)
        if node_name not in network_config.nodes:
            node_name = "electronics" if "electronics" in network_config.nodes else next(iter(network_config.nodes))
        inputs_by_node.setdefault(node_name, []).append(
            (power_msg, max(0.0, float(efficiency)), str(source_label))
        )

    network = ThermalNetworkSysModel(
        network_config,
        external_power_inputs_by_node=inputs_by_node,
        eclipse_msg=eclipse_msg,
    )

    return ThermalNetworkAssemblyGraph(
        network=network,
        evaluator=network,
        config=network_config,
        solar_input=None,
        nodes=network.nodes,
        heaters=network.heaters,
        radiators=network.radiators,
        power_bridges=network.power_bridges,
    )


def attach_thermal_network_graph_to_task(sim: Any, task_name: str, graph: ThermalNetworkAssemblyGraph) -> None:
    """Attach thermal network assembly graph to a simulation task.
    
    Args:
        sim: Basilisk SimulationBaseClass instance
        task_name: Name of the task to attach to
        graph: Thermal network assembly graph
    """
    graph.network.build(sim, task_name)
    sim.AddModelToTask(task_name, graph.network)

@dataclass(frozen=True)
class ThermalBasiliskSimContext:
    """Built thermal Basilisk-scheduled simulation graph; execution is left to runner.py."""

    subsystem: str
    config: "ThermalBasiliskConfig"
    simulation: Any
    process: Any
    task: Any
    task_name: str
    modules: dict[str, Any]
    recorders: dict[str, Any]
    message_handles: dict[str, Any]
    base_parameters: dict[str, Any]
    component_sources: dict[str, str]


def _require_thermal_basilisk() -> None:
    try:
        from Basilisk.utilities import SimulationBaseClass, macros  # noqa: F401
        from components.thermal_node.basilisk_scheduled import require_basilisk_scheduled_thermal
        require_basilisk_scheduled_thermal()
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Basilisk thermal scheduled modules are unavailable: {exc}") from exc


def build_thermal_basilisk_sim(config: "ThermalBasiliskConfig | None" = None) -> ThermalBasiliskSimContext:
    """Build the thermal Basilisk-scheduled simulation context without executing it.

    The thermal node is instantiated through the component-level scheduled
    thermal backend; heater/radiator/thermal-node component configs are preserved
    in ``base_parameters`` for fault/degradation mapping.
    """

    from .schemas import ThermalBasiliskConfig
    from Basilisk.utilities import SimulationBaseClass, macros
    from components.radiator.builder import build_nominal_radiator_config
    from components.thermal_node.basilisk_scheduled import (
        ConstantThermalPowerInput,
        ThermalNodeScheduledConfig,
        ThermalNodeScheduledSysModel,
    )

    _require_thermal_basilisk()
    cfg = config or ThermalBasiliskConfig()
    if cfg.step_s <= 0 or cfg.duration_s <= 0:
        raise ValueError("duration_s and step_s must be positive")

    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("thermalBasiliskProcess")
    task_name = "thermalBasiliskTask"
    task = sim.CreateNewTask(task_name, macros.sec2nano(float(cfg.step_s)))
    process.addTask(task)

    heat_input = ConstantThermalPowerInput(float(cfg.heat_power_w), tag="thermalConstantHeatInput")
    node_cfg = ThermalNodeScheduledConfig(
        node_name=str(cfg.node_name),
        initial_temp_c=float(cfg.initial_temp_c),
        ambient_temp_c=float(cfg.ambient_temp_c),
        min_safe_temp_c=float(cfg.min_safe_temp_c),
        max_safe_temp_c=float(cfg.max_safe_temp_c),
        thermal_capacity_j_per_c=float(cfg.thermal_capacity_j_per_c),
        conductance_w_per_c=float(cfg.conductance_w_per_c),
        heater_power_w=float(cfg.heater_power_w),
        cooling_power_w=float(cfg.cooling_power_w),
        heater_on_below_c=float(cfg.heater_on_below_c),
        heater_off_above_c=float(cfg.heater_off_above_c),
        cooling_on_above_c=float(cfg.cooling_on_above_c),
        cooling_off_below_c=float(cfg.cooling_off_below_c),
    )
    node = ThermalNodeScheduledSysModel(node_cfg)
    node.heatInMsg.subscribeTo(heat_input.heatOutMsg)

    sim.AddModelToTask(task_name, heat_input)
    sim.AddModelToTask(task_name, node)

    period = macros.sec2nano(float(cfg.step_s))
    thermal_rec = node.thermalStatusOutMsg.recorder(period)
    heater_rec = node.heaterStatusOutMsg.recorder(period)
    cooling_rec = node.coolingStatusOutMsg.recorder(period)
    for rec in (thermal_rec, heater_rec, cooling_rec):
        sim.AddModelToTask(task_name, rec)

    return ThermalBasiliskSimContext(
        subsystem="thermal",
        config=cfg,
        simulation=sim,
        process=process,
        task=task,
        task_name=task_name,
        modules={"heat_input": heat_input, "thermal_node": node},
        recorders={"thermal": thermal_rec, "heater": heater_rec, "cooling": cooling_rec},
        message_handles={
            "heat_input": heat_input.heatOutMsg,
            "thermal_status": node.thermalStatusOutMsg,
            "heater_status": node.heaterStatusOutMsg,
            "cooling_status": node.coolingStatusOutMsg,
        },
        base_parameters={
            "period_ns": period,
            "thermal_node_config": build_nominal_thermal_node_config(),
            "heater_config": build_nominal_heater_config(max_power_w=float(cfg.heater_power_w)),
            "radiator_config": build_nominal_radiator_config(),
        },
        component_sources={
            "heater": "components.heater.builder.build_nominal_heater_config",
            "radiator": "components.radiator.builder.build_nominal_radiator_config",
            "thermal_node": "components.thermal_node.basilisk_scheduled.ThermalNodeScheduledSysModel",
        },
    )



def build_eclipse_shadow_factor_bridge(eclipse_msg: Any) -> Any:
    """Create and wire the thermal-owned EclipseMsg-to-shadow bridge."""

    from components.thermal_node.basilisk_scheduled import EclipseShadowFactorConverter

    bridge = EclipseShadowFactorConverter()
    bridge.eclipseInMsg.subscribeTo(eclipse_msg)
    return bridge


def build_mode_thermal_input_bridge(mode_power_w_by_node: dict[str, Any], *, current_mode: str) -> Any:
    """Create the thermal-owned operating-mode heat input bridge."""

    from components.thermal_node.basilisk_scheduled import ModeThermalInput

    bridge = ModeThermalInput()
    bridge.set_mode_power_map(mode_power_w_by_node)
    bridge.set_current_mode(current_mode)
    return bridge


def build_heater_power_feedback_bridge(heater_inputs: list[tuple[Any, float]]) -> Any:
    """Create the thermal-owned heater-to-EPS power feedback bridge."""

    from components.thermal_node.basilisk_scheduled import HeaterPowerFeedback

    bridge = HeaterPowerFeedback()
    for heater_msg, power_w in heater_inputs:
        bridge.add_heater_input(heater_msg, float(power_w))
    return bridge
