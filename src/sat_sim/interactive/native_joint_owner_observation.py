"""R9-C: read-only *whole-spacecraft* scheduler/EPS/thermal/comm evidence.

All measurements are collected from Basilisk or Basilisk-scheduled module
instances in the worker thread after a controlled advance. This deliberately
does NOT export complete C++ integrator, power/thermal queue or radio state.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

SCHEMA = "sat-whole-native-joint-owner-observation.v1"
SCOPE = "LIVE_UNIFIED_BASILISK_READ_ONLY"
GAPS = (
    "native_integrator_internal_stages",
    "native_scheduler_task_and_event_queue_import",
    "eps_cpp_internal_state_and_power_message_history",
    "thermal_network_heater_and_radiator_internal_history",
    "comm_downlink_protocol_and_storage_private_queues",
    "fresh_process_whole_graph_parity",
)


def _number(x: Any, label: str) -> float:
    if type(x) not in (int, float):
        raise ValueError(f"JOINT_OWNER_NONFINITE:{label}")
    f = float(x)
    if not math.isfinite(f):
        raise ValueError(f"JOINT_OWNER_NONFINITE:{label}")
    return f


def _integer(x: Any, label: str) -> int:
    if type(x) is not int:
        raise ValueError(f"JOINT_OWNER_INVALID_INTEGER:{label}")
    return x


def _exact_keys(data: Any, keys: set[str], label: str) -> None:
    if not isinstance(data, dict) or set(data) != keys:
        raise ValueError(f"JOINT_OWNER_INVALID_SCHEMA:{label}")


def validate_joint_owner_observation(
    document: Any, *, expected_time_ns: int | None = None,
    native_scheduler_phase: Mapping[str, Any] | None = None,
) -> None:
    _exact_keys(document, {
        "schema_version", "scope", "capture_time_ns", "scheduler_phase",
        "eps", "thermal", "comm_data", "missing_restore_owners",
        "native_integrator_export_complete", "subsystem_private_state_complete",
        "cross_process_parity_proven", "resume_supported",
    }, "root")
    if document["schema_version"] != SCHEMA or document["scope"] != SCOPE:
        raise ValueError("JOINT_OWNER_INVALID_SCOPE")
    if any(document[k] is not False for k in (
        "native_integrator_export_complete", "subsystem_private_state_complete",
        "cross_process_parity_proven", "resume_supported",
    )):
        raise ValueError("JOINT_OWNER_FALSE_RESTORE_CLAIM")
    if document["missing_restore_owners"] != list(GAPS):
        raise ValueError("JOINT_OWNER_MISSING_GAPS")
    now = _integer(document["capture_time_ns"], "capture_time_ns")
    if now <= 0 or (expected_time_ns is not None and now != expected_time_ns):
        raise ValueError("JOINT_OWNER_TIME_MISMATCH")
    phase = document["scheduler_phase"]
    _exact_keys(phase, {"current_nanos", "next_task_time_nanos",
                        "until_next_task_nanos", "native_clock_restore_proven",
                        "task_queue_restore_proven", "event_queue_restore_proven"}, "scheduler")
    if any(phase[k] is not False for k in (
        "native_clock_restore_proven", "task_queue_restore_proven",
        "event_queue_restore_proven",
    )):
        raise ValueError("JOINT_OWNER_FALSE_SCHEDULER_RESTORE")
    current = _integer(phase["current_nanos"], "current_nanos")
    next_time = _integer(phase["next_task_time_nanos"], "next_task_time_nanos")
    remaining = _integer(phase["until_next_task_nanos"], "until_next_task_nanos")
    if current != now or next_time < current or remaining != next_time - current:
        raise ValueError("JOINT_OWNER_SCHEDULER_PHASE_MISMATCH")
    if native_scheduler_phase is not None and phase != native_scheduler_phase:
        raise ValueError("JOINT_OWNER_SCHEDULER_PHASE_MISMATCH")
    eps = document["eps"]
    _exact_keys(eps, {"battery_energy_j", "battery_capacity_j",
                      "solar_panel_net_power_w", "pdu_adcs_enabled",
                      "pdu_payload_enabled", "pdu_comm_enabled",
                      "pdu_heater_enabled"}, "eps")
    energy = _number(eps["battery_energy_j"], "battery_energy_j")
    capacity = _number(eps["battery_capacity_j"], "battery_capacity_j")
    _number(eps["solar_panel_net_power_w"], "solar_panel_net_power_w")
    if capacity <= 0 or energy < 0 or energy > capacity + 1e-3:
        raise ValueError("JOINT_OWNER_EPS_RANGE_INVALID")
    for key in ("pdu_adcs_enabled", "pdu_payload_enabled",
                "pdu_comm_enabled", "pdu_heater_enabled"):
        if _integer(eps[key], key) not in (0, 1):
            raise ValueError("JOINT_OWNER_PDU_STATUS_INVALID")
    thermal = document["thermal"]
    _exact_keys(thermal, {"model_type", "nodes"}, "thermal")
    if thermal["model_type"] != "BASILISK_SCHEDULED_PROJECT_THERMAL":
        raise ValueError("JOINT_OWNER_THERMAL_PROVENANCE_INVALID")
    nodes = thermal["nodes"]
    if not isinstance(nodes, dict) or not nodes:
        raise ValueError("JOINT_OWNER_THERMAL_NODES_MISSING")
    for name, node in nodes.items():
        if not isinstance(name, str) or not name:
            raise ValueError("JOINT_OWNER_THERMAL_NODE_NAME_INVALID")
        _exact_keys(node, {"temp_k", "last_update_ns", "safe_status"}, "thermal_node")
        if _number(node["temp_k"], "temp_k") <= 0:
            raise ValueError("JOINT_OWNER_THERMAL_TEMP_INVALID")
        t = _integer(node["last_update_ns"], "thermal_last_update_ns")
        if t < 0 or t > now:
            raise ValueError("JOINT_OWNER_THERMAL_TIME_MISMATCH")
        if _integer(node["safe_status"], "thermal_safe_status") not in (0, 1):
            raise ValueError("JOINT_OWNER_THERMAL_STATUS_INVALID")
    comm = document["comm_data"]
    _exact_keys(comm, {"storage_bits", "storage_capacity_bits", "link_active",
                       "delivered_data_rate_bps", "attempted_data_rate_bps"}, "comm")
    stored = _number(comm["storage_bits"], "storage_bits")
    limit = _number(comm["storage_capacity_bits"], "storage_capacity_bits")
    if limit <= 0 or stored < 0 or stored > limit + 1e-3:
        raise ValueError("JOINT_OWNER_COMM_STORAGE_RANGE_INVALID")
    if _integer(comm["link_active"], "link_active") not in (0, 1):
        raise ValueError("JOINT_OWNER_COMM_LINK_INVALID")
    for key in ("delivered_data_rate_bps", "attempted_data_rate_bps"):
        if _number(comm[key], key) < 0:
            raise ValueError("JOINT_OWNER_COMM_RATE_INVALID")


def capture_joint_owner_observation(
    *, scheduler_phase: Mapping[str, Any], battery: Any, panel: Any,
    pdu: Any, thermal_graph: Any, storage: Any, downlink: Any,
) -> dict[str, Any]:
    """Never execute the scheduler or write a message; read current owners."""
    now = int(scheduler_phase["current_nanos"])
    eps = {
        "battery_energy_j": float(battery.batPowerOutMsg.read().storageLevel),
        "battery_capacity_j": float(battery.storageCapacity),
        "solar_panel_net_power_w": float(panel.nodePowerOutMsg.read().netPower),
        "pdu_adcs_enabled": int(pdu.adcsStatusOutMsg.read().deviceStatus),
        "pdu_payload_enabled": int(pdu.payloadStatusOutMsg.read().deviceStatus),
        "pdu_comm_enabled": int(pdu.commStatusOutMsg.read().deviceStatus),
        "pdu_heater_enabled": int(pdu.heaterStatusOutMsg.read().deviceStatus),
    }
    thermal = {
        "model_type": "BASILISK_SCHEDULED_PROJECT_THERMAL",
        "nodes": {
            name: {
                "temp_k": float(node.temp_k),
                "last_update_ns": int(node._last_ns),
                "safe_status": int(node.thermalStatusOutMsg.read().deviceStatus),
            } for name, node in sorted(thermal_graph.nodes.items())
        },
    }
    downlink_msg = downlink.downlinkOutMsg.read()
    comm = {
        "storage_bits": float(storage.storageUnitDataOutMsg.read().storageLevel),
        "storage_capacity_bits": float(storage.storageCapacity),
        "link_active": int(downlink_msg.linkActive),
        "delivered_data_rate_bps": float(downlink_msg.deliveredDataRate),
        "attempted_data_rate_bps": float(downlink_msg.attemptedDataRate),
    }
    result = {
        "schema_version": SCHEMA, "scope": SCOPE, "capture_time_ns": now,
        "scheduler_phase": dict(scheduler_phase), "eps": eps,
        "thermal": thermal, "comm_data": comm,
        "missing_restore_owners": list(GAPS),
        "native_integrator_export_complete": False,
        "subsystem_private_state_complete": False,
        "cross_process_parity_proven": False,
        "resume_supported": False,
    }
    validate_joint_owner_observation(result, expected_time_ns=now,
                                     native_scheduler_phase=scheduler_phase)
    return result
