"""Thermal network implementation with multi-node heat transfer.

This module implements a comprehensive thermal network that:
- Models multiple thermal nodes with mass, specific heat, and surface properties
- Handles heat conduction between nodes (fixed: no double-counting)
- Models solar heating with eclipse modulation (fixed: eclipse_factor actually works)
- Implements active heaters with hysteresis control (fixed: float temp precision)
- Implements active radiators with deployable control (fixed: uses space sink temp)
- Uses Basilisk SysModel for scheduling and messaging
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic

import csv
import json
from dataclasses import dataclass, asdict
from pathlib import Path

from Basilisk.architecture import messaging, sysModel
from Basilisk.utilities import SimulationBaseClass, macros

from .network_config import (
    ThermalNetworkConfig,
    ThermalNodeParams,
    HeaterParams,
    RadiatorParams,
)

_SIGMA = 5.670374419e-8


@dataclass(frozen=True)
class ThermalNetworkTraceRow:
    """Single time step trace data for thermal network."""
    time_s: float
    node_name: str
    temp_k: float
    heat_input_w: float
    heater_on: bool
    heater_power_w: float
    radiator_on: bool
    radiator_rejected_w: float
    conduction_in_w: float
    conduction_out_w: float
    solar_heat_w: float
    radiation_heat_w: float
    thermal_safe: bool


@dataclass(frozen=True)
class ThermalNetworkSummary:
    """Simulation summary for thermal network."""
    duration_s: float
    step_s: float
    sample_count: int
    final_temps_k: dict[str, float]
    min_temps_k: dict[str, float]
    max_temps_k: dict[str, float]
    heater_activation_count: dict[str, int]
    radiator_activation_count: dict[str, int]
    unsafe_sample_count: int
    unsafe_nodes: list[str]
    status: str


class ThermalNode(sysModel.SysModel):
    """Single thermal node with heat capacity and energy balance."""

    def __init__(self, name: str, params: ThermalNodeParams):
        super().__init__()
        self.ModelTag = f"{name}ThermalNode"
        self.name = name
        self.params = params
        self.temp_k = float(params.initial_temp_k)
        self._heat_input_w = 0.0
        self._conduction_in_w = 0.0
        self._conduction_out_w = 0.0
        self._solar_heat_w = 0.0
        self._radiation_heat_w = 0.0
        self._last_ns: int | None = None
        self.last_heat_input_w = 0.0
        self.input_read_status = "VALID"
        self.input_read_error: str | None = None

        self.heatInMsg = messaging.PowerNodeUsageMsgReader()
        self.tempOutMsg = messaging.DeviceStatusMsg()
        self.thermalStatusOutMsg = messaging.DeviceStatusMsg()

    def Reset(self, CurrentSimNanos: int) -> None:
        self.temp_k = float(self.params.initial_temp_k)
        self._heat_input_w = 0.0
        self._conduction_in_w = 0.0
        self._conduction_out_w = 0.0
        self._solar_heat_w = 0.0
        self._radiation_heat_w = 0.0
        self._last_ns = None
        self.last_heat_input_w = 0.0
        self.input_read_status = "VALID"
        self.input_read_error = None
        self.UpdateState(CurrentSimNanos)

    def UpdateState(self, CurrentSimNanos: int) -> None:
        if self._last_ns is None:
            dt = 0.0
        else:
            dt = max(0.0, float(CurrentSimNanos - self._last_ns) * macros.NANO2SEC)
        self._last_ns = CurrentSimNanos

        self.input_read_status = "VALID"
        self.input_read_error = None
        self._heat_input_w = 0.0
        try:
            linked = bool(self.heatInMsg.isLinked()) if hasattr(self.heatInMsg, "isLinked") else True
            if linked:
                written = bool(self.heatInMsg.isWritten()) if hasattr(self.heatInMsg, "isWritten") else True
                if not written:
                    self.input_read_status = "NOT_READY" if int(CurrentSimNanos) == 0 else "ERROR"
                    self.input_read_error = "thermal power input message has not been written"
                else:
                    msg = self.heatInMsg()
                    if msg is not None and hasattr(msg, "netPower"):
                        self._heat_input_w = abs(float(msg.netPower))
            # An unlinked input is valid for a passive node with only internal,
            # conductive, radiative or solar heat sources.
        except Exception as exc:
            self.input_read_status = "NOT_READY" if int(CurrentSimNanos) == 0 else "ERROR"
            self.input_read_error = f"{type(exc).__name__}: {exc}"
            self._heat_input_w = 0.0

        self._heat_input_w += self.params.internal_heat_w
        self.last_heat_input_w = float(self._heat_input_w)

        net_w = (
            self._heat_input_w
            + self._conduction_in_w
            - self._conduction_out_w
            + self._solar_heat_w
            + self._radiation_heat_w
        )

        self.temp_k += net_w * dt / max(1e-9, self.params.thermal_capacity_j_per_k)

        safe = self.params.min_temp_k <= self.temp_k <= self.params.max_temp_k

        temp_msg = messaging.DeviceStatusMsgPayload()
        temp_msg.deviceStatus = int(round(self.temp_k))
        self.tempOutMsg.write(temp_msg, CurrentSimNanos, self.moduleID)

        safe_msg = messaging.DeviceStatusMsgPayload()
        safe_msg.deviceStatus = int(safe)
        self.thermalStatusOutMsg.write(safe_msg, CurrentSimNanos, self.moduleID)

        self._heat_input_w = 0.0
        self._conduction_in_w = 0.0
        self._conduction_out_w = 0.0
        self._solar_heat_w = 0.0
        self._radiation_heat_w = 0.0

    @property
    def thermal_safe(self) -> bool:
        return self.params.min_temp_k <= self.temp_k <= self.params.max_temp_k


class ThermalHeater(sysModel.SysModel):
    """Active heater with hysteresis control."""

    def __init__(self, name: str, params: HeaterParams):
        super().__init__()
        self.ModelTag = f"{name}Heater"
        self.name = name
        self.params = params
        self._heater_on = False
        self._node = None
        # Runtime fault hooks.  These are intentionally simple direct mutation
        # targets exposed for whole-spacecraft fault injection.
        self.forced_state: bool | None = None
        self.power_scale: float = 1.0
        self.enableInMsg = messaging.DeviceStatusMsgReader()
        self._has_enable_input = False
        self.enable_read_status = "VALID"
        self.enable_read_error: str | None = None

        self.heaterOutMsg = messaging.DeviceStatusMsg()
        self.powerOutMsg = messaging.PowerNodeUsageMsg()

    def set_node(self, node: ThermalNode) -> None:
        """Set the target thermal node (direct reference for float precision)."""
        self._node = node

    def subscribe_enable(self, status_msg) -> None:
        """Subscribe the heater to an EPS/PDU enable status message."""
        self.enableInMsg.subscribeTo(status_msg)
        self._has_enable_input = True

    def Reset(self, CurrentSimNanos: int) -> None:
        self._heater_on = False
        self.enable_read_status = "VALID"
        self.enable_read_error = None
        self.UpdateState(CurrentSimNanos)

    def UpdateState(self, CurrentSimNanos: int) -> None:
        node_temp_k = self._node.temp_k if self._node else self.params.setpoint_k

        if node_temp_k < self.params.on_below_k:
            self._heater_on = True
        elif node_temp_k > self.params.off_above_k:
            self._heater_on = False
        if self.forced_state is not None:
            self._heater_on = bool(self.forced_state)
        self.enable_read_status = "VALID"
        self.enable_read_error = None
        if self._has_enable_input:
            try:
                linked = bool(self.enableInMsg.isLinked()) if hasattr(self.enableInMsg, "isLinked") else True
                written = bool(self.enableInMsg.isWritten()) if hasattr(self.enableInMsg, "isWritten") else True
                if not linked:
                    self.enable_read_status = "ERROR"
                    self.enable_read_error = "heater enable message is not linked"
                    self._heater_on = False
                elif not written:
                    self.enable_read_status = "NOT_READY" if int(CurrentSimNanos) == 0 else "ERROR"
                    self.enable_read_error = "heater enable message has not been written"
                    self._heater_on = False
                elif not bool(self.enableInMsg().deviceStatus):
                    self._heater_on = False
            except Exception as exc:
                self.enable_read_status = "NOT_READY" if int(CurrentSimNanos) == 0 else "ERROR"
                self.enable_read_error = f"{type(exc).__name__}: {exc}"
                self._heater_on = False

        heater_msg = messaging.DeviceStatusMsgPayload()
        heater_msg.deviceStatus = int(self._heater_on)
        self.heaterOutMsg.write(heater_msg, CurrentSimNanos, self.moduleID)

        scale = max(0.0, float(getattr(self, 'power_scale', 1.0)))
        power_msg = messaging.PowerNodeUsageMsgPayload()
        power_msg.netPower = -abs(self.params.power_w * self.params.efficiency * scale) if self._heater_on else 0.0
        self.powerOutMsg.write(power_msg, CurrentSimNanos, self.moduleID)


class ThermalRadiator(sysModel.SysModel):
    """Active radiator with deployable control."""

    def __init__(self, name: str, params: RadiatorParams, sink_temp_k: float = 2.7):
        super().__init__()
        self.ModelTag = f"{name}Radiator"
        self.name = name
        self.params = params
        self.sink_temp_k = sink_temp_k
        self._radiator_on = False
        self._node = None
        self._rejected_heat_w = 0.0
        # Direct runtime mutation target for radiator fouling/blockage faults.
        self.rejection_factor: float = 1.0

        self.radiatorOutMsg = messaging.DeviceStatusMsg()

    def set_node(self, node: ThermalNode) -> None:
        """Set the target thermal node (direct reference for float precision)."""
        self._node = node

    def subscribe_enable(self, status_msg) -> None:
        """Subscribe the heater to an EPS/PDU enable status message."""
        self.enableInMsg.subscribeTo(status_msg)
        self._has_enable_input = True

    def Reset(self, CurrentSimNanos: int) -> None:
        self._radiator_on = False
        self._rejected_heat_w = 0.0
        self.UpdateState(CurrentSimNanos)

    def compute_rejection(self, node_temp_k: float) -> float:
        if node_temp_k <= self.params.min_temp_k:
            return 0.0
        # A deployable radiator rejects heat only after its control state has
        # commanded deployment.  A fixed radiator is always physically present
        # and therefore does not depend on ``_radiator_on``.
        if self.params.deployable and not self._radiator_on:
            return 0.0

        raw = (
            self.params.emissivity
            * self.params.view_factor
            * _SIGMA
            * self.params.area_m2
            * (node_temp_k ** 4 - self.sink_temp_k ** 4)
        )
        factor = max(0.0, float(getattr(self, 'rejection_factor', 1.0)))
        return max(0.0, raw * factor)

    def UpdateState(self, CurrentSimNanos: int) -> None:
        node_temp_k = self._node.temp_k if self._node else self.params.min_temp_k

        if self.params.deployable:
            if node_temp_k > self.params.max_temp_k:
                self._radiator_on = True
            elif node_temp_k < self.params.min_temp_k:
                self._radiator_on = False

        self._rejected_heat_w = self.compute_rejection(node_temp_k)

        radiator_msg = messaging.DeviceStatusMsgPayload()
        radiator_msg.deviceStatus = int(self._radiator_on)
        self.radiatorOutMsg.write(radiator_msg, CurrentSimNanos, self.moduleID)

    @property
    def rejected_heat_w(self) -> float:
        return self._rejected_heat_w


class SolarHeatInput(sysModel.SysModel):
    """Dynamic solar heat input modulated by eclipse."""

    def __init__(self, config: ThermalNetworkConfig):
        super().__init__()
        self.ModelTag = "SolarHeatInput"
        self.config = config
        self._shadow_factor = 1.0
        self._external_shadow_factor: float | None = None
        self.input_read_status = "VALID"
        self.input_read_error: str | None = None

        self.eclipseInMsg = messaging.EclipseMsgReader()
        self.heatOutMsg = messaging.PowerNodeUsageMsg()

    def set_external_shadow_factor(self, shadow_factor: float) -> None:
        """Set external shadow factor to override message-based value."""
        self._external_shadow_factor = shadow_factor

    def Reset(self, CurrentSimNanos: int) -> None:
        self._shadow_factor = 1.0
        self.input_read_status = "VALID"
        self.input_read_error = None
        self.UpdateState(CurrentSimNanos)

    def UpdateState(self, CurrentSimNanos: int) -> None:
        self.input_read_status = "VALID"
        self.input_read_error = None
        if self._external_shadow_factor is not None:
            self._shadow_factor = self._external_shadow_factor
        elif hasattr(self.eclipseInMsg, "isLinked") and bool(self.eclipseInMsg.isLinked()):
            try:
                written = bool(self.eclipseInMsg.isWritten()) if hasattr(self.eclipseInMsg, "isWritten") else True
                if not written:
                    self.input_read_status = "NOT_READY" if int(CurrentSimNanos) == 0 else "ERROR"
                    self.input_read_error = "eclipse message has not been written"
                else:
                    msg = self.eclipseInMsg()
                    for field in ("illuminationFactor", "eclipseFactor", "shadowFactor"):
                        if hasattr(msg, field):
                            self._shadow_factor = max(0.0, min(1.0, float(getattr(msg, field))))
                            break
            except Exception as exc:
                self.input_read_status = "NOT_READY" if int(CurrentSimNanos) == 0 else "ERROR"
                self.input_read_error = f"{type(exc).__name__}: {exc}"
        else:
            # An intentionally unlinked eclipse input represents a configured
            # always-illuminated standalone thermal case.
            self._shadow_factor = 1.0

        solar_heat_w = 0.0
        if self.config.solar_panels and self._shadow_factor > 0:
            solar_heat_w = (
                self.config.sun_power_w_per_m2
                * self.config.solar_panels.area_m2
                * self.config.solar_panels.absorptivity
                * (1.0 - self.config.solar_panels.electrical_efficiency)
                * self._shadow_factor
            )

        payload = messaging.PowerNodeUsageMsgPayload()
        payload.netPower = -abs(solar_heat_w)
        self.heatOutMsg.write(payload, CurrentSimNanos, self.moduleID)

    @property
    def shadow_factor(self) -> float:
        return self._shadow_factor




class ThermalPowerBridge(sysModel.SysModel):
    """Aggregate subsystem electrical loads into one node-specific heat input.

    Each input is a Basilisk ``PowerNodeUsageMsg``.  The bridge converts the
    absolute electrical draw into deposited heat through an explicit efficiency
    factor and publishes one message consumed by exactly one thermal node.
    Runtime counters are retained so whole-spacecraft energy-coupling audits can
    verify that the proxy was not only wired but also exercised.
    """

    def __init__(self, node_name: str):
        super().__init__()
        self.ModelTag = f"{node_name}ThermalPowerBridge"
        self.node_name = str(node_name)
        self.powerInMsgs: list[messaging.PowerNodeUsageMsgReader] = []
        self.efficiency_factors: list[float] = []
        self.source_labels: list[str] = []
        self.heatOutMsg = messaging.PowerNodeUsageMsg()
        self.last_electrical_input_w = 0.0
        self.last_heat_output_w = 0.0
        self.cumulative_electrical_energy_j = 0.0
        self.cumulative_heat_energy_j = 0.0
        self.last_source_power_w: dict[str, float] = {}
        self.cumulative_source_electrical_energy_j: dict[str, float] = {}
        self.cumulative_source_heat_energy_j: dict[str, float] = {}
        self._last_ns: int | None = None
        self.input_read_status = "VALID"
        self.input_read_errors: list[str] = []

    def add_power_input(self, power_msg, efficiency_factor: float = 1.0, *, source_label: str = "") -> None:
        reader = messaging.PowerNodeUsageMsgReader()
        reader.subscribeTo(power_msg)
        self.powerInMsgs.append(reader)
        self.efficiency_factors.append(max(0.0, float(efficiency_factor)))
        self.source_labels.append(str(source_label or f"source_{len(self.source_labels)}"))

    def Reset(self, CurrentSimNanos: int) -> None:
        self.last_electrical_input_w = 0.0
        self.last_heat_output_w = 0.0
        self.cumulative_electrical_energy_j = 0.0
        self.cumulative_heat_energy_j = 0.0
        self.last_source_power_w.clear()
        self.cumulative_source_electrical_energy_j.clear()
        self.cumulative_source_heat_energy_j.clear()
        self._last_ns = None
        self.input_read_status = "VALID"
        self.input_read_errors.clear()
        self.UpdateState(CurrentSimNanos)

    def UpdateState(self, CurrentSimNanos: int) -> None:
        if self._last_ns is None:
            dt = 0.0
        else:
            dt = max(0.0, float(CurrentSimNanos - self._last_ns) * macros.NANO2SEC)
        self._last_ns = int(CurrentSimNanos)

        electrical_w = 0.0
        heat_w = 0.0
        self.input_read_status = "VALID"
        self.input_read_errors.clear()
        for reader, efficiency, label in zip(
            self.powerInMsgs, self.efficiency_factors, self.source_labels
        ):
            source_w = 0.0
            try:
                linked = bool(reader.isLinked()) if hasattr(reader, "isLinked") else True
                written = bool(reader.isWritten()) if hasattr(reader, "isWritten") else True
                if not linked:
                    self.input_read_status = "ERROR"
                    self.input_read_errors.append(f"{label}: power message is not linked")
                elif not written:
                    status = "NOT_READY" if int(CurrentSimNanos) == 0 else "ERROR"
                    if status == "ERROR":
                        self.input_read_status = "ERROR"
                    elif self.input_read_status != "ERROR":
                        self.input_read_status = "NOT_READY"
                    self.input_read_errors.append(f"{label}: power message has not been written")
                else:
                    payload = reader()
                    source_w = abs(float(getattr(payload, "netPower", 0.0)))
            except Exception as exc:
                status = "NOT_READY" if int(CurrentSimNanos) == 0 else "ERROR"
                if status == "ERROR":
                    self.input_read_status = "ERROR"
                elif self.input_read_status != "ERROR":
                    self.input_read_status = "NOT_READY"
                self.input_read_errors.append(f"{label}: {type(exc).__name__}: {exc}")
                record_runtime_diagnostic(
                    code="THERMAL_POWER_BRIDGE_READ_FAILED",
                    category=DiagnosticCategory.MESSAGE_READ_FAILURE,
                    location="src/subsystems/thermal/thermal_network.py:ThermalPowerBridge.UpdateState",
                    exception=exc,
                    strict=None,
                )
            source_heat_w = source_w * efficiency
            electrical_w += source_w
            heat_w += source_heat_w
            self.last_source_power_w[label] = source_w
            self.cumulative_source_electrical_energy_j[label] = (
                self.cumulative_source_electrical_energy_j.get(label, 0.0) + source_w * dt
            )
            self.cumulative_source_heat_energy_j[label] = (
                self.cumulative_source_heat_energy_j.get(label, 0.0) + source_heat_w * dt
            )

        self.last_electrical_input_w = electrical_w
        self.last_heat_output_w = heat_w
        self.cumulative_electrical_energy_j += electrical_w * dt
        self.cumulative_heat_energy_j += heat_w * dt

        output = messaging.PowerNodeUsageMsgPayload()
        output.netPower = -abs(heat_w)
        self.heatOutMsg.write(output, CurrentSimNanos, self.moduleID)


class ThermalNetworkSysModel(sysModel.SysModel):
    """Integrated thermal network system model."""

    def __init__(
        self,
        config: ThermalNetworkConfig,
        *,
        external_power_inputs_by_node: dict[str, list[tuple[object, float, str]]] | None = None,
        eclipse_msg=None,
    ):
        super().__init__()
        self.ModelTag = "ThermalNetwork"
        self.config = config
        self.external_power_inputs_by_node = {
            str(node): list(inputs)
            for node, inputs in (external_power_inputs_by_node or {}).items()
        }
        self.eclipse_msg = eclipse_msg
        self.nodes: dict[str, ThermalNode] = {}
        self.heaters: dict[str, ThermalHeater] = {}
        self.radiators: dict[str, ThermalRadiator] = {}
        self.power_bridges: dict[str, ThermalPowerBridge] = {}
        self.solar_input: SolarHeatInput | None = None
        self._last_ns: int | None = None
        self.trace: list[ThermalNetworkTraceRow] = []
        self.read_errors: list[str] = []

        self._conduction_paths: list[tuple[str, str, float]] = []
        for path in config.conduction_paths:
            self._conduction_paths.append((path.from_node, path.to_node, path.conductance_w_per_k))

        self.thermalStatusOutMsg = messaging.DeviceStatusMsg()

    def build(self, sim, task_name: str) -> None:
        for name, params in self.config.nodes.items():
            node = ThermalNode(name, params)
            self.nodes[name] = node
            sim.AddModelToTask(task_name, node)

        if self.config.heaters:
            for node_name, params in self.config.heaters.items():
                if node_name in self.nodes:
                    heater = ThermalHeater(node_name, params)
                    heater.set_node(self.nodes[node_name])
                    self.heaters[node_name] = heater
                    sim.AddModelToTask(task_name, heater)

        if self.config.radiators:
            for node_name, params in self.config.radiators.items():
                if node_name in self.nodes:
                    radiator = ThermalRadiator(
                        node_name, params, sink_temp_k=self.config.sink_temp_k
                    )
                    radiator.set_node(self.nodes[node_name])
                    self.radiators[node_name] = radiator
                    sim.AddModelToTask(task_name, radiator)

        if self.config.solar_panels:
            self.solar_input = SolarHeatInput(self.config)
            if self.eclipse_msg is not None:
                self.solar_input.eclipseInMsg.subscribeTo(self.eclipse_msg)
            sim.AddModelToTask(task_name, self.solar_input)

        # One reader may subscribe to only one source message.  Build an explicit
        # node-local aggregation bridge so EPS heat and heater thermal output are
        # both applied without overwriting either connection.
        for node_name, node in self.nodes.items():
            bridge = ThermalPowerBridge(node_name)
            for power_msg, efficiency, label in self.external_power_inputs_by_node.get(node_name, []):
                bridge.add_power_input(power_msg, efficiency, source_label=label)
            heater = self.heaters.get(node_name)
            if heater is not None:
                # Heater.powerOutMsg is already the deposited thermal power after
                # heater efficiency, so no second efficiency factor is applied.
                bridge.add_power_input(heater.powerOutMsg, 1.0, source_label=f"heater.{node_name}")
            if bridge.powerInMsgs:
                self.power_bridges[node_name] = bridge
                sim.AddModelToTask(task_name, bridge)
                node.heatInMsg.subscribeTo(bridge.heatOutMsg)

    def Reset(self, CurrentSimNanos: int) -> None:
        self.trace.clear()
        self.read_errors.clear()
        self._last_ns = None
        # Publish a valid aggregate state at reset so downstream gates and
        # recorders never interpret the unwritten DeviceStatus default (0) as
        # an actual over-temperature condition at t=0.
        status_msg = messaging.DeviceStatusMsgPayload()
        status_msg.deviceStatus = int(all(node.thermal_safe for node in self.nodes.values()))
        self.thermalStatusOutMsg.write(status_msg, CurrentSimNanos, self.moduleID)

    def UpdateState(self, CurrentSimNanos: int) -> None:
        if self._last_ns is None:
            _dt = 0.0
        else:
            _dt = max(0.0, float(CurrentSimNanos - self._last_ns) * macros.NANO2SEC)
        self._last_ns = CurrentSimNanos

        self._compute_conduction()
        self._compute_radiation()
        self._compute_solar()

        for name, node in self.nodes.items():
            heater_on = self.heaters[name]._heater_on if name in self.heaters else False
            heater_power = (
                self.heaters[name].params.power_w * self.heaters[name].params.efficiency
                if heater_on else 0.0
            )
            radiator_on = self.radiators[name]._radiator_on if name in self.radiators else False
            radiator_rejected = self.radiators[name].rejected_heat_w if name in self.radiators else 0.0

            self.trace.append(ThermalNetworkTraceRow(
                time_s=float(CurrentSimNanos) * macros.NANO2SEC,
                node_name=name,
                temp_k=node.temp_k,
                heat_input_w=node.last_heat_input_w,
                heater_on=heater_on,
                heater_power_w=heater_power,
                radiator_on=radiator_on,
                radiator_rejected_w=radiator_rejected,
                conduction_in_w=node._conduction_in_w,
                conduction_out_w=node._conduction_out_w,
                solar_heat_w=node._solar_heat_w,
                radiation_heat_w=node._radiation_heat_w,
                thermal_safe=node.thermal_safe,
            ))

        for name, node in self.nodes.items():
            if node.input_read_status == "ERROR":
                item = f"node.{name}: {node.input_read_error or 'thermal power input read failure'}"
                if item not in self.read_errors:
                    self.read_errors.append(item)
        for name, bridge in self.power_bridges.items():
            if bridge.input_read_status == "ERROR":
                for error in bridge.input_read_errors or ["thermal power bridge input read failure"]:
                    item = f"bridge.{name}: {error}"
                    if item not in self.read_errors:
                        self.read_errors.append(item)
        for name, heater in self.heaters.items():
            if heater.enable_read_status == "ERROR":
                item = f"heater.{name}: {heater.enable_read_error or 'heater enable read failure'}"
                if item not in self.read_errors:
                    self.read_errors.append(item)
        if self.solar_input is not None and self.solar_input.input_read_status == "ERROR":
            item = f"solar_input: {self.solar_input.input_read_error or 'eclipse read failure'}"
            if item not in self.read_errors:
                self.read_errors.append(item)

        all_safe = all(node.thermal_safe for node in self.nodes.values())
        status_msg = messaging.DeviceStatusMsgPayload()
        status_msg.deviceStatus = int(all_safe)
        self.thermalStatusOutMsg.write(status_msg, CurrentSimNanos, self.moduleID)

    def _compute_conduction(self) -> None:
        for from_node, to_node, conductance in self._conduction_paths:
            if from_node in self.nodes and to_node in self.nodes:
                temp_diff = self.nodes[from_node].temp_k - self.nodes[to_node].temp_k
                heat_w = conductance * temp_diff
                self.nodes[from_node]._conduction_out_w += heat_w
                self.nodes[to_node]._conduction_in_w += heat_w

    def _compute_radiation(self) -> None:
        for name, node in self.nodes.items():
            radiation_heat = (
                _SIGMA
                * node.params.emissivity
                * node.params.surface_area_m2
                * (self.config.sink_temp_k ** 4 - node.temp_k ** 4)
            )
            node._radiation_heat_w += radiation_heat

            if name in self.radiators:
                node._radiation_heat_w -= self.radiators[name].rejected_heat_w

    def _compute_solar(self) -> None:
        if self.solar_input and self.solar_input._shadow_factor > 0:
            for name, node in self.nodes.items():
                solar_heat = (
                    self.config.sun_power_w_per_m2
                    * node.params.absorptivity
                    * node.params.surface_area_m2
                    * self.solar_input._shadow_factor
                )
                node._solar_heat_w += solar_heat


def run_thermal_network_simulation(
    config: ThermalNetworkConfig,
    eclipse_factor: float = 1.0,
) -> tuple[ThermalNetworkSummary, list[ThermalNetworkTraceRow]]:
    """Run thermal network simulation.
    
    Args:
        config: Thermal network configuration
        eclipse_factor: Eclipse factor (0=full eclipse, 1=full sun)
    
    Returns:
        Tuple of summary and trace data
    """
    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("thermalNetworkProcess")
    task_name = "thermalNetworkTask"
    process.addTask(sim.CreateNewTask(task_name, macros.sec2nano(float(config.step_s))))

    network = ThermalNetworkSysModel(config)
    network.build(sim, task_name)

    if network.solar_input:
        network.solar_input.set_external_shadow_factor(eclipse_factor)

    sim.AddModelToTask(task_name, network)

    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(float(config.duration_s)))
    sim.ExecuteSimulation()

    final_temps = {name: node.temp_k for name, node in network.nodes.items()}
    min_temps = {name: min(r.temp_k for r in network.trace if r.node_name == name) for name in network.nodes}
    max_temps = {name: max(r.temp_k for r in network.trace if r.node_name == name) for name in network.nodes}
    
    heater_counts = {name: sum(1 for r in network.trace if r.node_name == name and r.heater_on) for name in config.heaters}
    radiator_counts = {name: sum(1 for r in network.trace if r.node_name == name and r.radiator_on) for name in config.radiators}
    
    unsafe_count = sum(1 for r in network.trace if not r.thermal_safe)
    unsafe_nodes = list(set(r.node_name for r in network.trace if not r.thermal_safe))
    
    status = "PASS" if unsafe_count == 0 else "WARNING" if unsafe_count < len(network.trace) * 0.1 else "FAIL"

    summary = ThermalNetworkSummary(
        duration_s=float(config.duration_s),
        step_s=float(config.step_s),
        sample_count=len(network.trace),
        final_temps_k=final_temps,
        min_temps_k=min_temps,
        max_temps_k=max_temps,
        heater_activation_count=heater_counts,
        radiator_activation_count=radiator_counts,
        unsafe_sample_count=unsafe_count,
        unsafe_nodes=unsafe_nodes,
        status=status,
    )

    return summary, network.trace


def write_thermal_network_dataset(output_dir: str | Path, config: ThermalNetworkConfig) -> dict[str, str]:
    """Write thermal network simulation results to files."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    profiles = [
        ("full_sun", 1.0),
        ("partial_eclipse", 0.5),
        ("full_eclipse", 0.0),
    ]

    all_summaries = []
    all_rows: list[ThermalNetworkTraceRow] = []

    for profile_name, eclipse_factor in profiles:
        summary, rows = run_thermal_network_simulation(config, eclipse_factor=eclipse_factor)
        summary_dict = asdict(summary)
        summary_dict["profile_name"] = profile_name
        summary_dict["eclipse_factor"] = eclipse_factor
        all_summaries.append(summary_dict)
        all_rows.extend(rows)

    summary_path = output_dir / "thermal_network_summary.json"
    trace_path = output_dir / "thermal_network_trace.csv"
    manifest_path = output_dir / "thermal_network_manifest.json"

    summary_payload = {
        "dataset_type": "thermal_network",
        "profile_count": len(profiles),
        "profiles": [p[0] for p in profiles],
        "summaries": all_summaries,
        "overall_status": "PASS" if all(s["status"] == "PASS" for s in all_summaries) else "FAIL",
    }

    summary_path.write_text(json.dumps(summary_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    with trace_path.open("w", newline="", encoding="utf-8") as f:
        fields = list(ThermalNetworkTraceRow.__annotations__.keys())
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in all_rows:
            writer.writerow(asdict(row))

    manifest = {
        "dataset_type": "thermal_network",
        "files": {"summary": summary_path.name, "trace": trace_path.name},
        "summary": summary_payload,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return {"summary": str(summary_path), "trace": str(trace_path), "manifest": str(manifest_path)}
