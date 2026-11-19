"""P10-A2 status-only adapter for official Basilisk antenna power nodes."""
from __future__ import annotations

import math

import pytest

pytest.importorskip("Basilisk")

from Basilisk.architecture import messaging
from Basilisk.utilities import SimulationBaseClass, macros

from integration.resource_feedback import (
    DownlinkAntennaStatusAdapter,
    InputReadStatus,
)


@pytest.mark.parametrize(
    "pdu_enabled,link_active,attempted,delivered,expected_tx,expected_idle",
    [
        (1, 1, 1_000_000.0, 1_000_000.0, 1, 0),
        # Failed packets still consume TX power when an RF attempt occurred.
        (1, 1, 1_000_000.0, 0.0, 1, 0),
        (1, 0, 0.0, 0.0, 0, 1),
        (1, 1, 0.0, 0.0, 0, 1),
        (0, 1, 1_000_000.0, 1_000_000.0, 0, 0),
    ],
)
def test_downlink_antenna_status_adapter_gates_tx_and_idle(
    pdu_enabled, link_active, attempted, delivered, expected_tx, expected_idle,
):
    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("P10A2Process")
    process.addTask(sim.CreateNewTask("P10A2Task", macros.sec2nano(0.2)))

    pdu_payload = messaging.DeviceStatusMsgPayload()
    pdu_payload.deviceStatus = pdu_enabled
    pdu_msg = messaging.DeviceStatusMsg().write(pdu_payload)

    downlink_payload = messaging.DownlinkHandlingMsgPayload()
    downlink_payload.linkActive = link_active
    downlink_payload.attemptedDataRate = attempted
    downlink_payload.deliveredDataRate = delivered
    downlink_msg = messaging.DownlinkHandlingMsg().write(downlink_payload)

    adapter = DownlinkAntennaStatusAdapter(
        "downlinkAntennaStatusAdapter",
        downlink_msg=downlink_msg,
        enable_status_msg=pdu_msg,
    )
    sim.AddModelToTask("P10A2Task", adapter)
    tx_rec = adapter.txStatusOutMsg.recorder()
    idle_rec = adapter.idleStatusOutMsg.recorder()
    sim.AddModelToTask("P10A2Task", tx_rec)
    sim.AddModelToTask("P10A2Task", idle_rec)

    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(0.4))
    sim.ExecuteSimulation()

    assert len(adapter.trace) >= 2
    assert all(int(value) == expected_tx for value in tx_rec.deviceStatus)
    assert all(int(value) == expected_idle for value in idle_rec.deviceStatus)
    assert adapter.trace[-1].input_status is InputReadStatus.VALID
    assert adapter.trace[-1].tx_enabled is bool(expected_tx)
    assert adapter.trace[-1].idle_enabled is bool(expected_idle)


def test_downlink_antenna_status_adapter_rejects_nonfinite_rates_fail_closed():
    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("P10A2InvalidProcess")
    process.addTask(sim.CreateNewTask("P10A2InvalidTask", macros.sec2nano(0.2)))

    pdu_payload = messaging.DeviceStatusMsgPayload()
    pdu_payload.deviceStatus = 1
    pdu_msg = messaging.DeviceStatusMsg().write(pdu_payload)
    downlink_payload = messaging.DownlinkHandlingMsgPayload()
    downlink_payload.linkActive = 1
    downlink_payload.attemptedDataRate = math.nan
    downlink_payload.deliveredDataRate = 0.0
    downlink_msg = messaging.DownlinkHandlingMsg().write(downlink_payload)

    adapter = DownlinkAntennaStatusAdapter(
        "downlinkAntennaStatusAdapter",
        downlink_msg=downlink_msg,
        enable_status_msg=pdu_msg,
    )
    sim.AddModelToTask("P10A2InvalidTask", adapter)
    tx_rec = adapter.txStatusOutMsg.recorder()
    idle_rec = adapter.idleStatusOutMsg.recorder()
    sim.AddModelToTask("P10A2InvalidTask", tx_rec)
    sim.AddModelToTask("P10A2InvalidTask", idle_rec)

    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(0.4))
    sim.ExecuteSimulation()

    assert all(int(value) == 0 for value in tx_rec.deviceStatus)
    assert all(int(value) == 0 for value in idle_rec.deviceStatus)
    assert adapter.trace[-1].input_status is InputReadStatus.ERROR
    assert adapter.trace[-1].error
