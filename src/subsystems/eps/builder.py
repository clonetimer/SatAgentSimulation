"""EPS subsystem builder entry points."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Literal, Optional

from components.battery import build_nominal_battery_config
from components.pdu.builder import build_nominal_pdu_config
from components.power_sink.builder import build_nominal_power_sink_config, demand_w
from components.solar_panel.builder import build_nominal_solar_panel_config
from components.fault_spec import FaultSpec
from components.battery.faults import BatteryFaultType
from components.solar_panel.faults import SolarPanelFaultType

from .schemas import EPSBasiliskConfig, EpsConfig, EpsStepInput
from .degradation import EPSDegradation, apply_eps_degradation
from .faults import EPSFaultType

@dataclass(frozen=True)
class EpsBasiliskConfig:
    """Minimal Basilisk EPS bridge configuration.

    ``solar_power_w`` is modeled as a deterministic positive power node in the
    weak-supportData smoke case.  This avoids SPICE, eclipse, spacecraft state,
    and sun-vector dependencies while still testing Basilisk power-node wiring
    and simpleBattery integration semantics.
    """

    battery_capacity_wh: float = 100.0
    initial_soc: float = 0.5
    solar_power_w: float = 60.0
    loads_w: Mapping[str, float] = field(default_factory=lambda: {"load": 10.0})
    dt_s: float = 60.0
    steps: int = 10


@dataclass(frozen=True)
class EpsBasiliskModuleGraph:
    """Container for created Basilisk EPS objects.

    The object keeps raw Basilisk module instances typed as ``Any`` so this file
    can be imported without Basilisk installed.
    """

    battery: Any
    solar_source: Any
    load_sinks: Mapping[str, Any]
    simple_solar_panel: Any | None = None
    requests: Mapping[str, Any] = field(default_factory=dict)
    pdu: Any | None = None
    status_messages: Mapping[str, Any] = field(default_factory=dict)
    cfg: EpsBasiliskConfig | None = None


@dataclass(frozen=True)
class EpsBasiliskSmokeResult:
    """Result summary for optional Basilisk bridge smoke runs."""

    basilisk_available: bool
    ran_simulation: bool
    initial_storage_j: float
    final_storage_j: float
    expected_net_power_w: float
    note: str = ""


def basilisk_available() -> bool:
    """Return True if Basilisk imports in the current environment."""

    try:
        import Basilisk  # noqa: F401
        return True
    except Exception:
        return False


def require_basilisk() -> None:
    """Raise a clear error if Basilisk is unavailable."""

    if not basilisk_available():
        raise RuntimeError("Basilisk is not available. Install bsk/Basilisk to run EPS Basilisk bridge tests.")


def eps_config_to_basilisk_config(cfg: EpsConfig, mode: str = "nominal", shadow_factor: float = 1.0) -> EpsBasiliskConfig:
    """Build a minimal Basilisk bridge config from the Python EPS config.

    This conversion intentionally preserves only Basilisk simplePower-compatible
    quantities: battery capacity/initial storage, deterministic solar power, and
    simple load sink powers.  PDU and low-SOC strategy remain in the Python EPS
    layer.
    """

    solar_power_w = max(0.0, float(cfg.solar_panel.max_power_w) * float(cfg.solar_panel.efficiency) * max(0.0, min(1.0, float(shadow_factor))))
    loads_w = {name: max(0.0, float(demand_w(load_cfg, mode=mode, enabled=True))) for name, load_cfg in cfg.loads.items()}
    return EpsBasiliskConfig(
        battery_capacity_wh=float(cfg.battery.capacity_wh),
        initial_soc=float(cfg.battery.initial_soc),
        solar_power_w=solar_power_w,
        loads_w=loads_w,
    )


def build_basilisk_eps_graph(cfg: EpsBasiliskConfig) -> EpsBasiliskModuleGraph:
    """Create a Basilisk simplePower module graph without running it.

    The graph uses component-level factories from ``components/*/builder.py``:

    * ``build_simple_battery`` for storage integration;
    * ``build_simple_power_sink`` with positive ``nodePowerOut`` as a
      deterministic solar power source for weak-supportData tests;
    * one ``build_simple_power_sink`` per load with negative power;
    * ``build_simple_solar_panel`` when available (not wired in smoke tests).
    """

    require_basilisk()
    from components.battery.builder import build_simple_battery
    from components.power_sink.builder import build_simple_power_sink
    from components.solar_panel.builder import build_simple_solar_panel

    battery = build_simple_battery(
        "EPS_Battery",
        float(cfg.battery_capacity_wh),
        float(cfg.initial_soc),
    )

    solar_source = build_simple_power_sink(
        "EPS_DeterministicSolarPowerSource",
        max(0.0, float(cfg.solar_power_w)),
    )
    battery.addPowerNodeToModel(solar_source.nodePowerOutMsg)

    load_sinks: dict[str, Any] = {}
    for name, power_w in cfg.loads_w.items():
        sink = build_simple_power_sink(
            f"EPS_Load_{name}",
            -max(0.0, float(power_w)),
        )
        battery.addPowerNodeToModel(sink.nodePowerOutMsg)
        load_sinks[str(name)] = sink

    simple_solar_panel_obj: Any | None = None
    try:
        simple_solar_panel_obj = build_simple_solar_panel(
            model_tag="EPS_SimpleSolarPanel_Unwired",
            panel_normal_b=(1.0, 0.0, 0.0),
            max_power_w=1.0,
            efficiency=1.0,
        )
    except Exception:
        simple_solar_panel_obj = None

    return EpsBasiliskModuleGraph(
        battery=battery,
        solar_source=solar_source,
        load_sinks=load_sinks,
        simple_solar_panel=simple_solar_panel_obj,
        cfg=cfg,
    )


def build_environment_coupled_basilisk_eps_graph(
    cfg: EpsBasiliskConfig,
    *,
    sun_msg: Any,
    spacecraft_state_msg: Any,
    eclipse_msg: Any | None,
    panel_normal_b: tuple[float, float, float] = (1.0, 0.0, 0.0),
    enable_load_shedding: bool = False,
    payload_min_soc: float = 0.55,
    comm_min_soc: float = 0.50,
    heater_min_soc: float = 0.30,
    adcs_min_soc: float = 0.20,
    recovery_soc: float = 0.65,
) -> EpsBasiliskModuleGraph:
    """Create an EPS graph whose solar generation follows the live environment.

    Unlike :func:`build_basilisk_eps_graph`, this integration graph does not use
    a positive ``SimplePowerSink`` as a constant solar source.  It wires the
    Basilisk ``SimpleSolarPanel`` directly to Sun, spacecraft-state/attitude and
    eclipse messages, so incidence angle and illumination change battery input
    power during the same simulation run.
    """

    require_basilisk()
    if sun_msg is None or spacecraft_state_msg is None:
        raise ValueError("sun_msg and spacecraft_state_msg are required for environment-coupled EPS")

    from components.battery.builder import build_simple_battery
    from components.power_sink.builder import build_simple_power_sink
    from components.solar_panel.builder import build_simple_solar_panel, get_power_output_msg

    battery = build_simple_battery(
        "EPS_Battery",
        float(cfg.battery_capacity_wh),
        float(cfg.initial_soc),
    )
    solar_panel = build_simple_solar_panel(
        model_tag="EPS_EnvironmentCoupledSolarPanel",
        panel_normal_b=panel_normal_b,
        max_power_w=max(0.0, float(cfg.solar_power_w)),
        efficiency=1.0,
        sun_in_msg=sun_msg,
        state_in_msg=spacecraft_state_msg,
        eclipse_in_msg=eclipse_msg,
    )
    battery.addPowerNodeToModel(get_power_output_msg(solar_panel))

    load_sinks: dict[str, Any] = {}
    for name, power_w in cfg.loads_w.items():
        sink = build_simple_power_sink(
            f"EPS_Load_{name}",
            -max(0.0, float(power_w)),
        )
        battery.addPowerNodeToModel(sink.nodePowerOutMsg)
        load_sinks[str(name)] = sink

    requests: dict[str, Any] = {}
    status_messages: dict[str, Any] = {}
    pdu = None
    if enable_load_shedding:
        from components.pdu.builder import ConstantDeviceRequest, PduLoadSheddingConfig, PduLoadSheddingSysModel

        for name in ("payload", "adcs", "comm", "heater"):
            requests[name] = ConstantDeviceRequest(f"wholeSpacecraft{name.title()}PowerRequest", True)
        pdu = PduLoadSheddingSysModel(PduLoadSheddingConfig(
            payload_min_soc=float(payload_min_soc),
            comm_min_soc=float(comm_min_soc),
            heater_min_soc=float(heater_min_soc),
            adcs_min_soc=float(adcs_min_soc),
            recovery_soc=float(recovery_soc),
            fallback_initial_soc=float(cfg.initial_soc),
        ))
        pdu.batteryStatusInMsg.subscribeTo(battery.batPowerOutMsg)
        pdu.payloadRequestInMsg.subscribeTo(requests["payload"].deviceCmdOutMsg)
        pdu.adcsRequestInMsg.subscribeTo(requests["adcs"].deviceCmdOutMsg)
        pdu.commRequestInMsg.subscribeTo(requests["comm"].deviceCmdOutMsg)
        pdu.heaterRequestInMsg.subscribeTo(requests["heater"].deviceCmdOutMsg)
        status_messages = {
            "payload": pdu.payloadStatusOutMsg,
            "adcs": pdu.adcsStatusOutMsg,
            "comm": pdu.commStatusOutMsg,
            "heater": pdu.heaterStatusOutMsg,
        }
        for name, status_msg in status_messages.items():
            sink = load_sinks.get(name)
            if sink is not None and hasattr(sink, "nodeStatusInMsg"):
                sink.nodeStatusInMsg.subscribeTo(status_msg)

    return EpsBasiliskModuleGraph(
        battery=battery,
        solar_source=solar_panel,
        load_sinks=load_sinks,
        simple_solar_panel=solar_panel,
        requests=requests,
        pdu=pdu,
        status_messages=status_messages,
        cfg=cfg,
    )


EpsBackend = Literal["basilisk"]


def build_nominal_eps_config(degradation: Optional[EPSDegradation] = None) -> EpsConfig:
    """Build a deterministic EPS config for subsystem tests and demos.
    
    Creates a comprehensive EPS configuration with:
    - 100Wh battery with 50% initial SOC
    - 120W solar panel
    - PDU with 70W bus limit and load shedding
    - Multiple power sinks: OBC, thermal, ADCS, comm, payload, thruster
    
    Args:
        degradation: Optional EPS degradation parameters
    
    Returns:
        EpsConfig: Nominal EPS configuration
    """
    cfg = EpsConfig(
        battery=build_nominal_battery_config(capacity_wh=100.0, initial_soc=0.5, charge_efficiency=1.0, discharge_efficiency=1.0, min_soc=0.2, max_soc=1.0),
        solar_panel=build_nominal_solar_panel_config(max_power_w=120.0, efficiency=1.0, max_slew_rate_rad_s=1.0),
        pdu=build_nominal_pdu_config(bus_max_w=70.0, shed_order=("payload", "comm", "thruster")),
        loads={
            "obc": build_nominal_power_sink_config(name="obc", base_w=10.0),
            "thermal": build_nominal_power_sink_config(name="thermal", base_w=5.0),
            "adcs": build_nominal_power_sink_config(name="adcs", base_w=10.0, mode_power_w={"safePoint": 5.0, "payload": 10.0}),
            "comm": build_nominal_power_sink_config(name="comm", base_w=0.0, mode_power_w={"downlink": 30.0, "payload": 5.0}),
            "payload": build_nominal_power_sink_config(name="payload", base_w=0.0, mode_power_w={"payload": 55.0}),
            "thruster": build_nominal_power_sink_config(name="thruster", base_w=0.0, mode_power_w={"reboost": 40.0}),
        },
        low_soc_threshold=0.3,
        low_soc_shed_loads=("payload", "comm", "thruster"),
    )

    if degradation is not None:
        cfg = apply_eps_degradation(cfg, degradation)

    return cfg


def apply_eps_config_faults(cfg: EpsConfig, fault_specs: list[FaultSpec]) -> EpsConfig:
    """Apply faults to EPS configuration.
    
    Supported fault types:
        - BatteryFaultType.SuddenCapacityLoss: Reduce battery capacity
        - BatteryFaultType.OpenCircuit: Disable battery charging/discharging
        - SolarPanelFaultType.Failure: Reduce solar panel power and efficiency
    
    Args:
        cfg: EPS configuration
        fault_specs: List of fault specifications
    
    Returns:
        EpsConfig: Modified configuration with faults applied
    """
    from dataclasses import replace

    for fault in fault_specs:
        if isinstance(fault.fault_type, BatteryFaultType):
            if fault.fault_type == BatteryFaultType.SuddenCapacityLoss:
                degraded_capacity = cfg.battery.capacity_wh * (1.0 - fault.magnitude)
                cfg = replace(cfg, battery=replace(cfg.battery, capacity_wh=degraded_capacity))
            elif fault.fault_type == BatteryFaultType.OpenCircuit:
                cfg = replace(cfg, battery=replace(cfg.battery, charge_efficiency=0.0, discharge_efficiency=0.0))
        elif isinstance(fault.fault_type, SolarPanelFaultType):
            if fault.fault_type == SolarPanelFaultType.Failure:
                degraded_power = cfg.solar_panel.max_power_w * (1.0 - fault.magnitude)
                degraded_eff = cfg.solar_panel.efficiency * (1.0 - fault.magnitude)
                cfg = replace(cfg, solar_panel=replace(cfg.solar_panel, max_power_w=degraded_power, efficiency=degraded_eff))

    return cfg


def build_orbit_day_night_profile(dt_s: float = 60.0) -> list[EpsStepInput]:
    """Build a day/night cycle EPS demo profile.
    
    Creates a power profile simulating:
    - 10 min daylight in payload mode
    - 10 min eclipse in payload mode
    - 5 min daylight in downlink mode
    - 5 min eclipse in safe mode
    
    Note: This is not an orbital propagator; it is a subsystem power-profile harness.
    
    Args:
        dt_s: Time step in seconds for each profile entry
    
    Returns:
        list[EpsStepInput]: Sequence of EPS step inputs
    """
    steps: list[EpsStepInput] = []
    steps.extend(EpsStepInput(dt_s=dt_s, mode="payload", shadow_factor=1.0, battery_temp_c=25.0) for _ in range(10))
    steps.extend(EpsStepInput(dt_s=dt_s, mode="payload", shadow_factor=0.0, battery_temp_c=20.0) for _ in range(10))
    steps.extend(EpsStepInput(dt_s=dt_s, mode="downlink", shadow_factor=1.0, battery_temp_c=28.0) for _ in range(5))
    steps.extend(EpsStepInput(dt_s=dt_s, mode="safePoint", shadow_factor=0.0, battery_temp_c=18.0) for _ in range(5))
    return steps


def build_eps_context(cfg: EpsConfig | None = None, backend: EpsBackend = "basilisk") -> tuple[EpsConfig, EpsBasiliskModuleGraph]:
    """Build an EPS Basilisk context.
    
    Args:
        cfg: EPS configuration. If None, uses nominal config.
        backend: Backend to use. Only "basilisk" is supported.
    
    Returns:
        tuple: (EPS config, Basilisk module graph)
    
    Raises:
        ValueError: If backend is not "basilisk"
    """
    cfg = cfg or build_nominal_eps_config()
    if backend != "basilisk":
        raise ValueError("EPS Python backend has been removed; use backend='basilisk'")
    bsk_cfg = eps_config_to_basilisk_config(cfg)
    return cfg, build_basilisk_eps_graph(bsk_cfg)


def build_eps_basilisk_context(cfg: EpsConfig | None = None, bsk_cfg: EpsBasiliskConfig | None = None) -> EpsBasiliskModuleGraph:
    """Build Basilisk EPS graph from config.
    
    Args:
        cfg: EPS configuration. Used if bsk_cfg is None.
        bsk_cfg: Optional Basilisk EPS configuration. If None, derived from cfg.
    
    Returns:
        EpsBasiliskModuleGraph: Basilisk module graph for EPS
    """
    if bsk_cfg is None:
        cfg = cfg or build_nominal_eps_config()
        bsk_cfg = eps_config_to_basilisk_config(cfg)
    return build_basilisk_eps_graph(bsk_cfg)


def build_eps_basilisk_graph_for_power_profile(
    *,
    battery_capacity_wh: float,
    initial_soc: float,
    solar_power_w: float,
    loads_w: Mapping[str, float],
) -> EpsBasiliskModuleGraph:
    """Build Basilisk EPS graph from power profile parameters.
    
    Args:
        battery_capacity_wh: Battery capacity in watt-hours
        initial_soc: Initial state of charge (0-1)
        solar_power_w: Solar panel output power in watts
        loads_w: Dictionary mapping load names to power requirements (W)
    
    Returns:
        EpsBasiliskModuleGraph: Basilisk module graph for EPS
    """
    cfg = EpsBasiliskConfig(
        battery_capacity_wh=float(battery_capacity_wh),
        initial_soc=float(initial_soc),
        solar_power_w=float(solar_power_w),
        loads_w={str(k): float(v) for k, v in loads_w.items()},
    )
    return build_basilisk_eps_graph(cfg)


def get_eps_solar_power_output_msg(graph: EpsBasiliskModuleGraph) -> Any:
    """Return the EPS-owned solar generation output message.

    The whole-spacecraft assembly consumes this subsystem boundary and does not
    import the solar-panel component layer directly.
    """

    from components.solar_panel.builder import get_power_output_msg

    return get_power_output_msg(graph.solar_source)


def attach_eps_basilisk_graph_to_task(sim: Any, task_name: str, graph: EpsBasiliskModuleGraph) -> None:
    """Attach EPS Basilisk graph to a simulation task in dependency order."""

    for request in graph.requests.values():
        sim.AddModelToTask(task_name, request)
    if graph.pdu is not None:
        sim.AddModelToTask(task_name, graph.pdu)
    sim.AddModelToTask(task_name, graph.solar_source)
    for sink in graph.load_sinks.values():
        sim.AddModelToTask(task_name, sink)
    sim.AddModelToTask(task_name, graph.battery)



@dataclass(frozen=True)
class EPSBasiliskSimContext:
    """Built EPS Basilisk simulation graph; execution is left to runner.py."""

    subsystem: str
    config: "EPSBasiliskConfig"
    simulation: Any
    process: Any
    task: Any
    task_name: str
    modules: dict[str, Any]
    recorders: dict[str, Any]
    message_handles: dict[str, Any]
    base_parameters: dict[str, Any]
    component_sources: dict[str, str]


def _require_eps_basilisk() -> None:
    try:
        from Basilisk.utilities import SimulationBaseClass, macros  # noqa: F401
        from Basilisk.simulation import simpleBattery, simplePowerSink  # noqa: F401
        from Basilisk.architecture import messaging, sysModel  # noqa: F401
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Basilisk EPS/PDU modules are unavailable: {exc}") from exc


def build_eps_basilisk_sim(config: "EPSBasiliskConfig | None" = None) -> EPSBasiliskSimContext:
    """Build the EPS Basilisk simulation context without executing it."""

    _require_eps_basilisk()
    cfg = config or EPSBasiliskConfig()
    if cfg.step_s <= 0 or cfg.duration_s <= 0:
        raise ValueError("duration_s and step_s must be positive")

    from Basilisk.utilities import SimulationBaseClass, macros
    from Basilisk.architecture import messaging
    from components.battery.builder import (
        build_simple_battery,
        attach_power_nodes_to_battery,
        write_battery_capacity_fault_message,
        attach_battery_capacity_fault_message,
    )
    from components.power_sink.builder import build_simple_power_sink
    from components.pdu.builder import ConstantDeviceRequest, PduLoadSheddingConfig, PduLoadSheddingSysModel
    from components.solar_panel.builder import build_simple_solar_panel, get_power_output_msg, basilisk_simple_solar_panel_available
    from components.antenna.builder import build_antenna_power_node
    from components.reaction_wheel.builder import build_reaction_wheel_power_node

    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("epsBasiliskManagedProcess")
    task_name = "epsBasiliskManagedTask"
    task = sim.CreateNewTask(task_name, macros.sec2nano(float(cfg.step_s)))
    process.addTask(task)

    battery = build_simple_battery("epsSimpleBattery", cfg.battery_capacity_wh, cfg.initial_soc)
    battery_fault_msg = write_battery_capacity_fault_message(cfg.battery_fault_capacity_ratio)
    attach_battery_capacity_fault_message(battery, battery_fault_msg)

    use_native_solar_panel = cfg.use_simple_solar_panel and basilisk_simple_solar_panel_available()
    solar_power_node = None
    solar_power_out_msg = None
    sun_in_msg = None
    sc_state_in_msg = None
    eclipse_in_msg = None
    solar_status_msg = None

    if use_native_solar_panel:
        sun_payload = messaging.SpicePlanetStateMsgPayload()
        sun_payload.PositionVector = [1.5e11, 0.0, 0.0]
        sun_in_msg = messaging.SpicePlanetStateMsg().write(sun_payload)

        sc_state_payload = messaging.SCStatesMsgPayload()
        sc_state_payload.sigma_BN = [0.0, 0.0, 0.0]
        sc_state_payload.r_BN_N = [0.0, 0.0, 0.0]
        sc_state_in_msg = messaging.SCStatesMsg().write(sc_state_payload)

        eclipse_payload = messaging.EclipseMsgPayload()
        eclipse_payload.illuminationFactor = 1.0
        eclipse_in_msg = messaging.EclipseMsg().write(eclipse_payload)

        solar_status_payload = messaging.DeviceStatusMsgPayload()
        solar_status_payload.deviceStatus = 1 if bool(cfg.solar_panel_enabled) else 0
        solar_status_msg = messaging.DeviceStatusMsg().write(solar_status_payload)

        solar_panel = build_simple_solar_panel(
            "epsSimpleSolarPanel",
            cfg.solar_normal_b,
            cfg.solar_power_w,
            cfg.solar_efficiency,
            panel_area_m2=cfg.solar_panel_area_m2,
            sun_in_msg=sun_in_msg,
            state_in_msg=sc_state_in_msg,
            eclipse_in_msg=eclipse_in_msg,
            node_status_msg=solar_status_msg,
        )
        solar_power_node = solar_panel
        solar_power_out_msg = get_power_output_msg(solar_panel)
        sim.AddModelToTask(task_name, solar_panel)
    else:
        fallback_solar_power_w = abs(float(cfg.solar_power_w)) if bool(cfg.solar_panel_enabled) else 0.0
        solar_power_node = build_simple_power_sink("epsSolarPowerSource", fallback_solar_power_w)
        solar_power_out_msg = solar_power_node.nodePowerOutMsg

    bus = build_simple_power_sink("epsBusAlwaysOnLoad", -abs(float(cfg.bus_power_w)))
    payload = build_simple_power_sink("epsPayloadLoad", -abs(float(cfg.payload_power_w)))
    adcs = build_simple_power_sink("epsAdcsLoad", -abs(float(cfg.adcs_power_w)))
    comm = build_simple_power_sink("epsCommLoad", -abs(float(cfg.comm_power_w)))
    heater = build_simple_power_sink("epsHeaterLoad", -abs(float(cfg.heater_power_w)))
    rw_power_node, rw_power_state_msg = build_reaction_wheel_power_node(
        "epsReactionWheelPower",
        base_power_w=abs(float(cfg.rw_power_w)),
        wheel_speed_rad_s=100.0,
        wheel_torque_nm=0.0,
        p_max_w=-1.0,
    )
    antenna_power_node = build_antenna_power_node("epsAntennaPower", base_power_w=abs(float(cfg.antenna_power_w)))

    attach_power_nodes_to_battery(battery, [
        solar_power_out_msg,
        bus.nodePowerOutMsg,
        payload.nodePowerOutMsg,
        adcs.nodePowerOutMsg,
        comm.nodePowerOutMsg,
        heater.nodePowerOutMsg,
        rw_power_node.nodePowerOutMsg,
        antenna_power_node.nodePowerOutMsg,
    ])

    payload_req = ConstantDeviceRequest("payloadLoadRequest", cfg.payload_requested)
    adcs_req = ConstantDeviceRequest("adcsLoadRequest", cfg.adcs_requested)
    comm_req = ConstantDeviceRequest("commLoadRequest", cfg.comm_requested)
    heater_req = ConstantDeviceRequest("heaterLoadRequest", cfg.heater_requested)
    pdu = PduLoadSheddingSysModel(PduLoadSheddingConfig(
        payload_min_soc=cfg.payload_min_soc,
        comm_min_soc=cfg.comm_min_soc,
        heater_min_soc=cfg.heater_min_soc,
        adcs_min_soc=cfg.adcs_min_soc,
        recovery_soc=cfg.recovery_soc,
        fallback_initial_soc=cfg.initial_soc,
    ))
    # Runtime-writable PDU health fields sourced from components.pdu degradation/fault models.
    pdu.path_resistance_ohm = 0.05
    pdu.bus_voltage_v = 28.0
    pdu.channel_current_limit_a = 4.0
    pdu.channel_enabled = 1.0
    pdu.current_limit = 4.0
    pdu.voltage_regulation = 1.0
    pdu.batteryStatusInMsg.subscribeTo(battery.batPowerOutMsg)
    pdu.payloadRequestInMsg.subscribeTo(payload_req.deviceCmdOutMsg)
    pdu.adcsRequestInMsg.subscribeTo(adcs_req.deviceCmdOutMsg)
    pdu.commRequestInMsg.subscribeTo(comm_req.deviceCmdOutMsg)
    pdu.heaterRequestInMsg.subscribeTo(heater_req.deviceCmdOutMsg)

    payload.nodeStatusInMsg.subscribeTo(pdu.payloadStatusOutMsg)
    adcs.nodeStatusInMsg.subscribeTo(pdu.adcsStatusOutMsg)
    comm.nodeStatusInMsg.subscribeTo(pdu.commStatusOutMsg)
    heater.nodeStatusInMsg.subscribeTo(pdu.heaterStatusOutMsg)

    for model in [payload_req, adcs_req, comm_req, heater_req, pdu, solar_power_node, bus, payload, adcs, comm, heater, rw_power_node, antenna_power_node, battery]:
        # Native simpleSolarPanel is already added above; the simplePowerSink
        # fallback solar source must still be scheduled or its output message
        # remains unwritten and the battery sees no generation.
        if use_native_solar_panel and model is solar_power_node:
            continue
        sim.AddModelToTask(task_name, model)

    period = macros.sec2nano(float(cfg.step_s))
    battery_rec = battery.batPowerOutMsg.recorder(period)
    payload_rec = payload.nodePowerOutMsg.recorder(period)
    adcs_rec = adcs.nodePowerOutMsg.recorder(period)
    comm_rec = comm.nodePowerOutMsg.recorder(period)
    heater_rec = heater.nodePowerOutMsg.recorder(period)
    rw_rec = rw_power_node.nodePowerOutMsg.recorder(period)
    antenna_rec = antenna_power_node.nodePowerOutMsg.recorder(period)
    solar_rec = solar_power_out_msg.recorder(period)
    bus_rec = bus.nodePowerOutMsg.recorder(period)
    payload_status_rec = pdu.payloadStatusOutMsg.recorder(period)
    adcs_status_rec = pdu.adcsStatusOutMsg.recorder(period)
    comm_status_rec = pdu.commStatusOutMsg.recorder(period)
    heater_status_rec = pdu.heaterStatusOutMsg.recorder(period)
    for rec in [battery_rec, payload_rec, adcs_rec, comm_rec, heater_rec, rw_rec, antenna_rec, solar_rec, bus_rec, payload_status_rec, adcs_status_rec, comm_status_rec, heater_status_rec]:
        sim.AddModelToTask(task_name, rec)

    return EPSBasiliskSimContext(
        subsystem="eps",
        config=cfg,
        simulation=sim,
        process=process,
        task=task,
        task_name=task_name,
        modules={
            "battery": battery,
            "solar_power_node": solar_power_node,
            "bus": bus,
            "payload": payload,
            "adcs": adcs,
            "comm": comm,
            "heater": heater,
            "rw_power": rw_power_node,
            "antenna_power": antenna_power_node,
            "payload_request": payload_req,
            "adcs_request": adcs_req,
            "comm_request": comm_req,
            "heater_request": heater_req,
            "pdu": pdu,
        },
        recorders={
            "battery": battery_rec,
            "payload": payload_rec,
            "adcs": adcs_rec,
            "comm": comm_rec,
            "heater": heater_rec,
            "rw_power": rw_rec,
            "antenna_power": antenna_rec,
            "solar": solar_rec,
            "bus": bus_rec,
            "payload_status": payload_status_rec,
            "adcs_status": adcs_status_rec,
            "comm_status": comm_status_rec,
            "heater_status": heater_status_rec,
        },
        message_handles={
            "battery_status": battery.batPowerOutMsg,
            "solar_power": solar_power_out_msg,
            "solar_sun": sun_in_msg,
            "solar_spacecraft_state": sc_state_in_msg,
            "solar_eclipse": eclipse_in_msg,
            "solar_status": solar_status_msg,
            "battery_fault": battery_fault_msg,
            "rw_power": rw_power_node.nodePowerOutMsg,
            "antenna_power": antenna_power_node.nodePowerOutMsg,
            "payload_status": pdu.payloadStatusOutMsg,
            "adcs_status": pdu.adcsStatusOutMsg,
            "comm_status": pdu.commStatusOutMsg,
            "heater_status": pdu.heaterStatusOutMsg,
        },
        base_parameters={
            "period_ns": period,
            "use_native_solar_panel": use_native_solar_panel,
            "solar_panel_enabled": bool(cfg.solar_panel_enabled),
            "battery_fault_capacity_ratio": cfg.battery_fault_capacity_ratio,
            "rw_power_w": float(cfg.rw_power_w),
            "antenna_power_w": float(cfg.antenna_power_w),
            "rw_power_state_msg": rw_power_state_msg,
        },
        component_sources={
            "battery": "components.battery.builder.build_simple_battery",
            "solar_panel": "components.solar_panel.builder.build_simple_solar_panel",
            "pdu": "components.pdu.builder.PduLoadSheddingSysModel",
            "power_sink": "components.power_sink.builder.build_simple_power_sink",
            "reaction_wheel_power": "components.reaction_wheel.builder.build_reaction_wheel_power_node",
            "antenna_power": "components.antenna.builder.build_antenna_power_node",
        },
    )


@dataclass(frozen=True)
class EpsLoadSheddingAssemblyGraph:
    """EPS graph with native power nodes and subsystem-owned PDU policy."""

    battery: Any
    solar_source: Any
    load_sinks: Mapping[str, Any]
    requests: Mapping[str, Any]
    pdu: Any


def build_eps_load_shedding_assembly_graph(
    *,
    battery_capacity_wh: float,
    initial_soc: float,
    solar_power_w: float,
    bus_power_w: float,
    payload_power_w: float,
    adcs_power_w: float,
    comm_power_w: float,
    heater_power_w: float,
) -> EpsLoadSheddingAssemblyGraph:
    """Build the native EPS/PDU chain used by integrated assemblies."""

    from components.battery.builder import attach_power_nodes_to_battery, build_simple_battery
    from components.power_sink.builder import build_simple_power_sink
    from components.pdu.builder import ConstantDeviceRequest, PduLoadSheddingConfig, PduLoadSheddingSysModel

    battery = build_simple_battery("unifiedSimpleBattery", battery_capacity_wh, initial_soc)
    solar = build_simple_power_sink("unifiedSolarSource", abs(solar_power_w))
    loads = {
        "bus": build_simple_power_sink("unifiedBusLoad", -abs(bus_power_w)),
        "payload": build_simple_power_sink("unifiedPayloadLoad", -abs(payload_power_w)),
        "adcs": build_simple_power_sink("unifiedAdcsLoad", -abs(adcs_power_w)),
        "comm": build_simple_power_sink("unifiedCommLoad", -abs(comm_power_w)),
        "heater": build_simple_power_sink("unifiedHeaterLoad", -abs(heater_power_w)),
    }
    attach_power_nodes_to_battery(
        battery,
        [solar.nodePowerOutMsg, *(sink.nodePowerOutMsg for sink in loads.values())],
    )
    requests = {
        name: ConstantDeviceRequest(f"unified{name.title()}Request", True)
        for name in ("payload", "adcs", "comm", "heater")
    }
    pdu = PduLoadSheddingSysModel(PduLoadSheddingConfig(fallback_initial_soc=initial_soc))
    pdu.batteryStatusInMsg.subscribeTo(battery.batPowerOutMsg)
    pdu.payloadRequestInMsg.subscribeTo(requests["payload"].deviceCmdOutMsg)
    pdu.adcsRequestInMsg.subscribeTo(requests["adcs"].deviceCmdOutMsg)
    pdu.commRequestInMsg.subscribeTo(requests["comm"].deviceCmdOutMsg)
    pdu.heaterRequestInMsg.subscribeTo(requests["heater"].deviceCmdOutMsg)
    loads["payload"].nodeStatusInMsg.subscribeTo(pdu.payloadStatusOutMsg)
    loads["adcs"].nodeStatusInMsg.subscribeTo(pdu.adcsStatusOutMsg)
    loads["comm"].nodeStatusInMsg.subscribeTo(pdu.commStatusOutMsg)
    loads["heater"].nodeStatusInMsg.subscribeTo(pdu.heaterStatusOutMsg)
    return EpsLoadSheddingAssemblyGraph(
        battery=battery,
        solar_source=solar,
        load_sinks=loads,
        requests=requests,
        pdu=pdu,
    )


def attach_eps_load_shedding_graph_to_task(sim: Any, task_name: str, graph: EpsLoadSheddingAssemblyGraph) -> None:
    """Attach a complete EPS/PDU graph to a caller-owned Basilisk task."""

    for request in graph.requests.values():
        sim.AddModelToTask(task_name, request)
    sim.AddModelToTask(task_name, graph.pdu)
    sim.AddModelToTask(task_name, graph.solar_source)
    for sink in graph.load_sinks.values():
        sim.AddModelToTask(task_name, sink)
    sim.AddModelToTask(task_name, graph.battery)
