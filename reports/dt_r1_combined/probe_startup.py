"""Isolate the native battery startup-order effect and compare pointing gains."""
import json
import sys
from pathlib import Path

from Basilisk.simulation import simpleBattery, simplePowerSink
from Basilisk.utilities import SimulationBaseClass, macros

from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle


def battery_probe(soc, sink_priority):
    sim = SimulationBaseClass.SimBaseClass()
    proc = sim.CreateNewProcess("probe")
    proc.addTask(sim.CreateNewTask("tick", macros.sec2nano(1)))
    sink, battery = simplePowerSink.SimplePowerSink(), simpleBattery.SimpleBattery()
    sink.nodePowerOut = -10
    battery.storageCapacity = 10000
    battery.storedCharge_Init = soc * battery.storageCapacity
    battery.addPowerNodeToModel(sink.nodePowerOutMsg)
    recorder = battery.batPowerOutMsg.recorder()
    sim.AddModelToTask("tick", sink, None, sink_priority)
    sim.AddModelToTask("tick", battery, None, 50)
    sim.AddModelToTask("tick", recorder, None, 1)
    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(2))
    sim.ExecuteSimulation()
    assert list(recorder.times()) == [0, 10**9, 2*10**9]
    charge = list(map(float, recorder.storageLevel))
    capacity = list(map(float, recorder.storageCapacity))
    assert capacity == ([10000]*3 if sink_priority > 50 else [0, 10000, 10000])
    assert charge == [soc*10000 if sink_priority > 50 else 0, soc*10000-10, soc*10000-20]
    return dict(initial_soc=soc, sink_priority=sink_priority, energy_j=charge, capacity_j=capacity)


def whole(source, root, name, soc, gains):
    task = json.loads(source.read_bytes())
    task["simulation"].update(step_s=0.25, sample_s=2)
    task["parameters"]["values"].update(initial_soc=soc, controller_k=gains[0], controller_p=gains[1])
    plan = prepare_run(task, output_root=root, run_id=name)
    result = execute_prepared_run(plan.bundle_root, expected_plan_sha256=plan.execution_plan_sha256,
                                  max_attempts=1, hard_timeout=True)
    assert verify_run_bundle(plan.bundle_root)["ok"]
    rows = result.trace_rows
    assert rows[0]["time_s"] == 0 and rows[0]["eps.battery_capacity_j"] == 1e-12
    return dict(run_id=name, initial_soc=soc, gains=gains, initial_capacity_j=rows[0]["eps.battery_capacity_j"],
                initial_reported_soc=rows[0]["eps.battery_soc"], second_frame_soc=rows[1]["eps.battery_soc"],
                pointing_deg=[r["adcs.pointing_error_deg"] for r in rows],
                validation=result.validation.result.value, issues=result.validation.physical_validation["issues"])


if __name__ == "__main__":
    source, root = map(Path, sys.argv[1:])
    probes = [battery_probe(soc, priority) for soc in (0.1, 0.62) for priority in (55, 45)]
    runs = [whole(source, root, name, soc, gains) for name, soc, gains in (
        ("open-loop", 0.62, (0, 0)), ("closed-loop", 0.62, (3.5, 30)), ("low-charge", 0.1, (0, 0)))]
    (root/"warnings.json").write_text(json.dumps(dict(native_probes=probes, whole=runs), indent=2)+"\n", encoding="utf-8")
