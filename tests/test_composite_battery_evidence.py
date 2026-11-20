"""Native battery state, timing and observed-window evidence for the composite."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest


def _graph(**overrides):
    pytest.importorskip("Basilisk", reason="requires official Basilisk 2.11.0")
    from whole_spacecraft.builder import build_whole_spacecraft_graph
    from whole_spacecraft.schemas import WholeSpacecraftConfig

    cfg = replace(WholeSpacecraftConfig(
        initial_soc=0.9, solar_power_w=0.0, recorder_step_s=0.5,
        thermal_step_s=10.0, propulsion_on_time_s=(0.8, 0.0),
    ), **overrides)
    return build_whole_spacecraft_graph(cfg), cfg


def _run(graph, cfg, duration_s=4.0):
    from whole_spacecraft.runner import _execute_whole_spacecraft_graph
    from whole_spacecraft.schemas import WholeSpacecraftRunConfig

    return _execute_whole_spacecraft_graph(graph, WholeSpacecraftRunConfig(
        duration_s=duration_s, sample_s=cfg.recorder_step_s, structure=cfg,
    ))


def test_cold_start_uses_observed_native_window_and_preserves_public_indices():
    graph, cfg = _graph()
    summary, rows = _run(graph, cfg)
    native = graph.recorders["conservation_battery"]
    public = graph.recorders["battery"]

    # The official battery really wrote this zero payload. Preserve the raw
    # record; it is not a measured empty battery with positive capacity.
    assert int(native.timesWritten()[0]) == 0
    assert float(native.storageCapacity[0]) == 0.0
    assert float(native.storageLevel[0]) == 0.0
    assert float(native.currentNetPower[0]) == 0.0
    assert summary.energy_conservation_status == "PASS"
    assert summary.energy_balance_residual_j == pytest.approx(0.0, abs=1e-7)
    assert summary.battery_evidence_status == "PASS"
    assert summary.battery_startup_excluded_sample_count == 1
    assert summary.battery_evidence_start_s == pytest.approx(0.2)
    assert summary.battery_evidence_end_s == pytest.approx(4.0)
    assert summary.battery_evidence_sample_count == 20
    np.testing.assert_array_equal(native.times(), graph.task_cadence_probes["dynamics"].update_times_ns)
    np.testing.assert_array_equal(native.timesWritten(), native.times())

    expected_integral = sum(
        float(native.currentNetPower[i]) * float(native.timesWritten()[i] - native.timesWritten()[i - 1]) * 1e-9
        for i in range(2, len(native.times()))
    )
    assert summary.integrated_net_power_j == pytest.approx(expected_integral, abs=1e-10)
    assert summary.battery_storage_delta_j == pytest.approx(
        float(native.storageLevel[-1] - native.storageLevel[1]), abs=1e-10,
    )
    assert len(rows) == len(public.times()) - 1
    assert rows[0].time_s == pytest.approx(0.5)
    assert rows[0].battery_source_time_s == pytest.approx(0.4)
    for row, raw_index in zip(rows, range(1, len(public.times()))):
        assert row.time_s == pytest.approx(float(public.times()[raw_index]) * 1e-9)
        assert row.battery_source_time_s == pytest.approx(float(public.timesWritten()[raw_index]) * 1e-9)
        assert row.battery_capacity_j == float(public.storageCapacity[raw_index])
        assert row.battery_storage_j == float(public.storageLevel[raw_index])
        assert row.net_power_w == float(public.currentNetPower[raw_index])
        assert row.data_storage_bits == float(graph.recorders["storage"].storageLevel[raw_index])
        assert row.instrument_baud_bps == float(graph.recorders["instrument"].baudRate[raw_index])
        assert row.propellant_remaining_kg == float(graph.recorders["fuel_tank"].fuelMass[raw_index])
        assert row.spacecraft_position_x_m == float(graph.recorders["spacecraft_state"].r_BN_N[raw_index][0])


def test_public_cadence_does_not_change_native_battery_or_physical_dynamics():
    fine_graph, fine_cfg = _graph(recorder_step_s=0.2)
    coarse_graph, coarse_cfg = _graph(recorder_step_s=0.5)
    fine_summary, _ = _run(fine_graph, fine_cfg)
    coarse_summary, _ = _run(coarse_graph, coarse_cfg)
    for name, fields in (
        ("conservation_battery", ("storageLevel", "storageCapacity", "currentNetPower")),
        ("propulsion_thruster_0", ("thrustFactor", "thrustForce", "thrustForce_B")),
        ("propulsion_thruster_1", ("thrustFactor", "thrustForce", "thrustForce_B")),
    ):
        fine, coarse = fine_graph.recorders[name], coarse_graph.recorders[name]
        np.testing.assert_array_equal(fine.times(), coarse.times())
        for field in fields:
            np.testing.assert_array_equal(getattr(fine, field), getattr(coarse, field))
    # The public recorders use different grids; compare the actual common
    # scheduled snapshots rather than interpolate the orbit or propellant.
    for name, fields in (("spacecraft_state", ("r_BN_N", "v_BN_N")), ("fuel_tank", ("fuelMass", "fuelMassDot"))):
        fine, coarse = fine_graph.recorders[name], coarse_graph.recorders[name]
        common = sorted(set(map(int, fine.times())) & set(map(int, coarse.times())))
        assert len(common) > 1
        for timestamp in common:
            i = list(fine.times()).index(timestamp)
            j = list(coarse.times()).index(timestamp)
            for field in fields:
                np.testing.assert_array_equal(getattr(fine, field)[i], getattr(coarse, field)[j])
    assert fine_summary.battery_evidence_start_s == coarse_summary.battery_evidence_start_s
    assert fine_summary.battery_evidence_end_s == coarse_summary.battery_evidence_end_s
    assert fine_summary.integrated_net_power_j == coarse_summary.integrated_net_power_j
    assert fine_summary.energy_balance_residual_j == coarse_summary.energy_balance_residual_j


def _official_battery_recording():
    pytest.importorskip("Basilisk", reason="requires official Basilisk 2.11.0")
    from Basilisk.architecture import messaging
    from Basilisk.simulation import simpleBattery
    from Basilisk.utilities import SimulationBaseClass, macros

    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("batteryEvidenceProcess")
    process.addTask(sim.CreateNewTask("batteryEvidenceTask", macros.sec2nano(0.2)))
    payload = messaging.PowerNodeUsageMsgPayload()
    payload.netPower = -10.0
    power = messaging.PowerNodeUsageMsg().write(payload)
    battery = simpleBattery.SimpleBattery()
    battery.storageCapacity = 100.0
    battery.storedCharge_Init = 80.0
    battery.addPowerNodeToModel(power)
    recorder = battery.batPowerOutMsg.recorder()
    sim.AddModelToTask("batteryEvidenceTask", battery, None, 10)
    sim.AddModelToTask("batteryEvidenceTask", recorder, None, 0)
    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(1.0))
    sim.ExecuteSimulation()
    return recorder


def test_valid_native_time_zero_state_is_retained():
    from whole_spacecraft.battery_evidence import check_native_battery_evidence

    recorder = _official_battery_recording()
    evidence = check_native_battery_evidence(
        recorder, expected_update_times_ns=range(0, 1_000_000_001, 200_000_000),
    )
    assert evidence.startup_excluded_sample_count == 0
    assert evidence.start_s == 0.0
    assert evidence.end_s == 1.0
    assert len(evidence.samples) == 6
    assert evidence.storage_delta_j == pytest.approx(-10.0)
    assert evidence.integrated_net_power_j == pytest.approx(-10.0)
    assert evidence.min_soc == pytest.approx(0.7)


def test_true_empty_native_battery_keeps_zero_soc_and_clamp_residual():
    graph, cfg = _graph(initial_soc=0.0)
    summary, rows = _run(graph, cfg)
    native = graph.recorders["conservation_battery"]
    assert all(float(value) > 0.0 for value in native.storageCapacity[1:])
    assert all(float(value) == 0.0 for value in native.storageLevel[1:])
    assert summary.battery_evidence_status == "PASS"
    assert summary.battery_startup_excluded_sample_count == 1
    assert summary.min_soc == 0.0
    assert all(row.battery_soc == 0.0 for row in rows)
    assert summary.battery_storage_delta_j == 0.0
    assert summary.energy_balance_residual_j == pytest.approx(-summary.integrated_net_power_j)
    assert summary.energy_balance_residual_j > summary.energy_balance_tolerance_j
    assert summary.energy_conservation_status == "FAIL"
    assert summary.execution_status == "FAIL"


def test_native_zero_soc_between_public_samples_is_not_hidden():
    graph, cfg = _graph(solar_power_w=250.0, recorder_step_s=1.0)
    from Basilisk.architecture import messaging, sysModel

    fault = messaging.PowerStorageFaultMsg()
    graph.component_registry["battery"].batteryFaultInMsg.subscribeTo(fault)

    class MomentaryCapacityLoss(sysModel.SysModel):
        def UpdateState(self, CurrentSimNanos):
            payload = messaging.PowerStorageFaultMsgPayload()
            payload.faultCapacityRatio = 0.0 if int(CurrentSimNanos) == 400_000_000 else 1.0
            fault.write(payload, CurrentSimNanos, self.moduleID)

    owner = MomentaryCapacityLoss()
    owner.ModelTag = "nativeBatteryEvidenceCapacityPulse"
    graph.sim.AddModelToTask("wholeSpacecraftSimTask", owner, None, 56)
    summary, rows = _run(graph, cfg)
    native = graph.recorders["conservation_battery"]
    zero_index = list(native.times()).index(400_000_000)
    assert float(native.storageLevel[zero_index]) == 0.0
    assert float(native.storageCapacity[zero_index]) > 0.0
    assert all(row.battery_soc > 0.0 for row in rows)
    assert summary.battery_evidence_status == "PASS"
    assert summary.min_soc == 0.0
    assert summary.execution_status == "FAIL"


@pytest.mark.parametrize("defect, reason", [("capacity", "INVALID_CAPACITY"), ("zero_payload", "INVALID_CAPACITY"), ("nan", "NONFINITE_PAYLOAD"), ("stale", "STALE_SOURCE_TIME")])
def test_invalid_actual_native_output_between_public_samples_fails_after_recovery(defect, reason):
    graph, cfg = _graph(recorder_step_s=1.0)
    from Basilisk.architecture import messaging, sysModel

    output = graph.component_registry["battery"].batPowerOutMsg

    class InvalidNativeOutput(sysModel.SysModel):
        def UpdateState(self, CurrentSimNanos):
            if int(CurrentSimNanos) == 400_000_000:
                payload = output.read()
                if defect == "capacity":
                    payload.storageCapacity = 0.0
                if defect == "zero_payload":
                    payload = messaging.PowerStorageStatusMsgPayload()
                if defect == "nan":
                    payload.currentNetPower = float("nan")
                written_at = 200_000_000 if defect == "stale" else CurrentSimNanos
                output.write(payload, written_at, self.moduleID)

    owner = InvalidNativeOutput()
    owner.ModelTag = "nativeBatteryEvidenceInvalidOutput"
    graph.sim.AddModelToTask("wholeSpacecraftSimTask", owner, None, -5)
    with pytest.raises(RuntimeError, match="COMPOSITE_BATTERY_NATIVE_EVIDENCE_INVALID.*" + reason):
        _run(graph, cfg)
    native = graph.recorders["conservation_battery"]
    later_index = list(native.times()).index(600_000_000)
    assert float(native.storageCapacity[later_index]) > 0.0
    assert np.isfinite(float(native.currentNetPower[later_index]))


def _recording(*, times=(0, 200_000_000, 400_000_000), written=None, energy=(0.0, 79.0, 78.0), capacity=(0.0, 100.0, 100.0), power=(0.0, -5.0, -5.0)):
    return SimpleNamespace(
        times=lambda: np.asarray(times),
        timesWritten=lambda: np.asarray(times if written is None else written),
        storageLevel=np.asarray(energy), storageCapacity=np.asarray(capacity),
        currentNetPower=np.asarray(power),
    )


@pytest.mark.parametrize("overrides, reason", [
    ({"capacity": (0.0, 0.0, 100.0)}, "INVALID_CAPACITY"),
    ({"power": (1.0, -5.0, -5.0)}, "INVALID_CAPACITY"),
    ({"energy": (0.0, -1.0, 78.0)}, "OUT_OF_BOUNDS_ENERGY"),
    ({"energy": (0.0, 101.0, 78.0)}, "OUT_OF_BOUNDS_ENERGY"),
    ({"energy": (0.0, float("nan"), 78.0)}, "NONFINITE_PAYLOAD"),
    ({"energy": (0.0, 79.0)}, "FIELD_LENGTH_MISMATCH"),
    ({"power": ((0.0,), (-5.0,), (-5.0,))}, "MALFORMED_FIELD"),
    ({"written": (0, 0, 400_000_000)}, "STALE_SOURCE_TIME"),
    ({"written": (0, 400_000_000, 400_000_000)}, "STALE_SOURCE_TIME"),
    ({"times": (0, 400_000_000, 600_000_000)}, "NATIVE_SAMPLE_GRID_MISMATCH"),
])
def test_malformed_evidence_cannot_be_filtered_into_a_passing_balance(overrides, reason):
    from whole_spacecraft.battery_evidence import NativeBatteryEvidenceError, check_native_battery_evidence

    with pytest.raises(NativeBatteryEvidenceError, match=reason):
        check_native_battery_evidence(_recording(**overrides), expected_update_times_ns=(0, 200_000_000, 400_000_000))


def test_missing_native_fields_and_insufficient_observed_window_fail_specifically():
    from whole_spacecraft.battery_evidence import NativeBatteryEvidenceError, check_native_battery_evidence

    missing = _recording()
    del missing.currentNetPower
    with pytest.raises(NativeBatteryEvidenceError, match="MISSING_FIELD"):
        check_native_battery_evidence(missing, expected_update_times_ns=(0, 200_000_000, 400_000_000))
    with pytest.raises(NativeBatteryEvidenceError, match="MISSING_RECORDER"):
        check_native_battery_evidence(None, expected_update_times_ns=(0, 200_000_000, 400_000_000))
    one_valid = _recording(times=(0, 200_000_000), energy=(0.0, 79.0), capacity=(0.0, 100.0), power=(0.0, -5.0))
    with pytest.raises(NativeBatteryEvidenceError, match="INSUFFICIENT_VALID_SAMPLES"):
        check_native_battery_evidence(one_valid, expected_update_times_ns=(0, 200_000_000))


@pytest.mark.parametrize("duration_s, reason", [(0.2, "INSUFFICIENT_VALID_SAMPLES"), (0.4, "INSUFFICIENT_PUBLIC_SAMPLES")])
def test_native_runs_without_an_observable_interval_or_public_state_fail_specifically(duration_s, reason):
    graph, cfg = _graph()
    with pytest.raises(RuntimeError, match="COMPOSITE_BATTERY_NATIVE_EVIDENCE_INVALID.*" + reason):
        _run(graph, cfg, duration_s=duration_s)


def test_public_stale_or_inconsistent_payload_cannot_override_native_evidence():
    from whole_spacecraft.battery_evidence import NativeBatteryEvidenceError, check_native_battery_evidence, check_public_battery_samples

    evidence = check_native_battery_evidence(_recording(), expected_update_times_ns=(0, 200_000_000, 400_000_000))
    # A report at .3 s legitimately contains the native .2 s output.
    public = _recording(times=(0, 300_000_000), written=(0, 200_000_000), energy=(0.0, 79.0), capacity=(0.0, 100.0), power=(0.0, -5.0))
    samples = check_public_battery_samples(public, native_evidence=evidence)
    assert [sample.record_index for sample in samples] == [1]
    assert samples[0].recorder_time_ns == 300_000_000
    assert samples[0].source_time_ns == 200_000_000
    public.timesWritten = lambda: np.asarray((0, 0))
    with pytest.raises(NativeBatteryEvidenceError, match="STALE_SOURCE_TIME"):
        check_public_battery_samples(public, native_evidence=evidence)
    public.timesWritten = lambda: np.asarray((0, 200_000_000))
    public.storageLevel = np.asarray((0.0, 78.0))
    with pytest.raises(NativeBatteryEvidenceError, match="PUBLIC_SOURCE_MISMATCH"):
        check_public_battery_samples(public, native_evidence=evidence)


def test_capability_result_and_dataset_schema_expose_same_battery_evidence():
    pytest.importorskip("Basilisk", reason="requires official Basilisk 2.11.0")
    from sat_sim.adapters.whole_spacecraft_composite_digital_twin import WholeSpacecraftCompositeDigitalTwinAdapter
    from sat_sim.dataset_writer import _trace_schema

    result = WholeSpacecraftCompositeDigitalTwinAdapter().run({
        "task_id": "p10a7_battery_evidence",
        "capability_id": "whole_spacecraft.composite_digital_twin.v1",
        "task_type": "whole_spacecraft",
        "target": {"level": "whole_spacecraft", "name": "composite_digital_twin", "mode": "nominal"},
        "simulation": {"duration_s": 2.0, "sample_s": 0.5},
        "parameters": {"initial_soc": 0.9, "thermal_step_s": 10.0},
    })
    evidence = result.metadata["conservation_evidence"]
    for field in ("battery_evidence_status", "battery_evidence_start_s", "battery_evidence_end_s", "battery_evidence_sample_count", "battery_startup_excluded_sample_count"):
        assert evidence[field] == result.summary[field]
    assert result.trace_rows[0]["time_s"] == pytest.approx(0.5)
    assert result.trace_rows[0]["battery_source_time_s"] == pytest.approx(0.4)
    units = {item["name"]: item["unit"] for item in _trace_schema(result.trace_rows)}
    assert units["battery_source_time_s"] == "s"
    assert units["battery_capacity_j"] == "J"


def test_standalone_dataset_keeps_checked_trace_fields_and_observed_window(tmp_path):
    pytest.importorskip("Basilisk", reason="requires official Basilisk 2.11.0")
    import csv
    import json
    from pathlib import Path
    from whole_spacecraft.runner import write_whole_spacecraft_native_dataset
    from whole_spacecraft.schemas import WholeSpacecraftConfig, WholeSpacecraftRunConfig

    outputs = write_whole_spacecraft_native_dataset(tmp_path, WholeSpacecraftRunConfig(
        duration_s=2.0, sample_s=0.5,
        structure=WholeSpacecraftConfig(initial_soc=0.9, recorder_step_s=0.5, thermal_step_s=10.0),
    ))
    summary = json.loads(Path(outputs["summary"]).read_text())
    with Path(outputs["trace"]).open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert summary["battery_evidence_status"] == "PASS"
    assert summary["battery_evidence_start_s"] == pytest.approx(0.2)
    assert summary["battery_evidence_end_s"] == pytest.approx(2.0)
    assert len(rows) == summary["sample_count"]
    assert float(rows[0]["time_s"]) == pytest.approx(0.5)
    assert float(rows[0]["battery_source_time_s"]) == pytest.approx(0.4)
    assert all(float(row["battery_capacity_j"]) > 0.0 for row in rows)
