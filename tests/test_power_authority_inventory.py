"""Actual electrical providers must remain visible across runtime inventories."""
from __future__ import annotations

import importlib
import importlib.util
import json
import math

import pytest

pytest.importorskip("Basilisk", reason="requires official Basilisk")

from Basilisk.architecture import messaging, sysModel
from Basilisk.simulation import simplePowerSink
from Basilisk.utilities import SimulationBaseClass, macros

from integration.resource_feedback import DataActivityPowerBridge, PayloadActivityStatusAdapter, ThrusterActivityStatusAdapter
from subsystems.thermal.network_config import HeaterParams
from subsystems.thermal.thermal_network import ThermalHeater, ThermalPowerBridge


def _api():
    name = "integration.power_authority_inventory"
    assert importlib.util.find_spec(name) is not None, "shared power-authority inventory is missing"
    module = importlib.import_module(name)
    return module.PowerAuthorityBinding, module.build_power_authority_inventory


def _sink(tag="nativeLoad", watts=12.0):
    sink = simplePowerSink.SimplePowerSink()
    sink.ModelTag = tag
    sink.nodePowerOut = -watts
    return sink


def _run(*models, duration_s=0.4):
    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("InventoryProcess")
    task = "InventoryTask"
    process.addTask(sim.CreateNewTask(task, macros.sec2nano(0.2)))
    for index, model in enumerate(models):
        sim.AddModelToTask(task, model, None, len(models) - index)
    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(duration_s))
    sim.ExecuteSimulation()
    return sim, [model for scheduled in sim.TaskList for model in scheduled.TaskModels]


def _codes(inventory):
    return {error["code"] for error in inventory["errors"]}


@pytest.mark.parametrize("output_attr", ["powerOutMsg", "heatOutMsg", "unfamiliarPowerPort"])
def test_unknown_native_scheduled_generic_power_output_cannot_evade_census(output_attr):
    _, build = _api()

    class UnregisteredPowerSource(sysModel.SysModel):
        def __init__(self):
            super().__init__()
            setattr(self, output_attr, messaging.PowerNodeUsageMsg())

        def UpdateState(self, now):
            payload = messaging.PowerNodeUsageMsgPayload()
            payload.netPower = -7.0
            getattr(self, output_attr).write(payload, now, self.moduleID)

    source = UnregisteredPowerSource()
    _, scheduled = _run(source)
    inventory = build("unknown-generic-port", (), scheduled_models=scheduled)
    assert inventory["status"] == "ERROR"
    assert inventory["counts"]["unknown_provider_count"] == 1
    assert inventory["counts"]["unregistered_provider_count"] == 1
    assert inventory["authorities"][0]["output_attr"] == output_attr
    assert inventory["authorities"][0]["message_evidence"]["net_power_w"] == -7.0


