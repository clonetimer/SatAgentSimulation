"""Comm/Data subsystem builder."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from components.antenna.builder import (
    build_nominal_antenna_config,
    build_simple_antenna_native,
    _antenna_state_value,
    seed_simple_antenna_environment,
)
from components.data_queue.builder import build_nominal_data_queue_config, build_simple_storage_unit
from components.ground_station.builder import (
    build_nominal_ground_station_config,
    build_ground_location,
)
from components.link_budget.builder import (
    build_simple_transmitter,
    build_nominal_link_budget_config,
    build_link_budget_native,
    wire_link_budget_antennas,
)
from components.onboard_storage.builder import build_nominal_onboard_storage_config
from components.transmitter.builder import (
    build_sspa_transmitter_config,
    build_twt_transmitter_config,
)

from .schemas import CommDataBasiliskConfig, CommDataConfig, CommDataStepInput
from components.antenna.schemas import SimpleAntennaNativeConfig
from components.ground_station.schemas import GroundAccessNativeConfig
from components.link_budget.schemas import LinkBudgetNativeConfig


@dataclass(frozen=True)
class CommDataBasiliskAssemblyGraph:
    storage: Any
    transmitter: Any


@dataclass(frozen=True)
class CommDataNativeRfAccessGraph:
    """Native RF/access chain owned by the Comm/Data subsystem.

    Whole-spacecraft may request this graph but does not directly create the
    ground station, antennas, or link-budget module.
    """

    ground_location: Any
    spacecraft_antenna: Any
    ground_antenna: Any
    link_budget: Any
    access_msg: Any
    link_budget_msg: Any
    antenna_state_msgs: tuple[Any, ...] = ()
    environment_wiring: tuple[str, ...] = ()


def build_nominal_comm_data_config() -> CommDataConfig:
    """Return a deterministic Comm/Data config for subsystem tests and demos."""

    return CommDataConfig(
        queue=build_nominal_data_queue_config(capacity_bits=10_000.0),
        link=build_nominal_link_budget_config(raw_rate_bps=1_000.0, tx_power_w=5.0, tx_gain_dbi=20.0, rx_gain_dbi=20.0, downlink_eff=1.0),
        ground_station=build_nominal_ground_station_config(min_elevation_deg=5.0, max_range_m=2_000_000.0),
        transmitter=build_sspa_transmitter_config(
            max_power_w=5.0,
            efficiency=0.45,
            gain_db=35.0,
        ),
        station_pos_ecef_m=(6_378_000.0, 0.0, 0.0),
        generated_bps_by_mode={
            "safePoint": 10.0,
            "nominal": 50.0,
            "payload": 500.0,
            "downlink": 20.0,
        },
        default_generated_bps=20.0,
        max_downlink_bps=800.0,
        require_access_for_downlink=True,
        require_eps_permission=True,
    )


def build_nominal_comm_data_profile(dt_s: float = 10.0) -> tuple[CommDataStepInput, ...]:
    """Build a deterministic access/no-access and mode-changing profile."""

    earth_r = 6_378_000.0
    overhead = (earth_r + 500_000.0, 0.0, 0.0)
    out_of_access = (-earth_r - 500_000.0, 0.0, 0.0)
    far_overhead = (earth_r + 3_000_000.0, 0.0, 0.0)
    steps: list[CommDataStepInput] = []
    # Generate payload data with no access: queue should grow.
    steps.extend(CommDataStepInput(dt_s=dt_s, mode="payload", spacecraft_pos_ecef_m=out_of_access) for _ in range(4))
    # Access exists and EPS permits downlink: queue should drain or grow more slowly.
    steps.extend(CommDataStepInput(dt_s=dt_s, mode="downlink", spacecraft_pos_ecef_m=overhead) for _ in range(4))
    # Access exists geometrically but EPS denies downlink: queue should grow.
    steps.extend(CommDataStepInput(dt_s=dt_s, mode="payload", spacecraft_pos_ecef_m=overhead, eps_allows_downlink=False) for _ in range(2))
    # Far overhead exceeds max range: no access, queue grows.
    steps.extend(CommDataStepInput(dt_s=dt_s, mode="nominal", spacecraft_pos_ecef_m=far_overhead) for _ in range(2))
    return tuple(steps)


def build_comm_data_context(cfg: CommDataConfig | None = None, backend: str = "basilisk") -> tuple[CommDataConfig, dict[str, object]]:
    """Build a Comm/Data subsystem context.

    The old subsystem Python runner path has been removed. This entry point now
    returns a Basilisk-native subsystem summary.
    """

    cfg = cfg or build_nominal_comm_data_config()
    if backend != "basilisk":
        raise ValueError("Comm/Data Python backend has been removed; use backend='basilisk'")
    from .schemas import CommDataBasiliskConfig
    from .runner import run_comm_data_basilisk_scenario

    summary, _ = run_comm_data_basilisk_scenario(
        CommDataBasiliskConfig(
            instrument_baud_bps=float(max(cfg.generated_bps_by_mode.values(), default=cfg.default_generated_bps)),
            storage_capacity_bits=float(cfg.queue.capacity_bits),
            transmitter_baud_bps=float(cfg.max_downlink_bps or cfg.link.raw_rate_bps),
            native_storage_drain_enabled=True,
        )
    )
    return cfg, {
        "backend": summary.backend,
        "status": summary.status,
        "sample_count": summary.sample_count,
        "final_storage_bits": summary.final_storage_bits,
        "estimated_native_downlinked_bits": summary.estimated_native_downlinked_bits,
        "native_storage_drain_enabled": summary.native_storage_drain_enabled,
    }


def build_comm_data_basilisk_assembly_graph(
    cfg: CommDataConfig | None = None,
    *,
    storage_capacity_bits: float | None = None,
    initial_bits: float = 0.0,
    transmitter_baud_bps: float | None = None,
    storage_model_tag: str = "CommDataSubsystemStorage",
    transmitter_model_tag: str = "CommDataSubsystemTransmitter",
    transmitter_data_name: str = "downlink",
) -> CommDataBasiliskAssemblyGraph:
    cfg = cfg or build_nominal_comm_data_config()
    storage = build_simple_storage_unit(
        storage_model_tag,
        storage_capacity_bits if storage_capacity_bits is not None else float(cfg.queue.capacity_bits),
        initial_bits,
    )
    transmitter = build_simple_transmitter(
        transmitter_model_tag,
        transmitter_baud_bps if transmitter_baud_bps is not None else float(cfg.max_downlink_bps or cfg.link.raw_rate_bps),
        transmitter_data_name,
    )
    return CommDataBasiliskAssemblyGraph(storage=storage, transmitter=transmitter)


def attach_comm_data_basilisk_graph_to_task(sim: Any, task_name: str, graph: CommDataBasiliskAssemblyGraph) -> None:
    sim.AddModelToTask(task_name, graph.storage)
    sim.AddModelToTask(task_name, graph.transmitter)


def build_comm_data_config(
    amp_type: Literal["sspa", "twt"] = "sspa",
    *,
    tx_power_w: float | None = None,
    efficiency: float | None = None,
    gain_db: float | None = None,
) -> CommDataConfig:
    """Build a CommDataConfig with specified RF power amplifier type.

    Args:
        amp_type: "sspa" (default, low-power solid-state) or "twt" (high-power tube)
        tx_power_w: Override max TX power (auto-selected based on type if None)
        efficiency: Override efficiency (auto-selected based on type if None)
        gain_db: Override gain (auto-selected based on type if None)

    Returns:
        CommDataConfig with transmitter configured for the chosen amplifier type
    """
    if amp_type == "twt":
        transmitter = build_twt_transmitter_config(
            max_power_w=tx_power_w or 100.0,
            efficiency=efficiency or 0.65,
            gain_db=gain_db or 50.0,
        )
    else:
        transmitter = build_sspa_transmitter_config(
            max_power_w=tx_power_w or 5.0,
            efficiency=efficiency or 0.45,
            gain_db=gain_db or 35.0,
        )

    cfg = build_nominal_comm_data_config()

    from dataclasses import replace
    cfg = replace(cfg, transmitter=transmitter)
    return cfg



@dataclass(frozen=True)
class CommDataBasiliskSimContext:
    """Built Comm/Data Basilisk simulation graph; execution is left to runner.py."""

    subsystem: str
    config: "CommDataBasiliskConfig"
    simulation: Any
    process: Any
    task: Any
    task_name: str
    modules: dict[str, Any]
    recorders: dict[str, Any]
    message_handles: dict[str, Any]
    base_parameters: dict[str, Any]
    component_sources: dict[str, str]


def _require_comm_data_basilisk() -> None:
    try:
        from Basilisk.utilities import SimulationBaseClass, macros  # noqa: F401
        from Basilisk.simulation import simpleInstrument, simpleStorageUnit, simpleTransmitter  # noqa: F401
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Basilisk Comm/Data modules are unavailable: {exc}") from exc


def build_comm_data_basilisk_sim(config: "CommDataBasiliskConfig | None" = None) -> CommDataBasiliskSimContext:
    """Build the Comm/Data Basilisk simulation context without executing it."""

    from .schemas import CommDataBasiliskConfig

    _require_comm_data_basilisk()
    cfg = config or CommDataBasiliskConfig()
    if cfg.step_s <= 0 or cfg.duration_s <= 0:
        raise ValueError("duration_s and step_s must be positive")

    from Basilisk.utilities import SimulationBaseClass, macros
    from components.payload.builder import build_simple_instrument
    from components.data_queue.builder import build_simple_storage_unit
    from components.link_budget.builder import build_simple_transmitter

    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("commDataBasiliskProcess")
    task_name = "commDataBasiliskTask"
    task = sim.CreateNewTask(task_name, macros.sec2nano(float(cfg.step_s)))
    process.addTask(task)

    data_name = str(cfg.data_name)
    instrument = build_simple_instrument("commDataPayloadInstrument", cfg.instrument_baud_bps, data_name)
    storage = build_simple_storage_unit("commDataStorageUnit", cfg.storage_capacity_bits, cfg.initial_storage_bits)
    storage.addDataNodeToModel(instrument.nodeDataOutMsg)
    transmitter = build_simple_transmitter("commDataTransmitter", cfg.transmitter_baud_bps, data_name)
    transmitter_storage_node_baud = float(cfg.transmitter_baud_bps)
    try:
        transmitter.addStorageUnitToTransmitter(storage.storageUnitDataOutMsg)
    except Exception as exc:
        raise RuntimeError(
            "Comm/Data native wiring failed: transmitter could not subscribe to "
            "the storage status message"
        ) from exc
    if cfg.native_storage_drain_enabled:
        transmitter.nodeBaudRate = -abs(float(cfg.transmitter_baud_bps))
        transmitter_storage_node_baud = float(transmitter.nodeBaudRate)
        storage.addDataNodeToModel(transmitter.nodeDataOutMsg)

    for model in [instrument, storage, transmitter]:
        sim.AddModelToTask(task_name, model)

    period = macros.sec2nano(float(cfg.step_s))
    inst_rec = instrument.nodeDataOutMsg.recorder(period)
    storage_rec = storage.storageUnitDataOutMsg.recorder(period)
    tx_rec = transmitter.nodeDataOutMsg.recorder(period)
    for rec in [inst_rec, storage_rec, tx_rec]:
        sim.AddModelToTask(task_name, rec)

    return CommDataBasiliskSimContext(
        subsystem="comm_data",
        config=cfg,
        simulation=sim,
        process=process,
        task=task,
        task_name=task_name,
        modules={
            "instrument": instrument,
            "storage": storage,
            "transmitter": transmitter,
        },
        recorders={"instrument": inst_rec, "storage": storage_rec, "transmitter": tx_rec},
        message_handles={
            "instrument_node_data_out": instrument.nodeDataOutMsg,
            "storage_unit_data_out": storage.storageUnitDataOutMsg,
            "transmitter_node_data_out": transmitter.nodeDataOutMsg,
        },
        base_parameters={
            "data_name": data_name,
            "transmitter_storage_node_baud_bps": transmitter_storage_node_baud,
            "period_ns": period,
            "antenna_config": build_nominal_antenna_config(),
            "ground_station_config": build_nominal_ground_station_config(),
            "onboard_storage_config": build_nominal_onboard_storage_config(),
            "transmitter_config": build_sspa_transmitter_config(),
        },
        component_sources={
            "antenna": "components.antenna.builder.build_nominal_antenna_config",
            "transmitter": "components.transmitter.builder.build_sspa_transmitter_config",
            "link_budget": "components.link_budget.builder.build_simple_transmitter",
            "ground_station": "components.ground_station.builder.build_nominal_ground_station_config",
            "data_queue": "components.data_queue.builder.build_simple_storage_unit",
            "onboard_storage": "components.onboard_storage.builder.build_nominal_onboard_storage_config",
            "payload": "components.payload.builder.build_simple_instrument",
        },
    )


# ---------------------------------------------------------------------------
# Whole-spacecraft native RF/access builder hook
# ---------------------------------------------------------------------------

def build_comm_data_native_rf_access_chain(
    *,
    sc_state_msg: Any,
    ground_config: GroundAccessNativeConfig | None = None,
    spacecraft_antenna_config: SimpleAntennaNativeConfig | None = None,
    ground_antenna_config: SimpleAntennaNativeConfig | None = None,
    link_budget_config: LinkBudgetNativeConfig | None = None,
    sun_msg: Any | None = None,
    planet_msgs: tuple[Any, ...] = (),
    eclipse_msg: Any | None = None,
) -> CommDataNativeRfAccessGraph:
    """Build the native ground-access and RF-chain modules for integration.

    The function belongs to the Comm/Data subsystem layer.  It combines
    component-owned factories for ``groundLocation``, ``simpleAntenna`` and
    ``linkBudget`` so the whole-spacecraft layer can use a subsystem boundary
    rather than directly creating communication components.
    """

    gs_cfg = ground_config or GroundAccessNativeConfig(
        minimum_elevation_rad=0.0,
        maximum_range_m=2_500_000.0,
    )
    ground = build_ground_location("wholeSpacecraftGroundLocation", gs_cfg)
    ground.addSpacecraftToModel(sc_state_msg)

    sc_ant_cfg = spacecraft_antenna_config or SimpleAntennaNativeConfig(
        model_tag="wholeSpacecraftSpaceAntenna",
        antenna_name="spacecraft",
        directivity_db=20.0,
        tx_power_w=5.0,
        rx_power_w=1.0,
        position_b_m=(0.0, 0.0, 0.0),
        operating_mode="tx",
    )
    gd_ant_cfg = ground_antenna_config or SimpleAntennaNativeConfig(
        model_tag="wholeSpacecraftGroundAntenna",
        antenna_name="ground",
        directivity_db=20.0,
        tx_power_w=5.0,
        rx_power_w=1.0,
        position_b_m=(0.0, 0.0, 0.0),
        operating_mode="rx",
    )
    spacecraft_antenna = build_simple_antenna_native(sc_ant_cfg)
    ground_antenna = build_simple_antenna_native(gd_ant_cfg)
    spacecraft_antenna.scStateInMsg.subscribeTo(sc_state_msg)
    ground_antenna.groundStateInMsg.subscribeTo(ground.currentGroundStateOutMsg)
    seed_simple_antenna_environment(
        spacecraft_antenna, "space", operating_mode=sc_ant_cfg.operating_mode, available=sc_ant_cfg.available
    )
    seed_simple_antenna_environment(
        ground_antenna, "ground", operating_mode=gd_ant_cfg.operating_mode, available=gd_ant_cfg.available
    )

    # Close the native SimpleAntenna environment inputs instead of relying on
    # its uniform-background fallback.  The explicit state messages also remove
    # ambiguity between setter state and message-driven state at reset.
    from Basilisk.architecture import messaging

    state_msgs: list[Any] = []
    environment_wiring: list[str] = []
    antenna_configs = (("spacecraft", spacecraft_antenna, sc_ant_cfg), ("ground", ground_antenna, gd_ant_cfg))
    for label, antenna, antenna_cfg in antenna_configs:
        state_payload = messaging.AntennaStateMsgPayload()
        state_payload.antennaState = _antenna_state_value(antenna_cfg.operating_mode, available=antenna_cfg.available)
        state_msg = messaging.AntennaStateMsg().write(state_payload)
        antenna.antennaSetStateInMsg.subscribeTo(state_msg)
        state_msgs.append(state_msg)
        environment_wiring.append(f"{label}.antennaSetStateInMsg")
        if sun_msg is not None:
            antenna.sunInMsg.subscribeTo(sun_msg)
            environment_wiring.append(f"{label}.sunInMsg")
        if eclipse_msg is not None:
            antenna.sunEclipseInMsg.subscribeTo(eclipse_msg)
            environment_wiring.append(f"{label}.sunEclipseInMsg")
        for planet_msg in planet_msgs:
            antenna.addPlanetToModel(planet_msg)
        if planet_msgs:
            environment_wiring.append(f"{label}.planetInMsgs[{len(planet_msgs)}]")

    link_budget = build_link_budget_native(link_budget_config or LinkBudgetNativeConfig(model_tag="wholeSpacecraftNativeLinkBudget"))
    wire_link_budget_antennas(link_budget, spacecraft_antenna, ground_antenna)

    return CommDataNativeRfAccessGraph(
        ground_location=ground,
        spacecraft_antenna=spacecraft_antenna,
        ground_antenna=ground_antenna,
        link_budget=link_budget,
        access_msg=ground.accessOutMsgs[0],
        link_budget_msg=link_budget.linkBudgetOutPayload,
        antenna_state_msgs=tuple(state_msgs),
        environment_wiring=tuple(environment_wiring),
    )


def build_whole_spacecraft_native_rf_access_chain(
    *,
    sc_state_msg: Any,
    frequency_hz: float,
    bandwidth_hz: float,
    spacecraft_orientation_b: tuple[float, float, float],
    pointing_loss_enabled: bool,
    frequency_loss_enabled: bool,
    atmospheric_attenuation_enabled: bool,
    sun_msg: Any | None = None,
    planet_msgs: tuple[Any, ...] = (),
    eclipse_msg: Any | None = None,
) -> CommDataNativeRfAccessGraph:
    """Build the recommended whole-spacecraft native RF chain.

    Component schemas remain private to the Comm/Data subsystem boundary; the
    whole-spacecraft layer supplies only mission-level RF parameters.
    """

    return build_comm_data_native_rf_access_chain(
        sc_state_msg=sc_state_msg,
        spacecraft_antenna_config=SimpleAntennaNativeConfig(
            model_tag="wholeSpacecraftSpaceAntenna",
            antenna_name="spacecraft",
            frequency_hz=float(frequency_hz),
            bandwidth_hz=float(bandwidth_hz),
            directivity_db=20.0,
            tx_power_w=5.0,
            rx_power_w=1.0,
            orientation_b=tuple(float(x) for x in spacecraft_orientation_b),
            operating_mode="tx",
        ),
        ground_antenna_config=SimpleAntennaNativeConfig(
            model_tag="wholeSpacecraftGroundAntenna",
            antenna_name="ground",
            frequency_hz=float(frequency_hz),
            bandwidth_hz=float(bandwidth_hz),
            directivity_db=20.0,
            tx_power_w=5.0,
            rx_power_w=1.0,
            operating_mode="rx",
        ),
        link_budget_config=LinkBudgetNativeConfig(
            model_tag="wholeSpacecraftNativeLinkBudget",
            pointing_loss_enabled=bool(pointing_loss_enabled),
            frequency_loss_enabled=bool(frequency_loss_enabled),
            atmospheric_attenuation_enabled=bool(atmospheric_attenuation_enabled),
        ),
        sun_msg=sun_msg,
        planet_msgs=planet_msgs,
        eclipse_msg=eclipse_msg,
    )


# ---------------------------------------------------------------------------
# Whole-spacecraft native ODH builder hook
# ---------------------------------------------------------------------------

def build_comm_data_native_odh_downlink(
    *,
    storage: Any,
    data_name: str,
    bit_rate_request_bps: float,
    packet_size_bits: float,
    max_retransmissions: int,
    link_active: bool = True,
    cnr_linear: float = 1.0e9,
    distance_m: float = 500_000.0,
    bandwidth_hz: float = 1.0e6,
    frequency_hz: float = 2.2e9,
    link_budget_msg: Any | None = None,
):
    """Build a Basilisk DownlinkHandling storage-drain chain for integration.

    This function lives in the Comm/Data subsystem layer so whole-spacecraft
    code can request the native ODH chain without directly importing Basilisk
    communication modules or reimplementing component physics.
    """
    from Basilisk.architecture import messaging
    from Basilisk.simulation import downlinkHandling

    if packet_size_bits <= 0.0:
        raise ValueError("packet_size_bits must be positive")
    if max_retransmissions < 1:
        raise ValueError("max_retransmissions must be >= 1")

    if link_budget_msg is None:
        link_payload = messaging.LinkBudgetMsgPayload()
        link_payload.antennaName1 = "spacecraft"
        link_payload.antennaName2 = "ground"
        link_payload.antennaState1 = _antenna_state_value("tx", available=bool(link_active))
        link_payload.antennaState2 = _antenna_state_value("rx", available=bool(link_active))
        link_payload.CNR1 = max(0.0, float(cnr_linear)) if link_active else 0.0
        link_payload.CNR2 = max(0.0, float(cnr_linear)) if link_active else 0.0
        link_payload.distance = max(0.0, float(distance_m))
        link_payload.bandwidth = max(0.0, float(bandwidth_hz))
        link_payload.frequency = max(0.0, float(frequency_hz))
        link_msg = messaging.LinkBudgetMsg().write(link_payload)
    else:
        link_msg = link_budget_msg

    downlink = downlinkHandling.DownlinkHandling()
    downlink.ModelTag = "wholeSpacecraftNativeDownlinkHandling"
    downlink.nodeDataName = str(data_name)
    downlink.setBitRateRequest(max(0.0, float(bit_rate_request_bps)))
    downlink.setPacketSizeBits(float(packet_size_bits))
    downlink.setMaxRetransmissions(int(max_retransmissions))
    downlink.setReceiverAntenna(2)
    downlink.setRequireFullPacket(False)
    downlink.setRemovalPolicy(downlinkHandling.DownlinkHandling.RemovalPolicy_REMOVE_DELIVERED_ONLY)
    downlink.linkBudgetInMsg.subscribeTo(link_msg)
    downlink.addStorageUnitToDownlink(storage.storageUnitDataOutMsg)
    storage.addDataNodeToModel(downlink.nodeDataOutMsg)
    return downlink, link_msg
