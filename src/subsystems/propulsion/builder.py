"""Propulsion subsystem builder entry points."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Optional, Sequence

from components.fault_spec import FaultSpec
from components.thruster.faults import ThrusterFaultType
from components.fuel_tank import FuelTankConfig
from components.thruster import ThrusterCommandConfig, ThrusterPhysicalConfig

from .degradation import PropulsionDegradation, apply_propulsion_degradation
from .model import initialize_propulsion_state
from .schemas import PropulsionBasiliskConfig, PropulsionConfig, PropulsionStepInput


@dataclass(frozen=True)
class PropulsionBasiliskAssemblyGraph:
    thruster_effector: Any
    fuel_tank: Any
    command_msg: Any
    thruster_specs: tuple[Any, ...]
    on_time_s: tuple[float, ...]


@dataclass(frozen=True)
class BasiliskThrusterSpec:
    index: int
    thrust_n: float
    isp_s: float
    direction_b: tuple[float, float, float]
    location_b_m: tuple[float, float, float]
    min_on_time_s: float


@dataclass(frozen=True)
class BasiliskPropulsionSummary:
    backend: str
    available: bool
    status: str
    num_thrusters: int
    on_time_roundtrip_s: tuple[float, ...]
    thruster_specs: tuple[BasiliskThrusterSpec, ...]
    tank_initial_mass_kg: float
    tank_capacity_kg: float
    messages: tuple[str, ...]


def basilisk_available() -> bool:
    try:
        import Basilisk  # noqa: F401

        return True
    except Exception:
        return False


def require_basilisk() -> None:
    if not basilisk_available():
        raise RuntimeError("Basilisk is not available. Install bsk before running propulsion Basilisk bridge tests.")


def _get_tuple(seq: Sequence[Any], idx: int, default: Any) -> Any:
    return seq[idx] if idx < len(seq) else default


def _vec3(value: Sequence[float], default: tuple[float, float, float]) -> tuple[float, float, float]:
    vals = [float(x) for x in list(value)[:3]]
    if len(vals) < 3:
        vals.extend(list(default[len(vals):]))
    return (vals[0], vals[1], vals[2])


def write_thruster_on_time_msg(on_time_s: Sequence[float]):
    require_basilisk()
    from Basilisk.architecture import messaging

    payload = messaging.THRArrayOnTimeCmdMsgPayload()
    values = list(payload.OnTimeRequest)
    for idx, value in enumerate(on_time_s):
        if idx < len(values):
            values[idx] = max(0.0, float(value))
    payload.OnTimeRequest = values
    return messaging.THRArrayOnTimeCmdMsg().write(payload)


def read_thruster_on_time_msg(msg, num_thrusters: int) -> tuple[float, ...]:
    payload = msg.read()
    return tuple(float(x) for x in list(payload.OnTimeRequest)[: int(num_thrusters)])


def build_basilisk_thruster_effector(cfg: PropulsionConfig):
    require_basilisk()
    from components.thruster.builder import build_thruster_dynamic_effector_bundle, build_thruster_specs_from_configs

    native_specs = build_thruster_specs_from_configs(cfg.thruster_command, cfg.thruster_physical)
    bundle = build_thruster_dynamic_effector_bundle("propulsionThrusterDynamicEffector", native_specs)
    specs: list[BasiliskThrusterSpec] = []
    for idx, spec in enumerate(native_specs):
        specs.append(BasiliskThrusterSpec(
            idx,
            float(spec["max_thrust_n"]),
            float(spec["isp_s"]),
            _vec3(spec["direction"], (1.0, 0.0, 0.0)),
            _vec3(spec["location"], (0.0, 0.0, 0.0)),
            float(spec["min_on_time_s"]),
        ))
    return bundle.effector, tuple(specs)


_fuel_tank_bundles: list = []


def build_basilisk_fuel_tank(cfg: PropulsionConfig):
    require_basilisk()
    from components.fuel_tank.builder import build_fuel_tank_bundle_from_config

    bundle = build_fuel_tank_bundle_from_config("propulsionFuelTank", cfg.fuel_tank)
    _fuel_tank_bundles.append(bundle)
    return bundle.tank


def build_propulsion_basilisk_assembly_graph(
    cfg: PropulsionConfig,
    *,
    on_time_s: Sequence[float] | None = None,
) -> PropulsionBasiliskAssemblyGraph:
    thruster_effector, thruster_specs = build_basilisk_thruster_effector(cfg)
    fuel_tank = build_basilisk_fuel_tank(cfg)
    nominal_on_time = list(on_time_s or [0.0] * int(cfg.thruster_command.num_thrusters))
    if on_time_s is None:
        for idx in cfg.thruster_command.active_ids:
            if 0 <= idx < len(nominal_on_time):
                nominal_on_time[idx] = max(float(cfg.thruster_command.nominal_on_time_s), float(cfg.thruster_command.min_pulse_s))
    command_msg = write_thruster_on_time_msg(nominal_on_time)
    thruster_effector.cmdsInMsg.subscribeTo(command_msg)
    return PropulsionBasiliskAssemblyGraph(
        thruster_effector=thruster_effector,
        fuel_tank=fuel_tank,
        command_msg=command_msg,
        thruster_specs=thruster_specs,
        on_time_s=tuple(float(x) for x in nominal_on_time),
    )


def attach_propulsion_basilisk_graph_to_spacecraft(
    spacecraft: Any,
    sim: Any,
    task_name: str,
    graph: PropulsionBasiliskAssemblyGraph,
) -> None:
    spacecraft.addDynamicEffector(graph.thruster_effector)
    graph.fuel_tank.addThrusterSet(graph.thruster_effector)
    spacecraft.addStateEffector(graph.fuel_tank)
    sim.AddModelToTask(task_name, graph.thruster_effector)
    sim.AddModelToTask(task_name, graph.fuel_tank)


def build_basilisk_propulsion_graph(cfg: PropulsionConfig) -> BasiliskPropulsionSummary | dict[str, object]:
    if not basilisk_available():
        return {"backend": "basilisk", "available": False, "reason": "Basilisk not installed"}
    graph = build_propulsion_basilisk_assembly_graph(cfg)
    return BasiliskPropulsionSummary(
        backend="basilisk",
        available=True,
        status="graph_constructed",
        num_thrusters=int(cfg.thruster_command.num_thrusters),
        on_time_roundtrip_s=read_thruster_on_time_msg(graph.command_msg, int(cfg.thruster_command.num_thrusters)),
        thruster_specs=graph.thruster_specs,
        tank_initial_mass_kg=float(cfg.fuel_tank.initial_mass_kg),
        tank_capacity_kg=float(cfg.fuel_tank.capacity_kg),
        messages=(
            type(graph.command_msg).__name__,
            type(graph.thruster_effector.cmdsInMsg).__name__,
            type(graph.fuel_tank.fuelTankOutMsg).__name__,
        ),
    )


def build_basilisk_propulsion_context(cfg: PropulsionConfig | None = None):
    """Build the Basilisk-side propulsion context without running the subsystem."""
    from .builder import build_nominal_propulsion_config

    cfg = cfg or build_nominal_propulsion_config()
    return cfg, summary_to_dict(build_basilisk_propulsion_graph(cfg))


def summary_to_dict(summary: BasiliskPropulsionSummary | dict[str, object]) -> dict[str, object]:
    if isinstance(summary, dict):
        return dict(summary)
    return {
        "backend": summary.backend,
        "available": summary.available,
        "status": summary.status,
        "num_thrusters": summary.num_thrusters,
        "on_time_roundtrip_s": list(summary.on_time_roundtrip_s),
        "thruster_specs": [spec.__dict__ for spec in summary.thruster_specs],
        "tank_initial_mass_kg": summary.tank_initial_mass_kg,
        "tank_capacity_kg": summary.tank_capacity_kg,
        "messages": list(summary.messages),
    }


def build_nominal_propulsion_config(degradation: Optional[PropulsionDegradation] = None) -> PropulsionConfig:
    """Build the default propulsion configuration."""
    cfg = PropulsionConfig(
        thruster_command=ThrusterCommandConfig(
            num_thrusters=2,
            active_ids=(0, 1),
            nominal_on_time_s=0.2,
            min_pulse_s=0.05,
        ),
        thruster_physical=ThrusterPhysicalConfig(
            thrust_n=(1.0, 1.0),
            isp_s=(200.0, 200.0),
            directions_b=((1.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
            lever_arms_b_m=((0.0, 0.5, 0.0), (0.0, -0.5, 0.0)),
        ),
        fuel_tank=FuelTankConfig(
            capacity_kg=2.0,
            initial_mass_kg=1.0,
            full_pressure_pa=2.5e6,
            dry_pressure_pa=1.5e5,
        ),
        min_soc_for_burn=0.3,
        require_eps_permission=True,
    )
    if degradation is not None:
        cfg = apply_propulsion_degradation(cfg, degradation)
    return cfg


def apply_propulsion_config_faults(cfg: PropulsionConfig, fault_specs: list[FaultSpec]) -> PropulsionConfig:
    """Apply propulsion-relevant faults to the subsystem config."""
    for fault in fault_specs:
        if not isinstance(fault.fault_type, ThrusterFaultType):
            continue
        if fault.fault_type == ThrusterFaultType.IgnitionFailure:
            thrust_list = list(cfg.thruster_physical.thrust_n)
            for idx in cfg.thruster_command.active_ids:
                if 0 <= idx < len(thrust_list):
                    thrust_list[idx] = 0.0
            cfg = replace(cfg, thruster_physical=replace(cfg.thruster_physical, thrust_n=tuple(thrust_list)))
        elif fault.fault_type == ThrusterFaultType.NozzleBlockage:
            thrust_list = list(cfg.thruster_physical.thrust_n)
            for idx in cfg.thruster_command.active_ids:
                if 0 <= idx < len(thrust_list):
                    thrust_list[idx] = thrust_list[idx] * (1.0 - fault.magnitude)
            cfg = replace(cfg, thruster_physical=replace(cfg.thruster_physical, thrust_n=tuple(thrust_list)))
    return cfg


def nominal_propulsion_steps() -> tuple[PropulsionStepInput, ...]:
    """Return the reference propulsion step sequence used by tests and demos."""
    return (
        PropulsionStepInput(dt_s=1.0, burn_requested=True, battery_soc=0.8, eps_allows_burn=True, mode="reboost"),
        PropulsionStepInput(dt_s=1.0, burn_requested=False, battery_soc=0.75, eps_allows_burn=True, mode="coast"),
        PropulsionStepInput(dt_s=1.0, burn_requested=True, battery_soc=0.2, eps_allows_burn=True, mode="reboost"),
        PropulsionStepInput(dt_s=1.0, burn_requested=True, battery_soc=0.8, eps_allows_burn=False, mode="reboost"),
        PropulsionStepInput(dt_s=1.0, burn_requested=True, battery_soc=0.7, eps_allows_burn=True, mode="reboost"),
    )


def build_reference_propulsion_context(cfg: PropulsionConfig | None = None):
    """Build the reference propulsion context used by tests and policy runners."""
    cfg = cfg or build_nominal_propulsion_config()
    return cfg, initialize_propulsion_state(cfg)


def build_propulsion_context(cfg: PropulsionConfig | None = None):
    """Compatibility wrapper for the reference propulsion context."""
    return build_reference_propulsion_context(cfg)



@dataclass(frozen=True)
class PropulsionBasiliskSimContext:
    """Built propulsion Basilisk simulation graph; execution is left to runner.py."""

    subsystem: str
    propulsion_config: PropulsionConfig
    config: "PropulsionBasiliskConfig"
    simulation: Any
    process: Any
    task: Any
    task_name: str
    modules: dict[str, Any]
    recorders: dict[str, Any]
    message_handles: dict[str, Any]
    base_parameters: dict[str, Any]
    component_sources: dict[str, str]


def _require_propulsion_basilisk() -> None:
    try:
        from Basilisk.utilities import SimulationBaseClass, macros, simIncludeThruster  # noqa: F401
        from Basilisk.simulation import spacecraft, thrusterDynamicEffector, fuelTank  # noqa: F401
        from Basilisk.architecture import messaging  # noqa: F401
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Basilisk propulsion modules are unavailable: {exc}") from exc


def _nominal_basilisk_on_times(cfg: PropulsionConfig) -> tuple[float, ...]:
    values = [0.0] * int(cfg.thruster_command.num_thrusters)
    for idx in cfg.thruster_command.active_ids:
        if 0 <= idx < len(values):
            values[idx] = max(float(cfg.thruster_command.nominal_on_time_s), float(cfg.thruster_command.min_pulse_s))
    return tuple(values)


try:
    from Basilisk.architecture import messaging as _bsk_messaging
    from Basilisk.architecture import sysModel as _bsk_sys_model
except Exception:  # pragma: no cover - guarded by _require_propulsion_basilisk at runtime
    _bsk_messaging = None
    _bsk_sys_model = None


_DelayedBurnBase = _bsk_sys_model.SysModel if _bsk_sys_model is not None else object


class _DelayedBurnController(_DelayedBurnBase):
    """Basilisk SysModel that publishes a delayed thruster command in-task."""

    def __init__(self, burn_start_ns, on_times, cmd_msg):
        if _bsk_sys_model is None or _bsk_messaging is None:
            raise RuntimeError("Basilisk is required for the delayed burn controller")
        super().__init__()
        self.burn_start_ns = int(burn_start_ns)
        self.on_times = tuple(float(value) for value in on_times)
        self.cmd_msg = cmd_msg
        self.burn_started = False
        self.ModelTag = "projectDelayedBurnController"

    def Reset(self, currentTime):  # noqa: N802
        self.burn_started = False

    def UpdateState(self, currentTime):  # noqa: N802
        if self.burn_started or int(currentTime) < self.burn_start_ns:
            return
        payload = _bsk_messaging.THRArrayOnTimeCmdMsgPayload()
        values = list(payload.OnTimeRequest)
        for idx, value in enumerate(self.on_times[: len(values)]):
            values[idx] = max(float(value), 0.0)
        payload.OnTimeRequest = values
        self.cmd_msg.write(payload, int(currentTime), self.moduleID)
        self.burn_started = True


def build_propulsion_basilisk_sim(
    propulsion_config: PropulsionConfig | None = None,
    native_config: "PropulsionBasiliskConfig | None" = None,
) -> PropulsionBasiliskSimContext:
    """Build the propulsion Basilisk simulation context without executing it."""

    _require_propulsion_basilisk()
    pcfg = propulsion_config or build_nominal_propulsion_config()
    ncfg = native_config or PropulsionBasiliskConfig()
    if ncfg.step_s <= 0 or ncfg.duration_s <= 0:
        raise ValueError("duration_s and step_s must be positive")

    from Basilisk.simulation import spacecraft
    from Basilisk.utilities import SimulationBaseClass, macros
    from components.fuel_tank.builder import attach_fuel_tank_to_spacecraft, build_fuel_tank_bundle_from_config
    from components.thruster.builder import (
        attach_thruster_to_spacecraft,
        build_thruster_dynamic_effector_bundle,
        build_thruster_specs_from_configs,
        write_thruster_on_time_message,
    )

    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("propulsionBasiliskProcess")
    task_name = "propulsionBasiliskTask"
    task = sim.CreateNewTask(task_name, macros.sec2nano(float(ncfg.step_s)))
    process.addTask(task)

    sc = spacecraft.Spacecraft()
    sc.ModelTag = "propulsionSpacecraft"
    sc.hub.mHub = float(ncfg.spacecraft_mass_kg)
    sc.hub.r_CN_NInit = [[0.0], [0.0], [0.0]]
    sc.hub.v_CN_NInit = [[0.0], [0.0], [0.0]]
    sc.hub.sigma_BNInit = [[0.0], [0.0], [0.0]]
    sc.hub.omega_BN_BInit = [[0.0], [0.0], [0.0]]

    thruster_specs = build_thruster_specs_from_configs(pcfg.thruster_command, pcfg.thruster_physical)

    thruster_bundle = build_thruster_dynamic_effector_bundle("propulsionThrusterDynamicEffector", thruster_specs)
    thruster_effector = thruster_bundle.effector

    on_times = ncfg.on_time_s or _nominal_basilisk_on_times(pcfg)
    if ncfg.burn_start_s > 0 and ncfg.burn_start_s < ncfg.duration_s:
        initial_on_times = tuple(0.0 for _ in on_times)
        cmd_msg = write_thruster_on_time_message(initial_on_times)
    else:
        cmd_msg = write_thruster_on_time_message(on_times)
    thruster_effector.cmdsInMsg.subscribeTo(cmd_msg)

    initial_mass = float(ncfg.initial_propellant_kg if ncfg.initial_propellant_kg is not None else pcfg.fuel_tank.initial_mass_kg)
    capacity = float(ncfg.tank_capacity_kg if ncfg.tank_capacity_kg is not None else pcfg.fuel_tank.capacity_kg)
    fuel_tank_cfg = replace(pcfg.fuel_tank, initial_mass_kg=initial_mass, capacity_kg=capacity)
    fuel_tank_bundle = build_fuel_tank_bundle_from_config("propulsionFuelTank", fuel_tank_cfg)
    tank = fuel_tank_bundle.tank

    attach_thruster_to_spacecraft(thruster_bundle, sc)
    attach_fuel_tank_to_spacecraft(tank, sc, thruster_effector)

    sim.AddModelToTask(task_name, sc)
    sim.AddModelToTask(task_name, thruster_effector)
    sim.AddModelToTask(task_name, tank)

    controller = None
    if ncfg.burn_start_s > 0 and ncfg.burn_start_s < ncfg.duration_s:
        controller = _DelayedBurnController(
            macros.sec2nano(float(ncfg.burn_start_s)),
            on_times,
            cmd_msg,
        )
        sim.AddModelToTask(task_name, controller)

    period = macros.sec2nano(float(ncfg.step_s))
    sc_rec = sc.scStateOutMsg.recorder(period)
    tank_rec = tank.fuelTankOutMsg.recorder(period)
    sim.AddModelToTask(task_name, sc_rec)
    sim.AddModelToTask(task_name, tank_rec)
    thr_rec = None
    if len(thruster_effector.thrusterOutMsgs) > 0:
        thr_rec = thruster_effector.thrusterOutMsgs[0].recorder(period)
        sim.AddModelToTask(task_name, thr_rec)

    modules = {
        "spacecraft": sc,
        "thruster_effector": thruster_effector,
        "thruster_bundle": thruster_bundle,
        "fuel_tank": tank,
        "fuel_tank_bundle": fuel_tank_bundle,
    }
    if controller is not None:
        modules["delayed_burn_controller"] = controller

    recorders = {"spacecraft": sc_rec, "fuel_tank": tank_rec}
    if thr_rec is not None:
        recorders["thruster_0"] = thr_rec

    return PropulsionBasiliskSimContext(
        subsystem="propulsion",
        propulsion_config=pcfg,
        config=ncfg,
        simulation=sim,
        process=process,
        task=task,
        task_name=task_name,
        modules=modules,
        recorders=recorders,
        message_handles={
            "thruster_command": cmd_msg,
            "spacecraft_state": sc.scStateOutMsg,
            "fuel_tank": tank.fuelTankOutMsg,
            "fuel_leak_rate": fuel_tank_bundle.leak_rate_msg,
        },
        base_parameters={
            "period_ns": period,
            "initial_fuel_mass_kg": initial_mass,
            "tank_capacity_kg": capacity,
            "on_time_s": on_times,
            "thruster_specs": tuple(thruster_specs),
            "fuel_tank_model": fuel_tank_cfg.tank_model,
            "fuel_leak_rate_kg_s": float(fuel_tank_cfg.leak_rate_kg_s),
        },
        component_sources={
            "thruster": "components.thruster.builder.build_thruster_dynamic_effector_bundle",
            "fuel_tank": "components.fuel_tank.builder.build_fuel_tank_bundle_from_config",
        },
    )