def test_actual_native_project_and_proxy_providers_have_separate_counts():
    Binding, build = _api()
    sink = _sink()
    heater = ThermalHeater("battery", HeaterParams(power_w=12.0, efficiency=0.5))
    heater.forced_state = True
    data = messaging.DataNodeUsageMsgPayload()
    data.baudRate = -50.0
    data_msg = messaging.DataNodeUsageMsg().write(data)
    proxy = DataActivityPowerBridge(
        "legacyComm", nominal_rate_bps=100.0, active_power_w=30.0,
        idle_power_w=2.0, data_msg=data_msg,
    )
    activity = messaging.THROutputMsgPayload()
    activity.thrustFactor = 0.5
    activity_msg = messaging.THROutputMsg().write(activity)
    adapter = ThrusterActivityStatusAdapter("thrusterStatus", thruster_output_msg=activity_msg)
    bridge = ThermalPowerBridge("electronics")
    bridge.add_power_input(sink.nodePowerOutMsg, 0.9, source_label="native_load")
    recorder = sink.nodePowerOutMsg.recorder()
    sim, scheduled = _run(sink, heater, proxy, adapter, bridge, recorder)

    result = build(
        "native_fixture",
        [Binding(sink, "nodePowerOutMsg", "bus", ("bus_power",), ("electronics",), ("12 W fixed load",)),
         Binding(heater, "electricalPowerOutMsg", "heater", ("heater_power",), ("battery",), ("50% heat efficiency",)),
         Binding(proxy, "powerOutMsg", "comm", ("comm_power",), ("electronics",), ("legacy rate-to-power estimate",))],
        status_adapters=[adapter], thermal_bridges=[bridge], scheduled_models=scheduled,
    )

    assert result["status"] == "PASS"
    assert result["errors"] == []
    assert result["counts"]["electrical_provider_count"] == 3
    assert result["counts"]["official_provider_count"] == 1
    assert result["counts"]["project_physical_provider_count"] == 1
    assert result["counts"]["engineering_proxy_provider_count"] == 1
    assert result["counts"]["unknown_provider_count"] == 0
    assert result["counts"]["status_adapter_count"] == 1
    assert result["counts"]["thermal_bridge_count"] == 1
    assert result["counts"]["scheduled_model_count"] == 6
    assert result["counts"]["scheduled_recorder_count"] == 1
    entries = {entry["model_tag"]: entry for entry in result["authorities"]}
    assert entries["nativeLoad"]["model_class"] == "Basilisk.simulation.simplePowerSink.SimplePowerSink"
    assert entries["nativeLoad"]["origin"] == "official"
    assert entries["batteryHeater"]["origin"] == "project_physical"
    assert entries["legacyComm"]["origin"] == "engineering_proxy"
    for tag, expected_w in [("nativeLoad", -12.0), ("batteryHeater", -12.0), ("legacyComm", -17.0)]:
        evidence = entries[tag]["message_evidence"]
        assert evidence["written"] is True
        assert evidence["read_status"] == "VALID"
        assert evidence["source_time_ns"] == 400_000_000
        assert evidence["net_power_w"] == pytest.approx(expected_w)
    assert entries["batteryHeater"]["output_attr"] == "electricalPowerOutMsg"
    assert len([row for row in result["scheduled_models"] if row["kind"] == "recorder"]) == 1
    json.dumps(result, allow_nan=False)


def test_dedup_uses_actual_model_and_port_not_tag_or_swig_wrapper():
    Binding, build = _api()
    first, second = _sink("sameTag", 3.0), _sink("sameTag", 7.0)
    sim, scheduled = _run(first, second)
    result = build("duplicates", [
        Binding(first, "nodePowerOutMsg", "bus", ("bus",), ("battery",)),
        Binding(first, "nodePowerOutMsg", "bus", ("bus_alias",), ("electronics",)),
        Binding(second, "nodePowerOutMsg", "payload", ("payload",)),
    ], scheduled_models=scheduled + [first])

    assert result["status"] == "PASS"
    assert result["counts"]["electrical_provider_count"] == 2
    assert result["counts"]["scheduled_model_count"] == 2
    entries = result["authorities"]
    assert len({entry["provider_id"] for entry in entries}) == 2
    assert sorted(entry["message_evidence"]["net_power_w"] for entry in entries) == [-7.0, -3.0]
    first_entry = next(entry for entry in entries if entry["message_evidence"]["net_power_w"] == -3.0)
    assert first_entry["output_attrs"] == ["nodePowerOutMsg"]
    assert first_entry["recorder_names"] == ["bus", "bus_alias"]
    assert first_entry["thermal_destinations"] == ["battery", "electronics"]


def test_scheduled_provider_missing_from_bindings_is_visible_and_fails_inventory():
    _, build = _api()
    sink = _sink()
    recorder = sink.nodePowerOutMsg.recorder()
    sim, scheduled = _run(sink, recorder)
    result = build("missing_binding", [], status_adapters=[sink], scheduled_models=scheduled)

    assert result["status"] == "ERROR"
    assert result["counts"]["electrical_provider_count"] == 1
    assert result["counts"]["unregistered_provider_count"] == 1
    assert result["counts"]["official_provider_count"] == 1
    assert "UNREGISTERED_ELECTRICAL_PROVIDER" in _codes(result)
    assert result["authorities"][0]["registered"] is False
    assert result["authorities"][0]["message_evidence"]["net_power_w"] == -12.0


