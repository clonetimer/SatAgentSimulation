"""Basilisk helpers for the battery component."""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic
from .schemas import BatteryNativeConfig

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from .model import (
    apply_battery_config_faults,
    build_nominal_battery_config,
    initialize_battery,
    simulate_battery_power_profile,
    step_battery,
)
from .schemas import BatteryConfig, BatteryProfileResult, BatteryState


_messaging = None
_sysModel = None

try:
    from Basilisk.architecture import messaging, sysModel

    _messaging = messaging
    _sysModel = sysModel
except ImportError as exc:
    record_runtime_diagnostic(
        code='OPTIONAL_DEPENDENCY_IMPORT_UNAVAILABLE',
        category=DiagnosticCategory.OPTIONAL_DEPENDENCY_PROBE,
        location='src/components/battery/builder.py:<module>:01',
        exception=exc,
        strict=False,
    )




@dataclass(frozen=True)
class BatteryNativeTraceRow:
    time_s: float
    storage_j: float
    capacity_j: float
    soc: float
    net_power_w: float


@dataclass(frozen=True)
class BatteryNativeSummary:
    backend: str
    component: str
    basilisk_simbase_used: bool
    execute_simulation_used: bool
    native_modules: tuple[str, ...]
    sample_count: int
    initial_storage_j: float
    final_storage_j: float
    expected_final_storage_j: float
    status: str


def basilisk_available() -> bool:
    return _messaging is not None and _sysModel is not None


def require_basilisk_battery() -> None:
    try:
        from Basilisk.utilities import SimulationBaseClass, macros  # noqa: F401
        from Basilisk.simulation import simpleBattery, simplePowerSink  # noqa: F401
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Basilisk battery modules are unavailable: {exc}") from exc


def build_simple_battery(
    model_tag: str,
    capacity_wh: float,
    initial_soc: float,
    discharge_efficiency: float = 1.0,
    charge_efficiency: float = 1.0,
):
    discharge_efficiency = float(discharge_efficiency)
    charge_efficiency = float(charge_efficiency)
    if abs(discharge_efficiency - 1.0) > 1e-12 or abs(charge_efficiency - 1.0) > 1e-12:
        raise ValueError(
            "Basilisk SimpleBattery 2.11.0 does not expose charge/discharge "
            "efficiency parameters; use unity values for the native builder or "
            "an explicitly labelled project battery proxy"
        )

    require_basilisk_battery()
    from Basilisk.simulation import simpleBattery

    battery = simpleBattery.SimpleBattery()
    battery.ModelTag = model_tag
    battery.storageCapacity = max(0.0, float(capacity_wh)) * 3600.0
    battery.storedCharge_Init = battery.storageCapacity * max(0.0, min(1.0, float(initial_soc)))
    return battery


def attach_power_nodes_to_battery(battery, node_msgs: Iterable[object]) -> None:
    for msg in node_msgs:
        battery.addPowerNodeToModel(msg)


