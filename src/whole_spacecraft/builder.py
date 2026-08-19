"""Whole-spacecraft Basilisk builder.

This module is the builder layer for the whole-spacecraft Basilisk simulation.
It assembles the simulation graph but does not execute it.
"""
from __future__ import annotations

from dataclasses import replace
from math import cos, pi, sin
from typing import Any

try:
    from Basilisk.utilities import SimulationBaseClass, macros  # noqa: F401
except ImportError:
    SimulationBaseClass = None  # type: ignore[assignment]
    macros = None  # type: ignore[assignment]

from integrated.orbit_environment.builder import (  # noqa: E402
    attach_basilisk_orbital_environment_to_task,
    attach_gravity_factory_to_spacecraft,
    build_basilisk_orbital_environment,
)
from integrated.orbit_environment.schemas import BasiliskOrbEnvConfig  # noqa: E402
from integration.whole_spacecraft_links import (  # noqa: E402
    wire_comm_transmitter_storage_drain,
    wire_payload_to_comm_storage,
)
from integration.resource_feedback import (  # noqa: E402
    AdcsControlPowerBridge,
    DataActivityPowerBridge,
    DownlinkActivityPowerBridge,
    ThrusterActivityPowerBridge,
)
from subsystems.adcs.builder import (  # noqa: E402
    attach_adcs_closed_loop_graph,
    attach_adcs_to_spacecraft,
    build_adcs_closed_loop_config,
    build_nominal_adcs_config,
)
from subsystems.adcs.schemas import RwOnlyClosedLoopConfig  # noqa: E402
from subsystems.comm_data.builder import (  # noqa: E402
    attach_comm_data_basilisk_graph_to_task,
    build_comm_data_basilisk_assembly_graph,
    build_comm_data_native_odh_downlink,
    build_comm_data_native_rf_access_chain,
    build_whole_spacecraft_native_rf_access_chain,
)
from subsystems.eps.builder import (  # noqa: E402
    apply_eps_config_faults,
    attach_eps_basilisk_graph_to_task,
    build_basilisk_eps_graph,
    build_environment_coupled_basilisk_eps_graph,
    build_nominal_eps_config,
    eps_config_to_basilisk_config,
    get_eps_solar_power_output_msg,
)
from subsystems.payload.builder import (  # noqa: E402
    attach_payload_basilisk_graph_to_task,
    build_payload_basilisk_assembly_graph,
)
from subsystems.propulsion.builder import (  # noqa: E402
    apply_propulsion_config_faults,
    attach_propulsion_basilisk_graph_to_spacecraft,
    build_nominal_propulsion_config,
    build_propulsion_basilisk_assembly_graph,
)
from subsystems.thermal.builder import (  # noqa: E402
    apply_thermal_config_faults,
    attach_thermal_basilisk_graph_to_task,
    attach_thermal_network_graph_to_task,
    build_nominal_thermal_config,
    build_thermal_basilisk_assembly_graph,
    build_thermal_network_assembly_graph,
    build_eclipse_shadow_factor_bridge,
    build_mode_thermal_input_bridge,
    build_heater_power_feedback_bridge,
)
from whole_spacecraft._runtime_fault_injector import FaultInjector
from whole_spacecraft._mission_gate import WholeSpacecraftMissionGate
from whole_spacecraft.schemas import (
    WholeSpacecraftConfig,
    WholeSpacecraftCouplingRecord,
    WholeSpacecraftGraph,
)
from whole_spacecraft.task_cadence import TaskCadenceProbe, validate_task_periods


def _matrix_column(vec: tuple[float, float, float]) -> list[list[float]]:
    return [[float(vec[0])], [float(vec[1])], [float(vec[2])]]


def _matrix_3x3(flat: tuple[float, ...]) -> list[list[float]]:
    if len(flat) != 9:
        raise ValueError("spacecraft inertia must have 9 elements")
    return [
        [float(flat[0]), float(flat[1]), float(flat[2])],
        [float(flat[3]), float(flat[4]), float(flat[5])],
        [float(flat[6]), float(flat[7]), float(flat[8])],
    ]


def build_whole_spacecraft_bus(
    adcs_closed_loop_cfg: RwOnlyClosedLoopConfig,
    *,
    model_tag: str = "wholeSpacecraftBus",
    initial_orbit_radius_m: float = 7_000_000.0,
    initial_orbit_phase_deg: float = 0.0,
) -> Any:
    """Create the central whole-spacecraft dynamics object.

    Whole-spacecraft owns this object; ADCS, propulsion, and environment attach
    to it.  This prevents the ADCS subsystem from implicitly owning the central
    spacecraft bus in integrated simulations.
    """

    from Basilisk.simulation import spacecraft

    sc_object = spacecraft.Spacecraft()
    sc_object.ModelTag = model_tag
    sc_object.hub.mHub = float(adcs_closed_loop_cfg.spacecraft_mass_kg)
    sc_object.hub.r_BcB_B = [[0.0], [0.0], [0.0]]
    sc_object.hub.IHubPntBc_B = _matrix_3x3(adcs_closed_loop_cfg.spacecraft_inertia_kg_m2)
    sc_object.hub.sigma_BNInit = _matrix_column(adcs_closed_loop_cfg.initial_sigma_bn)
    sc_object.hub.omega_BN_BInit = _matrix_column(adcs_closed_loop_cfg.initial_omega_bn_b_rad_s)
    # Basilisk spacecraft defaults translational position/velocity to zero.
    # With gravity enabled, a zero-radius initial state creates singular
    # dynamics that can propagate NaN into ADCS/RW telemetry.  Use a simple
    # finite circular LEO state for the integrated nominal bus.
    leo_radius_m = float(initial_orbit_radius_m)
    if leo_radius_m <= 0.0:
        raise ValueError("initial_orbit_radius_m must be greater than zero")
    earth_mu_m3_s2 = 3.986004418e14
    circular_speed_m_s = (earth_mu_m3_s2 / leo_radius_m) ** 0.5
    phase_rad = float(initial_orbit_phase_deg) * pi / 180.0
    position_n = (leo_radius_m * cos(phase_rad), leo_radius_m * sin(phase_rad), 0.0)
    velocity_n = (-circular_speed_m_s * sin(phase_rad), circular_speed_m_s * cos(phase_rad), 0.0)
    sc_object.hub.r_CN_NInit = _matrix_column(position_n)
    sc_object.hub.v_CN_NInit = _matrix_column(velocity_n)
    return sc_object




def _coupling_record(
    *,
    name: str,
    source_subsystem: str,
    sink_subsystem: str,
    coupling_status: str,
    interface: str,
    evidence: tuple[str, ...] | list[str],
    notes: tuple[str, ...] | list[str] = (),
) -> WholeSpacecraftCouplingRecord:
    return WholeSpacecraftCouplingRecord(
        name=name,
        source_subsystem=source_subsystem,
        sink_subsystem=sink_subsystem,
        coupling_status=coupling_status,
        interface=interface,
        evidence=tuple(str(item) for item in evidence),
        notes=tuple(str(item) for item in notes),
    )


def _disabled_coupling_record(
    *,
    name: str,
    source_subsystem: str,
    sink_subsystem: str,
    interface: str,
) -> WholeSpacecraftCouplingRecord:
    return _coupling_record(
        name=name,
        source_subsystem=source_subsystem,
        sink_subsystem=sink_subsystem,
        coupling_status="disabled_by_schema",
        interface=interface,
        evidence=("disabled by WholeSpacecraftCouplingConfig",),
    )



def _factor_from_effect(obj: Any | None, attr: str) -> float:
    if obj is None:
        return 1.0
    try:
        return max(0.0, float(getattr(obj, attr, 1.0)))
    except Exception:
        return 1.0


def _payload_runtime_effects(payload_degradation: Any | None) -> dict[str, float]:
    return {
        "instrument_baud_factor": _factor_from_effect(payload_degradation, "instrument_baud_factor"),
        "storage_capacity_factor": _factor_from_effect(payload_degradation, "storage_capacity_factor"),
    }