class UnclassifiedElectricalModel(sysModel.SysModel):
    """A real scheduled SysModel whose implementation has not been classified."""

    def __init__(self):
        super().__init__()
        self.ModelTag = "unclassifiedElectrical"
        self.electricalPowerOutMsg = messaging.PowerNodeUsageMsg()

    def UpdateState(self, current_ns):
        payload = messaging.PowerNodeUsageMsgPayload()
        payload.netPower = -4.0
        self.electricalPowerOutMsg.write(payload, current_ns, self.moduleID)


def test_sysmodel_inheritance_does_not_make_unknown_provider_official():
    Binding, build = _api()
    model = UnclassifiedElectricalModel()
    sim, scheduled = _run(model)
    result = build("unknown_source", [Binding(model, "electricalPowerOutMsg", "unknown")], scheduled_models=scheduled)

    assert result["status"] == "ERROR"
    assert result["counts"]["electrical_provider_count"] == 1
    assert result["counts"]["official_provider_count"] == 0
    assert result["counts"]["unknown_provider_count"] == 1
    assert result["authorities"][0]["origin"] == "unknown"
    assert result["authorities"][0]["message_evidence"]["net_power_w"] == -4.0
    assert "UNKNOWN_ELECTRICAL_PROVIDER" in _codes(result)


def test_unwritten_default_payload_is_not_evidence_of_a_written_zero_load():
    Binding, build = _api()
    sink = _sink(watts=0.0)
    binding = Binding(sink, "nodePowerOutMsg", "zero_load")
    before = build("before_native_update", [binding], scheduled_models=[sink])
    assert before["status"] == "NOT_READY"
    evidence = before["authorities"][0]["message_evidence"]
    assert evidence["written"] is False
    assert evidence["read_status"] == "NOT_READY"
    assert evidence["net_power_w"] is None
    assert evidence["source_time_ns"] is None

    sim, scheduled = _run(sink, duration_s=0.0)
    after = build("after_native_update", [binding], scheduled_models=scheduled)
    assert after["status"] == "PASS"
    evidence = after["authorities"][0]["message_evidence"]
    assert evidence["written"] is True
    assert evidence["read_status"] == "VALID"
    assert evidence["net_power_w"] == 0.0
    assert evidence["source_time_ns"] == 0


@pytest.mark.parametrize("power", [math.nan, math.inf, -math.inf])
def test_nonfinite_native_power_is_reported_without_nonfinite_json(power):
    Binding, build = _api()
    sink = _sink(watts=power)
    sim, scheduled = _run(sink)
    result = build("invalid_power", [Binding(sink, "nodePowerOutMsg", "bus")], scheduled_models=scheduled)

    assert result["status"] == "ERROR"
    assert result["authorities"][0]["message_evidence"]["written"] is True
    assert result["authorities"][0]["message_evidence"]["read_status"] == "ERROR"
    assert result["authorities"][0]["message_evidence"]["net_power_w"] is None
    assert "INVALID_ELECTRICAL_MESSAGE" in _codes(result)
    json.dumps(result, allow_nan=False)


def test_written_heater_output_does_not_hide_failed_enable_input():
    Binding, build = _api()
    source = messaging.DeviceStatusMsg()
    heater = ThermalHeater("battery", HeaterParams(power_w=12.0))
    heater.forced_state = True
    heater.subscribe_enable(source)
    sim, scheduled = _run(heater)
    result = build("failed_heater_input", [Binding(heater, "electricalPowerOutMsg", "heater")], scheduled_models=scheduled)

    entry = result["authorities"][0]
    assert entry["message_evidence"]["read_status"] == "VALID"
    assert entry["message_evidence"]["net_power_w"] == 0.0
    assert entry["model_read_evidence"]["status"] == "ERROR"
    assert result["status"] == "ERROR"
    assert "MODEL_INPUT_ERROR" in _codes(result)