def run_battery_native_case(config: BatteryNativeConfig | None = None) -> tuple[BatteryNativeSummary, tuple[BatteryNativeTraceRow, ...]]:
    cfg = config or BatteryNativeConfig()
    if cfg.duration_s <= 0:
        raise ValueError("duration_s must be positive")
    if cfg.step_s <= 0:
        raise ValueError("step_s must be positive")
    if cfg.capacity_wh <= 0:
        raise ValueError("capacity_wh must be positive")
    if cfg.initial_soc < 0 or cfg.initial_soc > 1:
        raise ValueError("initial_soc must be between 0 and 1")
    if cfg.discharge_efficiency <= 0 or cfg.discharge_efficiency > 1:
        raise ValueError("discharge_efficiency must be between 0 and 1")
    if cfg.charge_efficiency <= 0 or cfg.charge_efficiency > 1:
        raise ValueError("charge_efficiency must be between 0 and 1")

    require_basilisk_battery()
    from Basilisk.utilities import SimulationBaseClass, macros
    from components.power_sink.builder import build_simple_power_sink

    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("batteryComponentNativeProcess")
    task_name = "batteryComponentNativeTask"
    process.addTask(sim.CreateNewTask(task_name, macros.sec2nano(float(cfg.step_s))))

    battery = build_simple_battery(
        "componentSimpleBattery",
        cfg.capacity_wh,
        cfg.initial_soc,
        discharge_efficiency=cfg.discharge_efficiency,
        charge_efficiency=cfg.charge_efficiency,
    )
    fault_msg = write_battery_capacity_fault_message(cfg.fault_capacity_ratio)
    attach_battery_capacity_fault_message(battery, fault_msg)
    nodes = []
    for idx, power_w in enumerate(cfg.power_nodes_w):
        node = build_simple_power_sink(f"componentBatteryPowerNode{idx}", power_w)
        nodes.append(node)
        sim.AddModelToTask(task_name, node)
    attach_power_nodes_to_battery(battery, [n.nodePowerOutMsg for n in nodes])
    sim.AddModelToTask(task_name, battery)
    rec = battery.batPowerOutMsg.recorder(macros.sec2nano(float(cfg.step_s)))
    sim.AddModelToTask(task_name, rec)
    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(float(cfg.duration_s)))
    sim.ExecuteSimulation()

    net_power = sum(float(x) for x in cfg.power_nodes_w)
    rows = []
    for i, t_ns in enumerate(list(rec.times())):
        time_s = float(t_ns) * macros.NANO2SEC
        capacity_j = float(rec.storageCapacity[i])
        storage_j = float(rec.storageLevel[i])
        rows.append(BatteryNativeTraceRow(time_s, storage_j, capacity_j, storage_j / capacity_j if capacity_j else 0.0, net_power))
    initial = cfg.capacity_wh * 3600.0 * max(0.0, min(1.0, cfg.initial_soc))
    expected = max(0.0, min(cfg.capacity_wh * 3600.0, initial + net_power * cfg.duration_s))
    final = rows[-1].storage_j if rows else initial
    status = "PASS" if rows and abs(final - expected) <= max(1e-3, abs(expected) * 1e-9) else "FAIL"
    return (
        BatteryNativeSummary(
            "basilisk_native_component",
            "battery",
            True,
            True,
            ("simpleBattery.SimpleBattery", "simplePowerSink.SimplePowerSink"),
            len(rows),
            initial,
            final,
            expected,
            status,
        ),
        tuple(rows),
    )


def write_battery_native_dataset(output_dir: str | Path, config: BatteryNativeConfig | None = None) -> dict[str, str]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary, rows = run_battery_native_case(config)
    summary_path = output_dir / "battery_native_summary.json"
    trace_path = output_dir / "battery_native_trace.csv"
    manifest_path = output_dir / "battery_native_manifest.json"
    summary_path.write_text(json.dumps(asdict(summary), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with trace_path.open("w", newline="", encoding="utf-8") as f:
        fields = list(asdict(rows[0]).keys()) if rows else list(BatteryNativeTraceRow.__annotations__.keys())
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    manifest = {
        "dataset_type": "basilisk_native_component_battery",
        "backend_truth": "Basilisk simpleBattery component runner",
        "files": {"summary": summary_path.name, "trace": trace_path.name},
        "summary": asdict(summary),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"summary": str(summary_path), "trace": str(trace_path), "manifest": str(manifest_path)}


def write_battery_capacity_fault_message(fault_capacity_ratio: float | None):
    """Create a Basilisk ``PowerStorageFaultMsg`` for SimpleBattery capacity faults.

    ``faultCapacityRatio`` is a native input-message field consumed by
    ``SimpleBattery.batteryFaultInMsg``.  ``None`` returns ``None`` so callers
    can keep the fault path unconnected for a nominal battery.
    """
    if fault_capacity_ratio is None:
        return None
    require_basilisk_battery()
    from Basilisk.architecture import messaging

    ratio = max(0.0, min(1.0, float(fault_capacity_ratio)))
    payload = messaging.PowerStorageFaultMsgPayload()
    payload.faultCapacityRatio = ratio
    return messaging.PowerStorageFaultMsg().write(payload)


def attach_battery_capacity_fault_message(battery, fault_msg) -> None:
    """Subscribe a SimpleBattery to a native PowerStorageFaultMsg."""
    if fault_msg is None:
        return
    if not hasattr(battery, "batteryFaultInMsg"):
        raise AttributeError("battery object does not expose batteryFaultInMsg")
    battery.batteryFaultInMsg.subscribeTo(fault_msg)