def _comm_runtime_effects(comm_data_degradation: Any | None) -> dict[str, float]:
    return {
        "transmitter_baud_factor": _factor_from_effect(comm_data_degradation, "transmitter_baud_factor"),
        "storage_capacity_factor": _factor_from_effect(comm_data_degradation, "storage_capacity_factor"),
    }



def _compat_manifest_from_coupling_matrix(coupling_matrix: dict[str, WholeSpacecraftCouplingRecord]) -> dict[str, Any]:
    """Build a deprecated compatibility manifest from actual graph evidence."""

    active = [record for record in coupling_matrix.values() if record.coupling_status != "disabled_by_schema"]
    return {
        "deprecated_report_only": True,
        "source_of_truth": "WholeSpacecraftGraph.coupling_matrix",
        "contract_count": len(active),
        "contracts": [
            {
                "name": record.name,
                "source_subsystem": record.source_subsystem,
                "sink_subsystem": record.sink_subsystem,
                "interface": record.interface,
                "support": record.coupling_status,
                "note": "; ".join(record.evidence),
            }
            for record in active
        ],
    }

def build_whole_spacecraft_graph(
    cfg: WholeSpacecraftConfig | None = None,
) -> WholeSpacecraftGraph:
    """Build the whole-spacecraft Basilisk graph without executing it."""

    cfg = cfg or WholeSpacecraftConfig()
    step_periods = {
        "adcs_dyn_step_s": float(cfg.adcs_dyn_step_s),
        "adcs_fsw_step_s": float(cfg.adcs_fsw_step_s),
        "orb_env_step_s": float(cfg.orb_env_step_s),
        "thermal_step_s": float(cfg.thermal_step_s),
        "recorder_step_s": float(cfg.recorder_step_s),
    }
    invalid_periods = {name: value for name, value in step_periods.items() if value <= 0.0}
    if invalid_periods:
        raise ValueError(f"all step times must be positive: {invalid_periods}")

    coupling = cfg.coupling
    coupling_matrix: dict[str, WholeSpacecraftCouplingRecord] = {}
    payload_effects = _payload_runtime_effects(cfg.payload_degradation)
    comm_effects = _comm_runtime_effects(cfg.comm_data_degradation)
    payload_instrument_baud_bps = max(0.0, float(cfg.instrument_baud_bps) * payload_effects["instrument_baud_factor"])
    transmitter_baud_bps = max(0.0, float(cfg.transmitter_baud_bps) * comm_effects["transmitter_baud_factor"])
    storage_capacity_bits = max(0.0, float(cfg.storage_capacity_bits) * min(payload_effects["storage_capacity_factor"], comm_effects["storage_capacity_factor"]))

    eps_nominal_reference = build_nominal_eps_config(None)
    eps_cfg = build_nominal_eps_config(cfg.eps_degradation)
    if cfg.apply_fault_specs_at_build_time:
        eps_cfg = apply_eps_config_faults(eps_cfg, cfg.fault_specs)
    eps_battery_capacity_factor = (
        float(eps_cfg.battery.capacity_wh) / float(eps_nominal_reference.battery.capacity_wh)
        if float(eps_nominal_reference.battery.capacity_wh) else 1.0
    )
    eps_solar_power_factor = (
        float(eps_cfg.solar_panel.max_power_w) / float(eps_nominal_reference.solar_panel.max_power_w)
        if float(eps_nominal_reference.solar_panel.max_power_w) else 1.0
    )
    adcs_cfg = build_nominal_adcs_config(cfg.adcs_degradation)
    adcs_build_fault_specs = tuple(cfg.fault_specs) if cfg.apply_fault_specs_at_build_time else ()
    thermal_cfg = build_nominal_thermal_config(cfg.thermal_degradation)
    if cfg.apply_fault_specs_at_build_time:
        thermal_cfg = apply_thermal_config_faults(thermal_cfg, cfg.fault_specs)
    propulsion_cfg = cfg.propulsion_config or build_nominal_propulsion_config(cfg.propulsion_degradation)
    if cfg.apply_fault_specs_at_build_time:
        propulsion_cfg = apply_propulsion_config_faults(propulsion_cfg, cfg.fault_specs)

    period_grid = validate_task_periods(
        dynamics_step_s=cfg.adcs_dyn_step_s,
        fsw_step_s=cfg.adcs_fsw_step_s,
        orbit_environment_step_s=cfg.orb_env_step_s,
        thermal_step_s=cfg.thermal_step_s,
        recorder_step_s=cfg.recorder_step_s,
    )
    if period_grid["status"] != "PASS":
        raise ValueError(f"WHOLE_SPACECRAFT_TASK_PERIOD_GRID_INVALID: {period_grid['issues']}")

    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("wholeSpacecraftNativeProcess")
    sim_task_name = "wholeSpacecraftSimTask"
    fsw_task_name = "wholeSpacecraftFswTask"
    orb_env_task_name = "wholeSpacecraftOrbitEnvironmentTask"
    thermal_task_name = "wholeSpacecraftThermalTask"
    recorder_task_name = "wholeSpacecraftRecorderTask"
    conservation_sample_s = max(float(cfg.adcs_dyn_step_s), min(1.0, float(cfg.thermal_step_s)))
    recorder_task_step_s = min(float(cfg.recorder_step_s), conservation_sample_s)
    # Explicit task periods prevent output sampling from altering physical evolution.
    process.addTask(sim.CreateNewTask(fsw_task_name, macros.sec2nano(float(cfg.adcs_fsw_step_s))), 50)
    process.addTask(sim.CreateNewTask(orb_env_task_name, macros.sec2nano(float(cfg.orb_env_step_s))), 45)
    process.addTask(sim.CreateNewTask(sim_task_name, macros.sec2nano(float(cfg.adcs_dyn_step_s))), 40)
    process.addTask(sim.CreateNewTask(thermal_task_name, macros.sec2nano(float(cfg.thermal_step_s))), 30)
    process.addTask(sim.CreateNewTask(recorder_task_name, macros.sec2nano(recorder_task_step_s)), 10)

    task_cadence_probes = {
        "dynamics": TaskCadenceProbe(sim_task_name, float(cfg.adcs_dyn_step_s)),
        "fsw": TaskCadenceProbe(fsw_task_name, float(cfg.adcs_fsw_step_s)),
        "orbit_environment": TaskCadenceProbe(orb_env_task_name, float(cfg.orb_env_step_s)),
        "thermal": TaskCadenceProbe(thermal_task_name, float(cfg.thermal_step_s)),
        "recorder_task": TaskCadenceProbe(recorder_task_name, float(recorder_task_step_s)),
    }
    # Lowest task-local priority records the activation after all functional models.
    sim.AddModelToTask(sim_task_name, task_cadence_probes["dynamics"], None, -1000)
    sim.AddModelToTask(fsw_task_name, task_cadence_probes["fsw"], None, -1000)
    sim.AddModelToTask(orb_env_task_name, task_cadence_probes["orbit_environment"], None, -1000)
    sim.AddModelToTask(thermal_task_name, task_cadence_probes["thermal"], None, -1000)
    sim.AddModelToTask(recorder_task_name, task_cadence_probes["recorder_task"], None, -1000)

    adcs_closed_loop_cfg = build_adcs_closed_loop_config(
        adcs_cfg,
        fault_specs=adcs_build_fault_specs,
        base_config=RwOnlyClosedLoopConfig(
            duration_s=0.0,
            initial_sigma_bn=tuple(float(x) for x in cfg.mission_initial_sigma_bn),
            initial_omega_bn_b_rad_s=tuple(float(x) for x in cfg.mission_initial_omega_bn_b_rad_s),
        ),
    )
    central_spacecraft = build_whole_spacecraft_bus(
        adcs_closed_loop_cfg,
        initial_orbit_radius_m=float(cfg.initial_orbit_radius_m),
        initial_orbit_phase_deg=float(cfg.initial_orbit_phase_deg),
    )

    sc_state_msg = central_spacecraft.scStateOutMsg
    orb_env_graph = build_basilisk_orbital_environment(
        sim,
        sc_state_msg,
        BasiliskOrbEnvConfig(
            step_s=float(cfg.orb_env_step_s),
            sun_model=cfg.orb_env_sun_model,
            sun_vector_n=cfg.orb_env_sun_vector_n,
            magnetic_field_model=cfg.orb_env_magnetic_field_model,
            use_j2_gravity=cfg.orb_env_use_j2_gravity,
            spice_data_path=cfg.orb_env_spice_data_path,
            wmm_data_path=cfg.orb_env_wmm_data_path,
            strict_resource_loading=bool(cfg.orb_env_strict_resource_loading),
            spice_epoch_utc=str(cfg.orb_env_spice_epoch_utc),
            enable_eclipse=cfg.orb_env_enable_eclipse,
        ),
    )
    attach_basilisk_orbital_environment_to_task(sim, orb_env_task_name, orb_env_graph)

    adcs_graph = attach_adcs_to_spacecraft(
        central_spacecraft,
        adcs_closed_loop_cfg,
        environment_graph=orb_env_graph,
    )
    attach_adcs_closed_loop_graph(sim, sim_task_name, fsw_task_name, adcs_graph)
    coupling_matrix["adcs_spacecraft_to_orbit_environment"] = _coupling_record(
        name="adcs_spacecraft_to_orbit_environment",
        source_subsystem="spacecraft_dynamics",
        sink_subsystem="orbit_environment",
        coupling_status="coupled_required_core_link",
        interface="spacecraft.scStateOutMsg -> eclipse/magnetic environment modules",
        evidence=(
            "build_basilisk_orbital_environment(sim, central_spacecraft.scStateOutMsg, ...)",
            f"magnetic_field_module={getattr(orb_env_graph.magnetic_field_module, 'ModelTag', type(orb_env_graph.magnetic_field_module).__name__)}",
        ),
        notes=("Required production coupling; not exposed as a disable switch.",),
    )
    if coupling.enable_gravity_to_spacecraft:
        attach_gravity_factory_to_spacecraft(central_spacecraft, orb_env_graph.grav_factory)
        coupling_matrix["gravity_to_spacecraft"] = _coupling_record(
            name="gravity_to_spacecraft",
            source_subsystem="orbit_environment",
            sink_subsystem="spacecraft_dynamics",
            coupling_status="coupled",
            interface="gravBodyFactory.addBodiesTo(spacecraft)",
            evidence=("attach_gravity_factory_to_spacecraft(central_spacecraft, orb_env_graph.grav_factory)",),
        )
    else:
        coupling_matrix["gravity_to_spacecraft"] = _disabled_coupling_record(
            name="gravity_to_spacecraft",
            source_subsystem="orbit_environment",
            sink_subsystem="spacecraft_dynamics",
            interface="gravBodyFactory.addBodiesTo(spacecraft)",
        )

    eps_cfg = replace(
        eps_cfg,
        battery=replace(
            eps_cfg.battery,
            capacity_wh=float(cfg.battery_capacity_wh) * max(0.0, eps_battery_capacity_factor),
            initial_soc=float(cfg.initial_soc),
        ),
        solar_panel=replace(eps_cfg.solar_panel, max_power_w=abs(float(cfg.solar_power_w)) * max(0.0, eps_solar_power_factor)),
        loads={
            **eps_cfg.loads,
            # Activity-dependent loads are supplied by message-driven bridges below.
            "payload": replace(eps_cfg.loads["payload"], base_w=0.0, mode_power_w={"payload": 0.0}),
            "comm": replace(eps_cfg.loads["comm"], base_w=0.0, mode_power_w={"payload": 0.0, "downlink": 0.0}),
            "adcs": replace(eps_cfg.loads["adcs"], base_w=0.0, mode_power_w={"payload": 0.0, "safePoint": 0.0}),
            "obc": replace(eps_cfg.loads["obc"], base_w=abs(float(cfg.bus_power_w))),
        },
    )
    eps_basilisk_cfg = eps_config_to_basilisk_config(eps_cfg, mode="payload", shadow_factor=1.0)
    if coupling.enable_orbit_sun_attitude_eclipse_to_eps_solar_power:
        eps_graph = build_environment_coupled_basilisk_eps_graph(
            eps_basilisk_cfg,
            sun_msg=orb_env_graph.sun_msg,
            spacecraft_state_msg=central_spacecraft.scStateOutMsg,
            eclipse_msg=orb_env_graph.eclipse_msg,
            panel_normal_b=tuple(float(x) for x in cfg.solar_panel_normal_b),
            enable_load_shedding=True,
            payload_min_soc=0.55,
            comm_min_soc=0.50,
            heater_min_soc=0.30,
            adcs_min_soc=0.20,
            recovery_soc=0.65,
        )
        coupling_matrix["orbit_sun_attitude_eclipse_to_eps_solar_power"] = _coupling_record(
            name="orbit_sun_attitude_eclipse_to_eps_solar_power",
            source_subsystem="orbit_environment,spacecraft_dynamics,adcs",
            sink_subsystem="eps",
            coupling_status="coupled" if orb_env_graph.eclipse_msg is not None else "coupled_without_eclipse",
            interface="SunMsg + SCStatesMsg + EclipseMsg -> SimpleSolarPanel -> SimpleBattery",
            evidence=(
                "SimpleSolarPanel.sunInMsg subscribes to orb_env_graph.sun_msg",
                "SimpleSolarPanel.stateInMsg subscribes to central_spacecraft.scStateOutMsg",
                "SimpleSolarPanel.sunEclipseInMsg subscribes to orb_env_graph.eclipse_msg" if orb_env_graph.eclipse_msg is not None else "eclipse message unavailable; incidence coupling remains active",
            ),
            notes=("Solar generation is no longer a constant positive power node in the recommended whole-spacecraft path.",),
        )
    else:
        eps_graph = build_basilisk_eps_graph(eps_basilisk_cfg)
        coupling_matrix["orbit_sun_attitude_eclipse_to_eps_solar_power"] = _disabled_coupling_record(
            name="orbit_sun_attitude_eclipse_to_eps_solar_power",
            source_subsystem="orbit_environment,spacecraft_dynamics,adcs",
            sink_subsystem="eps",
            interface="SunMsg + SCStatesMsg + EclipseMsg -> solar generation",
        )
    attach_eps_basilisk_graph_to_task(sim, sim_task_name, eps_graph)
    battery = eps_graph.battery

    payload_graph = build_payload_basilisk_assembly_graph(
        instrument_baud_bps=payload_instrument_baud_bps,
        data_name=cfg.payload_data_name,
        model_tag=cfg.payload_model_tag,
    )
    attach_payload_basilisk_graph_to_task(sim, sim_task_name, payload_graph)
    instrument = payload_graph.instrument

    comm_graph = build_comm_data_basilisk_assembly_graph(
        storage_capacity_bits=storage_capacity_bits,
        initial_bits=cfg.storage_initial_bits,
        transmitter_baud_bps=transmitter_baud_bps,
        storage_model_tag=cfg.storage_model_tag,
        transmitter_model_tag=cfg.transmitter_model_tag,
    )
    attach_comm_data_basilisk_graph_to_task(sim, sim_task_name, comm_graph)
    storage = comm_graph.storage
    transmitter = comm_graph.transmitter

    if coupling.enable_payload_to_comm_storage:
        try:
            evidence = wire_payload_to_comm_storage(instrument, storage)
        except Exception as exc:
            raise RuntimeError(
                "Failed to wire Payload -> Comm/Data storage integration link. "
                "This may indicate a Basilisk version compatibility issue "
                "where SimpleStorageUnit link APIs changed."
            ) from exc
        coupling_matrix["payload_to_comm_storage"] = _coupling_record(
            name="payload_to_comm_storage",
            source_subsystem="payload",
            sink_subsystem="comm_data",
            coupling_status="coupled",
            interface="instrument.nodeDataOutMsg -> storage.addDataNodeToModel",
            evidence=evidence,
        )
    else:
        coupling_matrix["payload_to_comm_storage"] = _disabled_coupling_record(
            name="payload_to_comm_storage",
            source_subsystem="payload",
            sink_subsystem="comm_data",
            interface="instrument.nodeDataOutMsg -> storage.addDataNodeToModel",
        )

    if coupling.enable_comm_transmitter_to_storage_drain:
        try:
            evidence = wire_comm_transmitter_storage_drain(transmitter, storage)
        except Exception as exc:
            raise RuntimeError(
                "Failed to wire Comm/Data transmitter -> storage drain integration link. "
                "This may indicate a Basilisk version compatibility issue "
                "where SimpleStorageUnit/SimpleTransmitter link APIs changed."
            ) from exc
        coupling_matrix["comm_transmitter_to_storage_drain"] = _coupling_record(
            name="comm_transmitter_to_storage_drain",
            source_subsystem="comm_data",
            sink_subsystem="comm_data",
            coupling_status="coupled",
            interface="transmitter.storageUnitDataOutMsg + transmitter.nodeDataOutMsg -> storage drain",
            evidence=evidence,
            notes=("Compatibility fixed-baud path; native ODH downlink is the mainline V11 path when enabled.",),
        )
    else:
        coupling_matrix["comm_transmitter_to_storage_drain"] = _disabled_coupling_record(
            name="comm_transmitter_to_storage_drain",
            source_subsystem="comm_data",
            sink_subsystem="comm_data",
            interface="transmitter.storageUnitDataOutMsg + transmitter.nodeDataOutMsg -> storage drain",
        )
    transmitter.nodeBaudRate = 0.0

    rf_access_graph = None
    if coupling.enable_comm_native_odh_downlink:
        try:
            rf_access_graph = build_whole_spacecraft_native_rf_access_chain(
                sc_state_msg=central_spacecraft.scStateOutMsg,
                frequency_hz=float(cfg.native_downlink_frequency_hz),
                bandwidth_hz=float(cfg.native_downlink_bandwidth_hz),
                spacecraft_orientation_b=tuple(float(x) for x in cfg.spacecraft_antenna_orientation_b),
                pointing_loss_enabled=bool(cfg.native_link_budget_pointing_loss_enabled),
                frequency_loss_enabled=bool(cfg.native_link_budget_frequency_loss_enabled),
                atmospheric_attenuation_enabled=bool(cfg.native_link_budget_atmospheric_attenuation_enabled),
                sun_msg=orb_env_graph.sun_msg,
                planet_msgs=orb_env_graph.planet_msgs,
                eclipse_msg=orb_env_graph.eclipse_msg,
            )
            for model in (
                rf_access_graph.ground_location,
                rf_access_graph.spacecraft_antenna,
                rf_access_graph.ground_antenna,
                rf_access_graph.link_budget,
            ):
                sim.AddModelToTask(sim_task_name, model)
            coupling_matrix["ground_access_rf_link_budget"] = _coupling_record(
                name="ground_access_rf_link_budget",
                source_subsystem="comm_data",
                sink_subsystem="comm_data",
                coupling_status="coupled",
                interface="groundLocation.AccessMsg + SimpleAntenna x2 -> LinkBudget.LinkBudget",
                evidence=(
                    "groundLocation.addSpacecraftToModel(central_spacecraft.scStateOutMsg)",
                    "spacecraft SimpleAntenna subscribes to spacecraft state",
                    "ground SimpleAntenna subscribes to ground state",
                    "LinkBudget subscribes to native SimpleAntenna output logs",
                ),
            )
        except Exception as exc:
            raise RuntimeError("Failed to build whole-spacecraft native ground access/RF chain") from exc
    else:
        coupling_matrix["ground_access_rf_link_budget"] = _disabled_coupling_record(
            name="ground_access_rf_link_budget",
            source_subsystem="comm_data",
            sink_subsystem="comm_data",
            interface="groundLocation.AccessMsg + SimpleAntenna x2 -> LinkBudget.LinkBudget",
        )

    native_downlink = None
    native_downlink_link_budget_msg = None
    if coupling.enable_comm_native_odh_downlink:
        try:
            native_downlink, native_downlink_link_budget_msg = build_comm_data_native_odh_downlink(
                storage=storage,
                data_name=cfg.payload_data_name,
                bit_rate_request_bps=float(cfg.native_downlink_bit_rate_request_bps),
                packet_size_bits=float(cfg.native_downlink_packet_size_bits),
                max_retransmissions=int(cfg.native_downlink_max_retransmissions),
                link_active=True,
                cnr_linear=0.0 if rf_access_graph is not None else float(cfg.native_downlink_cnr_linear),
                distance_m=float(cfg.native_downlink_distance_m),
                bandwidth_hz=float(cfg.native_downlink_bandwidth_hz),
                frequency_hz=float(cfg.native_downlink_frequency_hz),
            )
            sim.AddModelToTask(sim_task_name, native_downlink)
        except Exception as exc:
            raise RuntimeError("Failed to build whole-spacecraft native DownlinkHandling chain") from exc
        coupling_matrix["comm_native_odh_to_storage_drain"] = _coupling_record(
            name="comm_native_odh_to_storage_drain",
            source_subsystem="comm_data",
            sink_subsystem="comm_data",
            coupling_status="coupled",
            interface="DataStorageStatusMsg + LinkBudgetMsg -> DownlinkHandling -> storage.addDataNodeToModel",
            evidence=(
                "downlink.addStorageUnitToDownlink(storage.storageUnitDataOutMsg)",
                "storage.addDataNodeToModel(downlink.nodeDataOutMsg)",
                "mission_gate gates native ground access / RF LinkBudget into DownlinkHandling LinkBudgetMsg",
            ),
        )
    else:
        coupling_matrix["comm_native_odh_to_storage_drain"] = _disabled_coupling_record(
            name="comm_native_odh_to_storage_drain",
            source_subsystem="comm_data",
            sink_subsystem="comm_data",
            interface="DataStorageStatusMsg + LinkBudgetMsg -> DownlinkHandling -> storage.addDataNodeToModel",
        )

    # Build propulsion before thermal so actual thruster activity can feed both
    # EPS and thermal nodes in the same simulation graph.
    propulsion_graph = None
    fuel_tank = None
    if cfg.propulsion_enabled:
        propulsion_graph = build_propulsion_basilisk_assembly_graph(propulsion_cfg, on_time_s=cfg.propulsion_on_time_s)
        if coupling.enable_propulsion_effector_to_spacecraft:
            attach_propulsion_basilisk_graph_to_spacecraft(central_spacecraft, sim, sim_task_name, propulsion_graph)
            coupling_matrix["propulsion_effector_to_spacecraft"] = _coupling_record(
                name="propulsion_effector_to_spacecraft",
                source_subsystem="propulsion",
                sink_subsystem="spacecraft_dynamics",
                coupling_status="coupled",
                interface="ThrusterDynamicEffector/FuelTank -> spacecraft dynamic effector",
                evidence=("attach_propulsion_basilisk_graph_to_spacecraft(central_spacecraft, ...) executed",),
            )
        else:
            coupling_matrix["propulsion_effector_to_spacecraft"] = _disabled_coupling_record(
                name="propulsion_effector_to_spacecraft",
                source_subsystem="propulsion",
                sink_subsystem="spacecraft_dynamics",
                interface="ThrusterDynamicEffector/FuelTank -> spacecraft dynamic effector",
            )
        fuel_tank = propulsion_graph.fuel_tank

    power_inputs: list[tuple[Any, float, str, str]] = []
    shadow_factor_msg = None
    heater_feedback = None
    resource_feedback_bridges: dict[str, Any] = {}

    payload_status_msg = eps_graph.status_messages.get("payload")
    comm_status_msg = eps_graph.status_messages.get("comm")
    adcs_status_msg = eps_graph.status_messages.get("adcs")

    payload_power_bridge = DataActivityPowerBridge(
        "payloadActivityPowerBridge",
        nominal_rate_bps=max(1.0, float(payload_instrument_baud_bps)),
        active_power_w=abs(float(cfg.payload_power_w)),
        data_msg=instrument.nodeDataOutMsg,
        enable_status_msg=payload_status_msg,
    )
    sim.AddModelToTask(sim_task_name, payload_power_bridge)
    battery.addPowerNodeToModel(payload_power_bridge.powerOutMsg)
    resource_feedback_bridges["payload"] = payload_power_bridge
    power_inputs.append((payload_power_bridge.powerOutMsg, 0.95, "payload", "payload.actual_activity"))
    coupling_matrix["eps_pdu_to_payload_activity"] = _coupling_record(
        name="eps_pdu_to_payload_activity", source_subsystem="eps", sink_subsystem="payload",
        coupling_status="coupled",
        interface="PDU payloadStatusOutMsg -> MissionGate/DataActivityPowerBridge -> payload data and power",
        evidence=("payload PDU status is consumed by mission gate and payload power bridge",),
    )
    coupling_matrix["payload_activity_to_eps_thermal"] = _coupling_record(
        name="payload_activity_to_eps_thermal", source_subsystem="payload", sink_subsystem="eps,thermal",
        coupling_status="proxy_coupled_verified",
        interface="instrument.nodeDataOutMsg -> DataActivityPowerBridge -> battery + thermal payload node",
        evidence=("payload electrical power scales with actual DataNodeUsage baudRate",),
    )

    adcs_power_bridge = AdcsControlPowerBridge(
        "adcsControlPowerBridge",
        motor_torque_msg=adcs_graph.rw_closed_loop.rw_motor_torque.rwMotorTorqueOutMsg,
        wheel_speed_msg=adcs_graph.rw_closed_loop.rw_state_effector.rwSpeedOutMsg,
        base_power_w=10.0,
        drive_efficiency=0.75,
        max_power_w=120.0,
        enable_status_msg=adcs_status_msg,
    )
    sim.AddModelToTask(sim_task_name, adcs_power_bridge)
    battery.addPowerNodeToModel(adcs_power_bridge.powerOutMsg)
    resource_feedback_bridges["adcs"] = adcs_power_bridge
    power_inputs.append((adcs_power_bridge.powerOutMsg, 0.9, "adcs", "adcs.control_effort"))
    coupling_matrix["adcs_control_effort_to_eps_thermal"] = _coupling_record(
        name="adcs_control_effort_to_eps_thermal", source_subsystem="adcs", sink_subsystem="eps,thermal",
        coupling_status="proxy_coupled_verified",
        interface="RW motor torque + wheel speed -> electrical power -> battery + ADCS thermal node",
        evidence=("sum(abs(motorTorque * wheelSpeed))/drive_efficiency",),
    )

    if native_downlink is not None:
        comm_power_bridge = DownlinkActivityPowerBridge(
            "commDownlinkPowerBridge",
            nominal_rate_bps=max(1.0, float(cfg.comm_power_reference_rate_bps)),
            active_power_w=30.0, idle_power_w=2.0,
            downlink_msg=native_downlink.downlinkOutMsg, enable_status_msg=comm_status_msg,
        )
    else:
        comm_power_bridge = DataActivityPowerBridge(
            "commTransmitterPowerBridge",
            nominal_rate_bps=max(1.0, float(transmitter_baud_bps)),
            active_power_w=30.0, idle_power_w=2.0,
            data_msg=transmitter.nodeDataOutMsg, enable_status_msg=comm_status_msg,
        )
    sim.AddModelToTask(sim_task_name, comm_power_bridge)
    battery.addPowerNodeToModel(comm_power_bridge.powerOutMsg)
    resource_feedback_bridges["comm"] = comm_power_bridge
    power_inputs.append((comm_power_bridge.powerOutMsg, 0.9, "electronics", "comm.actual_activity"))
    coupling_matrix["eps_pdu_to_comm_activity"] = _coupling_record(
        name="eps_pdu_to_comm_activity", source_subsystem="eps", sink_subsystem="comm_data",
        coupling_status="coupled",
        interface="PDU commStatusOutMsg -> MissionGate/CommPowerBridge -> downlink and power",
        evidence=("comm PDU status is consumed by mission gate and communication power bridge",),
    )
    coupling_matrix["comm_activity_to_eps_thermal"] = _coupling_record(
        name="comm_activity_to_eps_thermal", source_subsystem="comm_data", sink_subsystem="eps,thermal",
        coupling_status="proxy_coupled_verified",
        interface="native downlink/DataNodeUsage activity -> CommPowerBridge -> battery + electronics thermal node",
        evidence=("communication electrical power scales with actual delivered/native downlink activity",),
        notes=("The power-to-heat mapping is an engineering proxy and requires paired-run causal validation for quantitative claims.",),
    )

    if propulsion_graph is not None:
        thruster_power_bridge = ThrusterActivityPowerBridge(
            "thrusterActivityPowerBridge",
            thruster_output_msgs=tuple(propulsion_graph.thruster_effector.thrusterOutMsgs),
            active_power_per_thruster_w=20.0,
        )
        sim.AddModelToTask(sim_task_name, thruster_power_bridge)
        battery.addPowerNodeToModel(thruster_power_bridge.powerOutMsg)
        resource_feedback_bridges["propulsion"] = thruster_power_bridge
        power_inputs.append((thruster_power_bridge.powerOutMsg, 0.85, "structure", "propulsion.actual_activity"))
        coupling_matrix["propulsion_activity_to_eps_thermal"] = _coupling_record(
            name="propulsion_activity_to_eps_thermal", source_subsystem="propulsion", sink_subsystem="eps,thermal",
            coupling_status="proxy_coupled_verified",
            interface="THROutputMsg.thrustFactor -> ThrusterActivityPowerBridge -> battery + structure thermal node",
            evidence=("native thruster output factors drive electrical and thermal resource feedback",),
        )

    if coupling.enable_eps_loads_to_thermal_heat and eps_graph.load_sinks:
        default_node_map = {
            "obc": "electronics",
            "thermal": "electronics",
            "comm": "electronics",
            "payload": "payload",
            "adcs": "adcs",
            "thruster": "structure",
            "eps": "electronics",
        }
        heat_evidence: list[str] = []
        for load_name, sink in eps_graph.load_sinks.items():
            efficiency_factor = float(thermal_cfg.heat_efficiency_by_component.get(load_name, 0.9))
            node_name = str(thermal_cfg.component_node_map.get(load_name, default_node_map.get(load_name, "electronics")))
            power_inputs.append((sink.nodePowerOutMsg, efficiency_factor, node_name, f"eps.{load_name}"))
            heat_evidence.append(
                f"{load_name}.nodePowerOutMsg -> {node_name} efficiency={efficiency_factor}"
            )
        coupling_matrix["eps_loads_to_thermal_heat"] = _coupling_record(
            name="eps_loads_to_thermal_heat",
            source_subsystem="eps",
            sink_subsystem="thermal",
            coupling_status="proxy_coupled_verified",
            interface="PowerNodeUsageMsg -> node-specific ThermalPowerBridge -> ThermalNode.heatInMsg",
            evidence=heat_evidence,
            notes=(
                "Electrical draw is mapped to deposited heat by configured efficiency factors.",
                "The proxy is audited at runtime through bridge energy counters; it is not a detailed electro-thermal solver.",
            ),
        )
    else:
        coupling_matrix["eps_loads_to_thermal_heat"] = _disabled_coupling_record(
            name="eps_loads_to_thermal_heat",
            source_subsystem="eps",
            sink_subsystem="thermal",
            interface="PowerNodeUsageMsg -> thermal heat input",
        )

    if coupling.enable_orbit_eclipse_to_thermal_shadow and orb_env_graph.eclipse_msg is not None:
        if cfg.thermal_use_network:
            coupling_matrix["orbit_eclipse_to_thermal_shadow"] = _coupling_record(
                name="orbit_eclipse_to_thermal_shadow",
                source_subsystem="orbit_environment",
                sink_subsystem="thermal",
                coupling_status="coupled",
                interface="EclipseMsg -> ThermalNetwork.SolarHeatInput.eclipseInMsg",
                evidence=("thermal network solar input subscribes directly to orb_env_graph.eclipse_msg",),
            )
        else:
            shadow_converter = build_eclipse_shadow_factor_bridge(orb_env_graph.eclipse_msg)
            shadow_factor_msg = shadow_converter.shadowFactorOutMsg
            sim.AddModelToTask(thermal_task_name, shadow_converter)
            coupling_matrix["orbit_eclipse_to_thermal_shadow"] = _coupling_record(
                name="orbit_eclipse_to_thermal_shadow",
                source_subsystem="orbit_environment",
                sink_subsystem="thermal",
                coupling_status="proxy_coupled",
                interface="EclipseMsg -> EclipseShadowFactorConverter -> reduced thermal shadow factor",
                evidence=("shadow_converter.eclipseInMsg.subscribeTo(orb_env_graph.eclipse_msg)",),
            )
    elif not coupling.enable_orbit_eclipse_to_thermal_shadow:
        coupling_matrix["orbit_eclipse_to_thermal_shadow"] = _disabled_coupling_record(
            name="orbit_eclipse_to_thermal_shadow",
            source_subsystem="orbit_environment",
            sink_subsystem="thermal",
            interface="EclipseMsg -> thermal shadow factor",
        )
    else:
        coupling_matrix["orbit_eclipse_to_thermal_shadow"] = _coupling_record(
            name="orbit_eclipse_to_thermal_shadow",
            source_subsystem="orbit_environment",
            sink_subsystem="thermal",
            coupling_status="not_coupled_no_eclipse_message",
            interface="EclipseMsg -> thermal shadow factor",
            evidence=("cfg.orb_env_enable_eclipse is False, so orb_env_graph.eclipse_msg is None",),
            notes=("Enable WholeSpacecraftConfig.orb_env_enable_eclipse to build this native eclipse-to-thermal link.",),
        )

    # Mode heat is a fallback only.  When EPS load messages are present, adding
    # a second mode-power source would double-count the same electrical loads.
    if thermal_cfg.mode_power_w_by_node and not power_inputs and not cfg.thermal_use_network:
        mode_thermal_input = build_mode_thermal_input_bridge(
            thermal_cfg.mode_power_w_by_node, current_mode="payload"
        )
        power_inputs.append((mode_thermal_input.heatOutMsg, 1.0, "electronics", "mode.payload"))
        sim.AddModelToTask(thermal_task_name, mode_thermal_input)

    if cfg.thermal_use_network:
        thermal_graph = build_thermal_network_assembly_graph(
            duration_s=max(3600.0, float(cfg.thermal_step_s)),
            step_s=cfg.thermal_step_s,
            power_inputs=power_inputs if power_inputs else None,
            eclipse_msg=(orb_env_graph.eclipse_msg if coupling.enable_orbit_eclipse_to_thermal_shadow else None),
            thermal_degradation=cfg.thermal_degradation,
        )
        attach_thermal_network_graph_to_task(sim, thermal_task_name, thermal_graph)
        heater_status_msg = eps_graph.status_messages.get("heater")
        if heater_status_msg is not None:
            for heater in thermal_graph.network.heaters.values():
                heater.subscribe_enable(heater_status_msg)
            coupling_matrix["eps_pdu_to_thermal_heaters"] = _coupling_record(
                name="eps_pdu_to_thermal_heaters", source_subsystem="eps", sink_subsystem="thermal",
                coupling_status="coupled",
                interface="PDU heaterStatusOutMsg -> ThermalHeater.enableInMsg",
                evidence=("all thermal-network heaters subscribe to EPS PDU heater permit",),
            )

        if coupling.enable_thermal_heaters_to_eps_load and thermal_graph.network.heaters:
            heater_feedback = build_heater_power_feedback_bridge([
                (heater.heaterOutMsg, heater.params.power_w)
                for heater in thermal_graph.network.heaters.values()
            ])
            if heater_feedback.heaterInMsgs:
                sim.AddModelToTask(thermal_task_name, heater_feedback)
                battery.addPowerNodeToModel(heater_feedback.powerOutMsg)
                coupling_matrix["thermal_heaters_to_eps_load"] = _coupling_record(
                    name="thermal_heaters_to_eps_load",
                    source_subsystem="thermal",
                    sink_subsystem="eps",
                    coupling_status="proxy_coupled_verified",
                    interface="heater status/output -> HeaterPowerFeedback.PowerNodeUsageMsg -> battery.addPowerNodeToModel",
                    evidence=tuple(f"{name}.heaterOutMsg power_w={heater.params.power_w}" for name, heater in thermal_graph.network.heaters.items()),
                    notes=("Heater electrical load is bridged back to EPS as a power sink.",),
                )
        elif not coupling.enable_thermal_heaters_to_eps_load:
            coupling_matrix["thermal_heaters_to_eps_load"] = _disabled_coupling_record(
                name="thermal_heaters_to_eps_load",
                source_subsystem="thermal",
                sink_subsystem="eps",
                interface="heater status/output -> EPS load",
            )
    else:
        thermal_graph = build_thermal_basilisk_assembly_graph(
            duration_s=0.0,
            step_s=cfg.thermal_step_s,
            heat_power_w=float(cfg.thermal_heat_power_w),
            power_inputs=power_inputs if power_inputs else None,
            shadow_factor_msg=shadow_factor_msg,
            thermal_cfg=thermal_cfg,
        )
        attach_thermal_basilisk_graph_to_task(sim, thermal_task_name, thermal_graph)

        if coupling.enable_thermal_heaters_to_eps_load and thermal_graph.nodes:
            heater_inputs: list[tuple[Any, float]] = []
            heater_evidence: list[str] = []
            for node_name, node in thermal_graph.nodes.items():
                heater = thermal_cfg.heaters.get(node_name)
                if heater:
                    heater_inputs.append((node.heaterStatusOutMsg, heater.max_power_w))
                    heater_evidence.append(f"{node_name}.heaterStatusOutMsg power_w={heater.max_power_w}")
            heater_feedback = build_heater_power_feedback_bridge(heater_inputs)
            if heater_feedback.heaterInMsgs:
                sim.AddModelToTask(thermal_task_name, heater_feedback)
                battery.addPowerNodeToModel(heater_feedback.powerOutMsg)
                coupling_matrix["thermal_heaters_to_eps_load"] = _coupling_record(
                    name="thermal_heaters_to_eps_load",
                    source_subsystem="thermal",
                    sink_subsystem="eps",
                    coupling_status="proxy_coupled_verified",
                    interface="heater status/output -> HeaterPowerFeedback.PowerNodeUsageMsg -> battery.addPowerNodeToModel",
                    evidence=heater_evidence,
                    notes=("Heater electrical load is bridged back to EPS as a power sink.",),
                )
        elif not coupling.enable_thermal_heaters_to_eps_load:
            coupling_matrix["thermal_heaters_to_eps_load"] = _disabled_coupling_record(
                name="thermal_heaters_to_eps_load",
                source_subsystem="thermal",
                sink_subsystem="eps",
                interface="heater status/output -> EPS load",
            )

    mission_gate = None
    if coupling.enable_adcs_pointing_to_payload_comm_gate:
        mission_gate = WholeSpacecraftMissionGate(
            battery_status_msg=battery.batPowerOutMsg,
            thermal_status_msg=thermal_graph.evaluator.thermalStatusOutMsg,
            attitude_guid_msg=adcs_graph.rw_closed_loop.attitude_error.attGuidOutMsg,
            instrument=instrument,
            transmitter=transmitter,
            payload_nominal_baud_bps=float(payload_instrument_baud_bps),
            transmitter_nominal_baud_bps=0.0 if native_downlink_link_budget_msg is not None else float(transmitter_baud_bps),
            access_window_s=20.0,
            access_period_s=60.0,
            max_pointing_error_deg=0.25,
            downlink_link_budget_msg=native_downlink_link_budget_msg,
            downlink_bit_rate_request_bps=float(cfg.native_downlink_bit_rate_request_bps),
            downlink_cnr_linear=float(cfg.native_downlink_cnr_linear),
            downlink_distance_m=float(cfg.native_downlink_distance_m),
            downlink_bandwidth_hz=float(cfg.native_downlink_bandwidth_hz),
            downlink_frequency_hz=float(cfg.native_downlink_frequency_hz),
            native_access_msg=(rf_access_graph.access_msg if rf_access_graph is not None else None),
            native_link_budget_source_msg=(rf_access_graph.link_budget_msg if rf_access_graph is not None else None),
            native_link_budget_cnr_floor_linear=float(cfg.native_downlink_cnr_floor_linear),
            payload_power_status_msg=payload_status_msg,
            comm_power_status_msg=comm_status_msg,
        )
        sim.AddModelToTask(sim_task_name, mission_gate)
        coupling_matrix["adcs_pointing_to_payload_comm_gate"] = _coupling_record(
            name="adcs_pointing_to_payload_comm_gate",
            source_subsystem="adcs",
            sink_subsystem="payload,comm_data",
            coupling_status="proxy_coupled_verified",
            interface="AttGuidMsg + EPS/Thermal status -> WholeSpacecraftMissionGate -> instrument baud and native downlink LinkBudgetMsg",
            evidence=(
                "mission_gate.attitude_guid_msg subscribes to adcs attitude error",
                "mission_gate writes instrument baud commands",
                "mission_gate reads native AccessMsg when RF/access graph is enabled",
                "mission_gate writes access-gated LinkBudgetMsg for DownlinkHandling",
            ),
            notes=("MissionGate handles task permission and access gating; BER/PER/storage removal remain in Basilisk DownlinkHandling.",),
        )
    else:
        coupling_matrix["adcs_pointing_to_payload_comm_gate"] = _disabled_coupling_record(
            name="adcs_pointing_to_payload_comm_gate",
            source_subsystem="adcs",
            sink_subsystem="payload,comm_data",
            interface="AttGuidMsg + EPS/Thermal status -> payload/comm rates",
        )


    recorders: dict[str, Any] = {
        "battery": battery.batPowerOutMsg.recorder(macros.sec2nano(float(cfg.recorder_step_s))),
        "storage": storage.storageUnitDataOutMsg.recorder(macros.sec2nano(float(cfg.recorder_step_s))),
        "instrument": instrument.nodeDataOutMsg.recorder(macros.sec2nano(float(cfg.recorder_step_s))),
        "transmitter": transmitter.nodeDataOutMsg.recorder(macros.sec2nano(float(cfg.recorder_step_s))),
        "conservation_battery": battery.batPowerOutMsg.recorder(macros.sec2nano(conservation_sample_s)),
        "conservation_storage": storage.storageUnitDataOutMsg.recorder(macros.sec2nano(conservation_sample_s)),
        "conservation_instrument": instrument.nodeDataOutMsg.recorder(macros.sec2nano(conservation_sample_s)),
        "solar_power": get_eps_solar_power_output_msg(eps_graph).recorder(macros.sec2nano(float(cfg.recorder_step_s))),
        "adcs_attitude": adcs_graph.rw_closed_loop.attitude_log,
        "adcs_rate": adcs_graph.rw_closed_loop.rate_log,
        "rw_speeds": adcs_graph.rw_closed_loop.rw_speed_log,
        "spacecraft_state": central_spacecraft.scStateOutMsg.recorder(macros.sec2nano(float(cfg.recorder_step_s))),
        "adcs_power": resource_feedback_bridges["adcs"].powerOutMsg.recorder(macros.sec2nano(float(cfg.recorder_step_s))),
        "pdu_heater_status": eps_graph.status_messages["heater"].recorder(macros.sec2nano(float(cfg.recorder_step_s))) if eps_graph.status_messages.get("heater") is not None else None,
    }
    if resource_feedback_bridges.get("propulsion") is not None:
        recorders["propulsion_power"] = resource_feedback_bridges["propulsion"].powerOutMsg.recorder(macros.sec2nano(float(cfg.recorder_step_s)))
    if orb_env_graph.eclipse_msg is not None:
        recorders["eclipse"] = orb_env_graph.eclipse_msg.recorder(macros.sec2nano(float(cfg.recorder_step_s)))
    if cfg.thermal_use_network and hasattr(thermal_graph, "network"):
        for heater_name, heater in getattr(thermal_graph.network, "heaters", {}).items():
            recorders[f"heater_{heater_name}"] = heater.heaterOutMsg.recorder(macros.sec2nano(float(cfg.recorder_step_s)))

    if native_downlink_link_budget_msg is not None:
        recorders["gated_downlink_link_budget"] = native_downlink_link_budget_msg.recorder(macros.sec2nano(float(cfg.recorder_step_s)))
    if native_downlink is not None:
        recorders["downlink_node"] = native_downlink.nodeDataOutMsg.recorder(macros.sec2nano(float(cfg.recorder_step_s)))
        recorders["downlink"] = native_downlink.downlinkOutMsg.recorder(macros.sec2nano(float(cfg.recorder_step_s)))
        recorders["conservation_downlink"] = native_downlink.downlinkOutMsg.recorder(macros.sec2nano(conservation_sample_s))
    if rf_access_graph is not None:
        recorders["ground_access"] = rf_access_graph.access_msg.recorder(macros.sec2nano(float(cfg.recorder_step_s)))
        recorders["rf_link_budget"] = rf_access_graph.link_budget_msg.recorder(macros.sec2nano(float(cfg.recorder_step_s)))
        recorders["spacecraft_antenna"] = rf_access_graph.spacecraft_antenna.antennaOutMsg.recorder(macros.sec2nano(float(cfg.recorder_step_s)))
        recorders["ground_antenna"] = rf_access_graph.ground_antenna.antennaOutMsg.recorder(macros.sec2nano(float(cfg.recorder_step_s)))

    if cfg.thermal_use_network and hasattr(thermal_graph, "network") and thermal_graph.network.nodes:
        for node_name, node in thermal_graph.network.nodes.items():
            rec_name = f"thermal_{node_name}"
            recorders[rec_name] = node.thermalStatusOutMsg.recorder(macros.sec2nano(float(cfg.recorder_step_s)))
            sim.AddModelToTask(recorder_task_name, recorders[rec_name])
    elif thermal_graph.nodes:
        for node_name, node in thermal_graph.nodes.items():
            rec_name = f"thermal_{node_name}"
            recorders[rec_name] = node.thermalStatusOutMsg.recorder(macros.sec2nano(float(cfg.recorder_step_s)))
            sim.AddModelToTask(recorder_task_name, recorders[rec_name])
    elif hasattr(thermal_graph.evaluator, "thermalStatusOutMsg"):
        recorders["thermal"] = thermal_graph.evaluator.thermalStatusOutMsg.recorder(macros.sec2nano(float(cfg.recorder_step_s)))
        sim.AddModelToTask(recorder_task_name, recorders["thermal"])

    if cfg.thermal_use_network and hasattr(thermal_graph, "network"):
        for node_name, bridge in getattr(thermal_graph.network, "power_bridges", {}).items():
            rec_name = f"thermal_power_bridge_{node_name}"
            recorders[rec_name] = bridge.heatOutMsg.recorder(macros.sec2nano(float(cfg.recorder_step_s)))
            sim.AddModelToTask(recorder_task_name, recorders[rec_name])
    if heater_feedback is not None:
        recorders["heater_eps_feedback"] = heater_feedback.powerOutMsg.recorder(macros.sec2nano(float(cfg.recorder_step_s)))
        sim.AddModelToTask(recorder_task_name, recorders["heater_eps_feedback"])

    if fuel_tank is not None:
        recorders["fuel_tank"] = fuel_tank.fuelTankOutMsg.recorder(macros.sec2nano(float(cfg.recorder_step_s)))
        sim.AddModelToTask(recorder_task_name, recorders["fuel_tank"])
    for rec_name in (
        "battery", "storage", "instrument", "transmitter", "solar_power", "gated_downlink_link_budget",
        "conservation_battery", "conservation_storage", "conservation_instrument", "conservation_downlink",
        "downlink_node", "downlink", "ground_access", "rf_link_budget",
        "spacecraft_antenna", "ground_antenna", "spacecraft_state", "adcs_power",
        "propulsion_power", "pdu_heater_status", "eclipse",
    ):
        rec = recorders.get(rec_name)
        if rec is not None:
            sim.AddModelToTask(recorder_task_name, rec)
    for rec_name, rec in recorders.items():
        if rec_name.startswith("heater_") and rec_name != "heater_eps_feedback" and rec is not None:
            sim.AddModelToTask(recorder_task_name, rec)

    component_registry: dict[str, Any] = {
        "battery": battery,
        "storage": storage,
        "comm_storage": storage,
        "transmitter": transmitter,
        "instrument": instrument,
        "payload_instrument": instrument,
        "mission_gate": mission_gate,
        "payload_gate": mission_gate,
        "comm_gate": mission_gate,
        "native_downlink": native_downlink,
        "native_downlink_link_budget_msg": native_downlink_link_budget_msg,
        "native_rf_access_graph": rf_access_graph,
        "eps_pdu": eps_graph.pdu,
        "payload_power_bridge": resource_feedback_bridges.get("payload"),
        "comm_power_bridge": resource_feedback_bridges.get("comm"),
        "adcs_power_bridge": resource_feedback_bridges.get("adcs"),
        "propulsion_power_bridge": resource_feedback_bridges.get("propulsion"),
        "rw_cluster": adcs_graph.rw_closed_loop.rw_state_effector,
        "reaction_wheel_bundle": adcs_graph.rw_closed_loop.rw_bundle,
    }
    if cfg.thermal_use_network and hasattr(thermal_graph, "network"):
        component_registry["thermal_network"] = thermal_graph.network
        for node_name, node in getattr(thermal_graph.network, "nodes", {}).items():
            component_registry[f"thermal_node.{node_name}"] = node
            component_registry[f"thermal_node_{node_name}"] = node
        for node_name, heater in getattr(thermal_graph.network, "heaters", {}).items():
            component_registry[f"heater.{node_name}"] = heater
            component_registry[f"thermal_heater_{node_name}"] = heater
            if node_name == "battery":
                component_registry["battery_heater"] = heater
        for node_name, radiator in getattr(thermal_graph.network, "radiators", {}).items():
            component_registry[f"radiator.{node_name}"] = radiator
            component_registry[f"thermal_radiator_{node_name}"] = radiator
        for node_name, bridge in getattr(thermal_graph.network, "power_bridges", {}).items():
            component_registry[f"thermal_power_bridge.{node_name}"] = bridge
        if heater_feedback is not None:
            component_registry["heater_eps_feedback"] = heater_feedback
    elif hasattr(thermal_graph, "nodes"):
        for node_name, node in thermal_graph.nodes.items():
            component_registry[f"thermal_node.{node_name}"] = node
            component_registry[f"thermal_node_{node_name}"] = node
            # The scheduled node owns the heater state internally.
            component_registry[f"heater.{node_name}"] = node
            component_registry[f"thermal_heater_{node_name}"] = node
            if node_name == "battery":
                component_registry["battery_heater"] = node
    for idx, _wheel in enumerate(getattr(adcs_graph.rw_closed_loop.config, "wheels", ())):
        component_registry[f"rw_{idx}"] = adcs_graph.rw_closed_loop.rw_state_effector
    if fuel_tank is not None:
        component_registry["fuel_tank"] = fuel_tank
    if propulsion_graph is not None:
        component_registry["thruster"] = propulsion_graph.thruster_effector
        thruster_data = getattr(propulsion_graph.thruster_effector, "thrusterData", None)
        if thruster_data is None:
            raise RuntimeError("propulsion thruster effector does not expose thrusterData for fault routing")
        for idx in range(len(thruster_data)):
            component_registry[f"thruster_{idx}"] = thruster_data[idx]

    if "thermal_heaters_to_eps_load" not in coupling_matrix:
        coupling_matrix["thermal_heaters_to_eps_load"] = _coupling_record(
            name="thermal_heaters_to_eps_load",
            source_subsystem="thermal",
            sink_subsystem="eps",
            coupling_status="not_coupled_no_heater_model",
            interface="heater status/output -> EPS load",
            evidence=("no heater feedback inputs were created for this thermal graph",),
        )

    fault_injector = None
    if cfg.fault_specs:
        fault_injector = FaultInjector(
            sim=sim,
            spacecraft=central_spacecraft,
            fault_specs=list(cfg.fault_specs),
            component_registry=component_registry,
        )
        fault_injector.schedule_all_faults()

    return WholeSpacecraftGraph(
        sim=sim,
        sim_task_name=sim_task_name,
        fsw_task_name=fsw_task_name,
        orb_env_task_name=orb_env_task_name,
        thermal_task_name=thermal_task_name,
        recorder_task_name=recorder_task_name,
        spacecraft=central_spacecraft,
        adcs_graph=adcs_graph,
        eps_graph=eps_graph,
        payload_graph=payload_graph,
        comm_graph=comm_graph,
        thermal_graph=thermal_graph,
        propulsion_graph=propulsion_graph,
        orb_env_graph=orb_env_graph,
        battery=battery,
        storage=storage,
        instrument=instrument,
        transmitter=transmitter,
        fuel_tank=fuel_tank,
        mission_gate=mission_gate,
        component_registry=component_registry,
        fault_injector=fault_injector,
        applied_modes={
            "adcs": {"degradation": cfg.adcs_degradation is not None, "fault_count": len(cfg.fault_specs)},
            "eps": {"degradation": cfg.eps_degradation is not None, "fault_count": len(cfg.fault_specs)},
            "payload": {"degradation": cfg.payload_degradation is not None, "fault_count": len(cfg.fault_specs), "runtime_effects": payload_effects},
            "comm_data": {"degradation": cfg.comm_data_degradation is not None, "fault_count": len(cfg.fault_specs), "runtime_effects": comm_effects},
            "thermal": {"degradation": cfg.thermal_degradation is not None, "fault_count": len(cfg.fault_specs)},
            "propulsion": {"degradation": cfg.propulsion_degradation is not None, "fault_count": len(cfg.fault_specs)},
            "integration": {
                "coupling_count": len(coupling_matrix),
                "task_cadence_s": {
                    "dynamics": float(cfg.adcs_dyn_step_s),
                    "fsw": float(cfg.adcs_fsw_step_s),
                    "orbit_environment": float(cfg.orb_env_step_s),
                    "thermal": float(cfg.thermal_step_s),
                    "recorder": float(cfg.recorder_step_s),
                    "recorder_task": float(recorder_task_step_s),
                },
                "coupling_source": "WholeSpacecraftConfig.coupling -> WholeSpacecraftGraph.coupling_matrix",
                "deprecated_contract_manifest": "compatibility_only",
            },
            "spacecraft": {"owner": "whole_spacecraft.builder", "attached_subsystems": ("adcs", "propulsion", "orbit_environment")},
        },
        recorders=recorders,
        coupling_matrix=coupling_matrix,
        integration_manifest=_compat_manifest_from_coupling_matrix(coupling_matrix),
        task_cadence_probes=task_cadence_probes,
    )


__all__ = [
    "WholeSpacecraftConfig",
    "WholeSpacecraftGraph",
    "WholeSpacecraftMissionGate",
    "build_whole_spacecraft_bus",
    "build_whole_spacecraft_graph",
]