def test_recovered_status_adapter_keeps_cumulative_input_failure_evidence():
    _, build = _api()
    source = messaging.THROutputMsg()
    adapter = ThrusterActivityStatusAdapter("thrusterStatus", thruster_output_msg=source)
    sim, scheduled = _run(adapter)
    payload = messaging.THROutputMsgPayload()
    payload.thrustFactor = 1.0
    source.write(payload, 600_000_000)
    adapter.UpdateState(600_000_000)
    result = build("recovered_status_input", [], status_adapters=[adapter], scheduled_models=scheduled)

    assert str(adapter.trace[-1].input_status) == "VALID"
    assert result["counts"]["electrical_provider_count"] == 0
    assert result["status_adapters"][0]["model_read_evidence"]["input_error_count"] == 2
    assert result["status_adapters"][0]["model_read_evidence"]["status"] == "ERROR"
    assert result["status"] == "ERROR"
    assert "MODEL_INPUT_ERROR" in _codes(result)


def test_missing_declared_port_does_not_invent_a_provider_or_hide_real_one():
    Binding, build = _api()
    sink = _sink()
    sim, scheduled = _run(sink)
    result = build("wrong_port", [Binding(sink, "missingPowerOutMsg", "bus")], scheduled_models=scheduled)

    assert result["status"] == "ERROR"
    assert result["counts"]["electrical_provider_count"] == 1
    assert result["counts"]["unregistered_provider_count"] == 1
    assert "MISSING_ELECTRICAL_OUTPUT" in _codes(result)
    assert "UNREGISTERED_ELECTRICAL_PROVIDER" in _codes(result)


def test_full_scheduled_census_identifies_native_fsw_without_inventing_power():
    _, build = _api()
    from Basilisk.fswAlgorithms import inertial3D

    reference = inertial3D.inertial3D()
    reference.ModelTag = "nativeReference"
    recorder = reference.attRefOutMsg.recorder()
    sim, scheduled = _run(reference, recorder)
    result = build("native_fsw_census", [], scheduled_models=scheduled)

    assert result["status"] == "PASS"
    assert result["counts"]["electrical_provider_count"] == 0
    assert result["counts"]["scheduled_model_count"] == 2
    assert result["counts"]["scheduled_recorder_count"] == 1
    entry = next(row for row in result["scheduled_models"] if row["model_tag"] == "nativeReference")
    assert entry["model_class"] == "Basilisk.fswAlgorithms.inertial3D.inertial3D"
    assert entry["origin"] == "official"
    assert entry["electrical_provider_ids"] == []


def test_reduced_configured_zero_watt_heater_remains_a_project_authority():
    Binding, build = _api()
    from components.thermal_node.basilisk_scheduled import ThermalNodeScheduledConfig, ThermalNodeScheduledSysModel

    configured = ThermalNodeScheduledSysModel(ThermalNodeScheduledConfig(
        node_name="configured", heater_power_w=0.0, heater_hardware_present=True,
    ))
    absent = ThermalNodeScheduledSysModel(ThermalNodeScheduledConfig(
        node_name="absent", heater_hardware_present=False,
    ))
    heat_msg = messaging.PowerNodeUsageMsg().write(messaging.PowerNodeUsageMsgPayload())
    configured.heatInMsg.subscribeTo(heat_msg)
    absent.heatInMsg.subscribeTo(heat_msg)
    sim, scheduled = _run(configured, absent)
    result = build("reduced_hardware", [Binding(configured, "electricalPowerOutMsg", "heater_load")], scheduled_models=scheduled)

    assert result["status"] == "PASS"
    assert result["counts"]["electrical_provider_count"] == 1
    assert result["counts"]["project_physical_provider_count"] == 1
    assert result["counts"]["unregistered_provider_count"] == 0
    assert result["authorities"][0]["message_evidence"]["net_power_w"] == 0.0
    rows = {row["model_tag"]: row for row in result["scheduled_models"]}
    assert rows["configuredThermalNode"]["heater_hardware_present"] is True
    assert rows["absentThermalNode"]["heater_hardware_present"] is False
    assert rows["absentThermalNode"]["electrical_provider_ids"] == []


