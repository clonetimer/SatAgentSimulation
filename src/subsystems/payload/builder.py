"""Payload subsystem builder."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from components.payload.builder import build_nominal_payload_config
from components.payload.builder import build_simple_instrument
from .schemas import PayloadBasiliskConfig, PayloadSubsystemConfig


@dataclass(frozen=True)
class PayloadBasiliskAssemblyGraph:
    instrument: Any


def build_nominal_payload_subsystem_config() -> PayloadSubsystemConfig:
    return PayloadSubsystemConfig(
        instrument=build_nominal_payload_config(
            name="nadir_imager",
            observation_power_w=18.0,
            standby_power_w=3.0,
            data_rate_bps=250_000.0,
            heat_fraction=0.85,
            max_pointing_error_deg=0.25,
            allowed_modes=("observation", "payload", "NOMINAL_OBSERVATION"),
        ),
        mode_data_rate_scale={"observation": 1.0, "payload": 1.0, "NOMINAL_OBSERVATION": 1.0},
    )


def build_nominal_payload_subsystem_profile(dt_s: float = 1.0) -> tuple:

    from .schemas import PayloadSubsystemStepInput

    return (
        PayloadSubsystemStepInput(dt_s=dt_s, mode="observation", pointing_error_deg=0.03),
        PayloadSubsystemStepInput(dt_s=dt_s, mode="observation", pointing_error_deg=0.10),
        PayloadSubsystemStepInput(dt_s=dt_s, mode="observation", pointing_error_deg=0.35),
        PayloadSubsystemStepInput(dt_s=dt_s, mode="observation", eps_allows_payload=False),
        PayloadSubsystemStepInput(dt_s=dt_s, mode="observation", thermal_allows_payload=False),
        PayloadSubsystemStepInput(dt_s=dt_s, mode="observation", adcs_pointing_ready=False),
        PayloadSubsystemStepInput(dt_s=dt_s, mode="safePoint", pointing_error_deg=0.03),
        PayloadSubsystemStepInput(dt_s=dt_s, mode="observation", pointing_error_deg=0.03),
    )


def build_payload_context(cfg: PayloadSubsystemConfig | None = None, backend: str = "basilisk") -> tuple[PayloadSubsystemConfig, dict[str, object]]:
    cfg = cfg or build_nominal_payload_subsystem_config()
    if backend != "basilisk":
        raise ValueError("Payload Python backend has been removed; use backend='basilisk'")
    from components.payload.basilisk_impl import run_basilisk_payload_smoke

    result = run_basilisk_payload_smoke(cfg)
    return cfg, {
        "backend": result.backend,
        "available": result.available,
        "status": result.status,
        "modules": result.modules,
        "summary": result.summary,
        "note": result.note,
    }


def build_payload_basilisk_assembly_graph(
    cfg: PayloadSubsystemConfig | None = None,
    *,
    instrument_baud_bps: float | None = None,
    data_name: str = "payload_science",
    model_tag: str = "PayloadSubsystemInstrument",
) -> PayloadBasiliskAssemblyGraph:
    cfg = cfg or build_nominal_payload_subsystem_config()
    instrument = build_simple_instrument(
        model_tag,
        instrument_baud_bps if instrument_baud_bps is not None else float(cfg.instrument.data_rate_bps),
        data_name,
    )
    return PayloadBasiliskAssemblyGraph(instrument=instrument)


def attach_payload_basilisk_graph_to_task(sim: Any, task_name: str, graph: PayloadBasiliskAssemblyGraph) -> None:
    sim.AddModelToTask(task_name, graph.instrument)

@dataclass(frozen=True)
class PayloadBasiliskSimContext:
    """Built payload Basilisk simulation graph; execution is left to runner.py."""

    subsystem: str
    config: "PayloadBasiliskConfig"
    simulation: Any
    process: Any
    task: Any
    task_name: str
    modules: dict[str, Any]
    recorders: dict[str, Any]
    message_handles: dict[str, Any]
    base_parameters: dict[str, Any]
    component_sources: dict[str, str]


def _require_payload_basilisk() -> None:
    try:
        from Basilisk.utilities import SimulationBaseClass, macros  # noqa: F401
        from Basilisk.simulation import simpleInstrument  # noqa: F401
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Basilisk payload modules are unavailable: {exc}") from exc


def build_payload_basilisk_sim(config: "PayloadBasiliskConfig | None" = None) -> PayloadBasiliskSimContext:
    """Build the payload Basilisk simulation context without executing it.

    The instrument module is created through ``components.payload.builder`` so
    the subsystem builder remains an assembly layer rather than a duplicate
    component implementation.
    """

    from .schemas import PayloadBasiliskConfig
    from Basilisk.utilities import SimulationBaseClass, macros

    _require_payload_basilisk()
    cfg = config or PayloadBasiliskConfig()
    if cfg.step_s <= 0 or cfg.duration_s <= 0:
        raise ValueError("duration_s and step_s must be positive")

    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("payloadBasiliskProcess")
    task_name = "payloadBasiliskTask"
    task = sim.CreateNewTask(task_name, macros.sec2nano(float(cfg.step_s)))
    process.addTask(task)

    instrument = build_simple_instrument(
        "payloadInstrument",
        float(cfg.instrument_baud_bps),
        str(cfg.data_name),
    )
    sim.AddModelToTask(task_name, instrument)

    period = macros.sec2nano(float(cfg.step_s))
    instrument_rec = instrument.nodeDataOutMsg.recorder(period)
    sim.AddModelToTask(task_name, instrument_rec)

    return PayloadBasiliskSimContext(
        subsystem="payload",
        config=cfg,
        simulation=sim,
        process=process,
        task=task,
        task_name=task_name,
        modules={
            "instrument": instrument,
        },
        recorders={"instrument": instrument_rec},
        message_handles={"instrument_node_data_out": instrument.nodeDataOutMsg},
        base_parameters={
            "period_ns": period,
            "data_name": str(cfg.data_name),
            "subsystem_config": build_nominal_payload_subsystem_config(),
        },
        component_sources={
            "payload": "components.payload.builder.build_simple_instrument",
            "payload_sensor": "components.payload_sensor.builder.build_nominal_payload_sensor_config",
            "onboard_storage": "components.onboard_storage.builder.build_nominal_onboard_storage_config",
            "data_queue": "components.data_queue.builder.build_nominal_data_queue_config",
        },
    )

