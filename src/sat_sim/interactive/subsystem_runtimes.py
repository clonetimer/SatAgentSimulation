"""Persistent runtimes for the focused EPS and Comm/Data main capabilities."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from .models import Telecommand


class EpsPersistentRuntime:
    capability_id = "subsystem.eps.unified_native.v1"

    def __init__(self, config: Any) -> None:
        self.config = config
        self._ctx: Any = None
        self._macros: Any = None
        self._time_s = 0.0
        self._emitted = 0
        self._pending_delta: tuple[dict[str, Any], ...] = ()
        self._fault_active = False
        self._closed = False

    @property
    def current_time_s(self) -> float:
        return self._time_s

    def prepare(self) -> None:
        if self._ctx is not None or self._closed:
            raise RuntimeError("EPS runtime cannot be prepared")
        from Basilisk.utilities import macros
        from subsystems.eps.builder import build_eps_basilisk_sim

        self._ctx = build_eps_basilisk_sim(self.config)
        self._ctx.simulation.InitializeSimulation()
        self._macros = macros

    @staticmethod
    def _public(row: Any, fault_active: bool) -> dict[str, Any]:
        return {
            "time_s": row.time_s,
            "eps.battery_storage_j": row.battery_storage_j,
            "eps.battery_capacity_j": row.battery_capacity_j,
            "eps.battery_soc": row.battery_soc,
            "eps.solar_power_w": row.solar_power_w,
            "eps.bus_load_w": row.bus_load_w,
            "eps.payload_load_enabled_w": row.payload_load_enabled_w,
            "eps.adcs_load_enabled_w": row.adcs_load_enabled_w,
            "eps.comm_load_enabled_w": row.comm_load_enabled_w,
            "eps.heater_load_enabled_w": row.heater_load_enabled_w,
            "eps.net_power_w": row.net_power_w,
            "eps.load_shed_active": row.load_shed_active,
            "eps.shed_reason": row.shed_reason,
            "label.fault_active": fault_active,
        }

    def advance_to(self, stop_time_s: float) -> tuple[dict[str, Any], ...]:
        if self._ctx is None or self._closed:
            raise RuntimeError("EPS runtime is not active")
        stop_time_s = float(stop_time_s)
        if stop_time_s <= self._time_s or stop_time_s > float(self.config.duration_s):
            raise ValueError("stop time must increase within EPS duration")
        self._ctx.simulation.ConfigureStopTime(self._macros.sec2nano(stop_time_s))
        self._ctx.simulation.ExecuteSimulation()
        from subsystems.eps.runner import _eps_rows_from_recorders

        rows = _eps_rows_from_recorders(self._ctx.recorders, self.config, self._ctx.modules["pdu"])
        self._pending_delta = tuple(self._public(row, self._fault_active) for row in rows[self._emitted:])
        self._emitted = len(rows)
        self._time_s = stop_time_s
        return self._pending_delta

    def apply_command(self, command: Telecommand) -> None:
        if self._ctx is None or self._closed:
            raise RuntimeError("EPS runtime is not active")
        params = command.parameters
        if command.operation == "eps.load.set":
            load = {"downlink": "comm"}.get(str(params["load_id"]), str(params["load_id"]))
            self._ctx.modules[f"{load}_request"].requested = bool(params["enabled"])
        elif command.operation == "eps.protection.set":
            pdu = self._ctx.modules["pdu"]
            if bool(params["enabled"]):
                pdu.config = replace(
                    pdu.config,
                    payload_min_soc=self.config.payload_min_soc,
                    comm_min_soc=self.config.comm_min_soc,
                    heater_min_soc=self.config.heater_min_soc,
                    adcs_min_soc=self.config.adcs_min_soc,
                )
            else:
                pdu.config = replace(pdu.config, payload_min_soc=0.0, comm_min_soc=0.0, heater_min_soc=0.0, adcs_min_soc=0.0)
        elif command.operation == "eps.fault.inject":
            severity = max(0.0, min(float(params["severity"]), 1.0))
            if params["fault_id"] == "battery_capacity_loss":
                self._ctx.modules["battery"].storageCapacity = float(self.config.battery_capacity_wh) * 3600.0 * (1.0 - severity)
            elif params["fault_id"] == "solar_array_degradation":
                solar = self._ctx.modules["solar_power_node"]
                if not hasattr(solar, "nodePowerOut"):
                    raise ValueError("dynamic solar degradation requires the focused deterministic solar node")
                solar.nodePowerOut = float(self.config.solar_power_w) * (1.0 - severity)
            self._fault_active = True
        else:
            raise ValueError(f"operation has no EPS runtime adapter: {command.operation}")

    def read_delta(self) -> tuple[dict[str, Any], ...]:
        rows, self._pending_delta = self._pending_delta, ()
        return rows

    def finalize(self) -> dict[str, Any]:
        if self._ctx is None:
            raise RuntimeError("EPS runtime was not prepared")
        self._closed = True
        self._ctx = None
        return {"persistent_instance": True, "capability_id": self.capability_id, "final_sim_time_s": self._time_s}

    def abort(self) -> None:
        self._closed = True
        self._ctx = None


class CommDataPersistentRuntime:
    capability_id = "subsystem.comm_data.unified_native.v1"

    def __init__(self, config: Any) -> None:
        self.config = config
        self._ctx: Any = None
        self._macros: Any = None
        self._time_s = 0.0
        self._emitted = 0
        self._pending_delta: tuple[dict[str, Any], ...] = ()
        self._pending_generate_bits: float | None = None
        self._fault_active = False
        self._closed = False

    @property
    def current_time_s(self) -> float:
        return self._time_s

    def prepare(self) -> None:
        if self._ctx is not None or self._closed:
            raise RuntimeError("Comm/Data runtime cannot be prepared")
        from Basilisk.utilities import macros
        from subsystems.comm_data.builder import build_comm_data_basilisk_sim

        self._ctx = build_comm_data_basilisk_sim(self.config)
        self._ctx.simulation.InitializeSimulation()
        self._macros = macros

    @staticmethod
    def _public(row: Any, fault_active: bool) -> dict[str, Any]:
        return {
            "time_s": row.time_s,
            "comm_data.instrument_baud_bps": row.instrument_baud_bps,
            "comm_data.storage_level_bits": row.storage_level_bits,
            "comm_data.storage_capacity_bits": row.storage_capacity_bits,
            "comm_data.transmitter_baud_bps": row.transmitter_baud_bps,
            "comm_data.native_storage_drain_enabled": row.native_storage_drain_enabled,
            "comm_data.transmitter_storage_node_baud_bps": row.transmitter_storage_node_baud_bps,
            "label.fault_active": fault_active,
        }

    def advance_to(self, stop_time_s: float) -> tuple[dict[str, Any], ...]:
        if self._ctx is None or self._closed:
            raise RuntimeError("Comm/Data runtime is not active")
        stop_time_s = float(stop_time_s)
        if stop_time_s <= self._time_s or stop_time_s > float(self.config.duration_s):
            raise ValueError("stop time must increase within Comm/Data duration")
        instrument = self._ctx.modules["instrument"]
        if self._pending_generate_bits is not None:
            instrument.nodeBaudRate = self._pending_generate_bits / max(stop_time_s - self._time_s, float(self.config.step_s))
        self._ctx.simulation.ConfigureStopTime(self._macros.sec2nano(stop_time_s))
        self._ctx.simulation.ExecuteSimulation()
        if self._pending_generate_bits is not None:
            instrument.nodeBaudRate = float(self.config.instrument_baud_bps)
            self._pending_generate_bits = None
        from subsystems.comm_data.runner import _comm_rows_from_recorders

        transmitter_baud = float(self._ctx.base_parameters["transmitter_storage_node_baud_bps"])
        rows = _comm_rows_from_recorders(self._ctx.recorders, self.config, transmitter_baud)
        self._pending_delta = tuple(self._public(row, self._fault_active) for row in rows[self._emitted:])
        self._emitted = len(rows)
        self._time_s = stop_time_s
        return self._pending_delta

    def apply_command(self, command: Telecommand) -> None:
        if self._ctx is None or self._closed:
            raise RuntimeError("Comm/Data runtime is not active")
        params = command.parameters
        if command.operation == "comm_data.generate":
            self._pending_generate_bits = float(params["bits"])
        elif command.operation == "comm_data.downlink.set":
            enabled = bool(params["enabled"])
            rate = abs(float(params["rate_bps"]))
            self._ctx.modules["transmitter"].nodeBaudRate = -rate if enabled else 0.0
        elif command.operation == "comm_data.fault.inject":
            self._ctx.modules["transmitter"].nodeBaudRate = 0.0
            self._fault_active = True
        else:
            raise ValueError(f"operation has no Comm/Data runtime adapter: {command.operation}")

    def read_delta(self) -> tuple[dict[str, Any], ...]:
        rows, self._pending_delta = self._pending_delta, ()
        return rows

    def finalize(self) -> dict[str, Any]:
        if self._ctx is None:
            raise RuntimeError("Comm/Data runtime was not prepared")
        self._closed = True
        self._ctx = None
        return {"persistent_instance": True, "capability_id": self.capability_id, "final_sim_time_s": self._time_s}

    def abort(self) -> None:
        self._closed = True
        self._ctx = None