def test_thermal_startup_not_ready_diagnostic_is_not_reported_as_input_error():
    Binding, build = _api()
    sink = _sink()
    bridge = ThermalPowerBridge("startup")
    bridge.add_power_input(sink.nodePowerOutMsg, 1.0, source_label="unwritten_load")
    bridge.Reset(0)
    assert bridge.input_read_status == "NOT_READY"
    assert bridge.input_error_count == 0

    result = build("thermal_startup", [Binding(sink, "nodePowerOutMsg", "bus")],
                   thermal_bridges=[bridge], scheduled_models=[sink, bridge])

    assert result["status"] == "NOT_READY"
    assert result["errors"] == []
    evidence = result["thermal_bridges"][0]["model_read_evidence"]
    assert evidence["status"] == "NOT_READY"
    assert evidence["input_error_count"] == 0
    assert evidence["errors"]


def test_heater_heat_output_cannot_be_registered_as_its_electrical_authority():
    Binding, build = _api()
    heater = ThermalHeater("battery", HeaterParams(power_w=12.0, efficiency=0.5))
    heater.forced_state = True
    sim, scheduled = _run(heater)
    result = build("wrong_heater_port", [Binding(heater, "powerOutMsg", "heater_load")], scheduled_models=scheduled)

    assert result["status"] == "ERROR"
    assert result["counts"]["electrical_provider_count"] == 1
    assert result["counts"]["unregistered_provider_count"] == 1
    assert result["authorities"][0]["output_attr"] == "electricalPowerOutMsg"
    assert result["authorities"][0]["message_evidence"]["net_power_w"] == -12.0
    assert "NON_ELECTRICAL_OUTPUT" in _codes(result)


def test_history_only_status_adapter_preserves_transient_error_count_after_recovery():
    _, build = _api()
    source = messaging.DataNodeUsageMsg()
    permit = messaging.DeviceStatusMsgPayload()
    permit.deviceStatus = 1
    permit_msg = messaging.DeviceStatusMsg().write(permit)
    adapter = PayloadActivityStatusAdapter("payloadStatus", data_msg=source, enable_status_msg=permit_msg)
    adapter.Reset(0)
    payload = messaging.DataNodeUsageMsgPayload()
    payload.baudRate = math.nan
    source.write(payload, 200_000_000)
    adapter.UpdateState(200_000_000)
    payload.baudRate = 100.0
    source.write(payload, 400_000_000)
    adapter.UpdateState(400_000_000)
    result = build("recovered_history_only", [], status_adapters=[adapter], scheduled_models=[adapter])

    assert not hasattr(adapter, "input_error_count")
    assert str(adapter.trace[-1].input_status) == "VALID"
    evidence = result["status_adapters"][0]["model_read_evidence"]
    assert evidence["status"] == "ERROR"
    assert evidence["input_error_count"] == 1
    assert evidence["retained_error_sample_count"] == 1
    assert evidence["errors"]
    assert result["status"] == "ERROR"


def test_startup_not_ready_history_is_not_a_cumulative_input_failure():
    _, build = _api()
    source = messaging.DataNodeUsageMsg()
    permit = messaging.DeviceStatusMsgPayload()
    permit.deviceStatus = 1
    permit_msg = messaging.DeviceStatusMsg().write(permit)
    adapter = PayloadActivityStatusAdapter("payloadStatus", data_msg=source, enable_status_msg=permit_msg)
    adapter.Reset(0)
    payload = messaging.DataNodeUsageMsgPayload()
    payload.baudRate = 100.0
    source.write(payload, 200_000_000)
    adapter.UpdateState(200_000_000)
    result = build("startup_history_only", [], status_adapters=[adapter], scheduled_models=[adapter])

    assert str(adapter.trace[0].input_status) == "NOT_READY"
    assert str(adapter.trace[-1].input_status) == "VALID"
    evidence = result["status_adapters"][0]["model_read_evidence"]
    assert evidence["status"] == "VALID"
    assert evidence["input_error_count"] == 0
    assert evidence["retained_error_sample_count"] == 0
    assert result["status"] == "PASS"
