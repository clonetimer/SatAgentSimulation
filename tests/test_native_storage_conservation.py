"""Conservation bounds against actual Basilisk integer-bit storage updates."""
from types import SimpleNamespace

import pytest

from whole_spacecraft.runner import _storage_rounding_budget_bits
from whole_spacecraft.task_cadence import TaskCadenceProbe


def _run_storage(capacity_bits):
    from Basilisk.architecture import messaging
    from Basilisk.simulation import simpleStorageUnit
    from Basilisk.utilities import SimulationBaseClass, macros

    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("storageProcess")
    process.addTask(sim.CreateNewTask("storageTask", macros.sec2nano(0.1)))
    storage = simpleStorageUnit.SimpleStorageUnit()
    storage.storageCapacity = capacity_bits
    messages = []
    for rate in (101.3, -10.7):
        payload = messaging.DataNodeUsageMsgPayload()
        payload.baudRate = rate
        message = messaging.DataNodeUsageMsg().write(payload)
        messages.append(message)
        storage.addDataNodeToModel(message)
    recorder = storage.storageUnitDataOutMsg.recorder()
    probe = TaskCadenceProbe("storageTask", 0.1)
    sim.AddModelToTask("storageTask", storage)
    sim.AddModelToTask("storageTask", recorder)
    sim.AddModelToTask("storageTask", probe)
    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(10))
    sim.ExecuteSimulation()
    return storage, probe, recorder


def test_fractional_native_node_increments_have_integer_rounding_budget():
    storage, probe, recorder = _run_storage(10000)
    # 100 physical updates: round(10.13) + round(-1.07) = 9 bits/update.
    assert recorder.storageLevel[-1] == 900
    continuous_bits = (101.3 - 10.7) * 10
    residual = continuous_bits - recorder.storageLevel[-1]
    assert residual == pytest.approx(6)
    budget = _storage_rounding_budget_bits(storage, probe, recorder)
    assert budget == 100  # 0.5 bit * 2 native input nodes * 100 actual intervals.
    assert abs(residual) <= budget
    # An additional unaccounted 101-bit loss cannot be excused as rounding.
    assert abs(residual + 101) > budget


def test_capacity_loss_is_not_covered_by_native_rounding_budget():
    storage, probe, recorder = _run_storage(100)
    residual = (101.3 - 10.7) * 10 - recorder.storageLevel[-1]
    assert residual > _storage_rounding_budget_bits(storage, probe, recorder)


def test_unknown_storage_implementation_gets_no_native_rounding_allowance():
    assert _storage_rounding_budget_bits(SimpleNamespace(), None, None) == 0
