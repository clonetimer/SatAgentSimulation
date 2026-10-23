"""R9-K runtime verification for a *rebuilt* native Basilisk SimpleBattery wheel.

This test runs only in the dedicated custom-wheel GitHub Actions job, NOT in
the stock bsk==2.11.0 native whole-spacecraft workflow. The test creates a
real C++ SimpleBattery with real Basilisk PowerNodeUsageMsg inputs; serializes
the native SWIG checkpoint to a separate operating-system process; imports
it into a *new* real C++ SimpleBattery; then compares four native C++ update
epochs to the original uninterrupted module.

No Python battery propagation, no imitation of hidden C++ fields, and NO
assertion that the whole spacecraft or all EPS message histories recovered.
"""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import pytest

_FIELDS = (
    "schemaVersion", "captureTimeNs", "storageCapacityJ",
    "storedChargeJ", "previousTimeS", "currentTimestepS",
    "currentPowerSumW", "faultCapacityRatio",
    "outputStorageCapacityJ", "outputStorageLevelJ", "outputNetPowerW",
)
_EPOCHS = (1_000_000_000, 1_200_000_000, 1_400_000_000, 1_600_000_000)


def _real_battery():
    from Basilisk.architecture import messaging
    from Basilisk.simulation import simpleBattery

    module = simpleBattery.SimpleBattery()
    module.storedCharge_Init = 100.0
    module.storageCapacity = 1000.0
    power = messaging.PowerNodeUsageMsgPayload()
    power.netPower = 5.0
    node = messaging.PowerNodeUsageMsg().write(power)
    module.addPowerNodeToModel(node)
    module.Reset(0)
    return module, node


def _read_native_output(module):
    msg = module.batPowerOutMsg.read()
    out = {
        "level_j": float(msg.storageLevel),
        "capacity_j": float(msg.storageCapacity),
        "net_power_w": float(msg.currentNetPower),
    }
    assert all(math.isfinite(x) for x in out.values())
    return out


def _serialize(snapshot):
    return {key: getattr(snapshot, key) for key in _FIELDS}


def _reconstruct(doc):
    from Basilisk.simulation import simpleBattery

    assert set(doc) == set(_FIELDS)
    snapshot = simpleBattery.SimpleBatteryCheckpointState()
    for key, value in doc.items():
        setattr(snapshot, key, value)
    return snapshot


def _run_child(path: str):
    from Basilisk.simulation import simpleBattery
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    assert os.getpid() != doc["source_pid"]
    assert hasattr(simpleBattery, "SimpleBatteryCheckpointState")

    module, node = _real_battery()
    native = _reconstruct(doc["snapshot"])
    # The C++ Message wrapper is not obliged to expose isWritten();
    # readback is from the actual protected native checkpoint after import.
    module.importCheckpointState(native, doc["checkpoint_ns"])
    readback = module.exportCheckpointState(doc["checkpoint_ns"])
    assert _serialize(readback) == doc["snapshot"]
    assert float(readback.previousTimeS) == pytest.approx(0.8)
    assert float(readback.storedChargeJ) == pytest.approx(104.0)

    measurements = []
    for t in _EPOCHS:
        module.UpdateState(t)
        measurements.append({
            "time_ns": t,
            "out": _read_native_output(module),
            "native_stored_charge_j": float(module.exportCheckpointState(t).storedChargeJ),
            "native_previous_time_s": float(module.exportCheckpointState(t).previousTimeS),
        })

    print("R9K_NATIVE_BATTERY_CHILD_JSON:" + json.dumps({
        "source_pid": doc["source_pid"],
        "child_pid": os.getpid(),
        "imported_native_snapshot": _serialize(readback),
        "samples": measurements,
        "whole_spacecraft_resume_supported": False,
        "message_histories_restored": False,
        "automatic_tc_replay_allowed": False,
    }, sort_keys=True, allow_nan=False), flush=True)


def test_r9k_rebuilt_native_wheel_fresh_pid_battery_integrator_parity(tmp_path):
    from Basilisk.simulation import simpleBattery
    assert hasattr(simpleBattery, "SimpleBatteryCheckpointState"), (
        "R9K_CUSTOM_WHEEL_NOT_LOADED"
    )
    assert callable(getattr(simpleBattery.SimpleBattery, "exportCheckpointState", None))
    assert callable(getattr(simpleBattery.SimpleBattery, "importCheckpointState", None))

    source, node = _real_battery()
    for t in (0, 200_000_000, 400_000_000, 600_000_000, 800_000_000):
        source.UpdateState(t)
    native = source.exportCheckpointState(800_000_000)
    data = _serialize(native)
    assert data["schemaVersion"] == 1
    assert data["captureTimeNs"] == 800_000_000
    assert data["storedChargeJ"] == pytest.approx(104.0)
    assert data["previousTimeS"] == pytest.approx(0.8)
    assert data["currentTimestepS"] == pytest.approx(0.2)
    assert data["currentPowerSumW"] == pytest.approx(5.0)
    assert data["outputStorageLevelJ"] == pytest.approx(data["storedChargeJ"])

    # Falsified C++ snapshot must fail BEFORE it can change native module.
    probe, probe_node = _real_battery()
    probe.UpdateState(200_000_000)
    protected_before = _serialize(probe.exportCheckpointState(200_000_000))
    for field, bad in (
        ("captureTimeNs", 900_000_000),
        ("storageCapacityJ", 2000.0),
        ("storedChargeJ", -1.0),
        ("previousTimeS", 2.0),
        ("currentTimestepS", -0.1),
        ("faultCapacityRatio", 1.5),
        ("outputStorageLevelJ", -123.0),
    ):
        altered = dict(data)
        altered[field] = bad
        with pytest.raises(Exception, match="R9J_IMPORT_NATIVE_BATTERY_INVALID"):
            probe.importCheckpointState(_reconstruct(altered), 800_000_000)
        assert _serialize(probe.exportCheckpointState(200_000_000)) == protected_before

    # Target process is not a fabricated model; it imports actual exported
    # C++ protected state through a compiled SWIG native wrapper.
    file = tmp_path / "native-true-battery.json"
    file.write_text(json.dumps({
        "source_pid": os.getpid(),
        "checkpoint_ns": 800_000_000,
        "snapshot": data,
    }, sort_keys=True), encoding="utf-8")
    worker = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--r9k-child", str(file)],
        capture_output=True, text=True, timeout=90, check=False,
    )
    assert worker.returncode == 0, worker.stdout + "\n" + worker.stderr
    lines = [
        line.removeprefix("R9K_NATIVE_BATTERY_CHILD_JSON:")
        for line in worker.stdout.splitlines()
        if line.startswith("R9K_NATIVE_BATTERY_CHILD_JSON:")
    ]
    assert len(lines) == 1
    child = json.loads(lines[0])
    assert child["child_pid"] != os.getpid()
    assert child["source_pid"] == os.getpid()
    assert child["whole_spacecraft_resume_supported"] is False
    assert child["message_histories_restored"] is False
    assert child["automatic_tc_replay_allowed"] is False
    assert child["imported_native_snapshot"] == data

    for t, actual in zip(_EPOCHS, child["samples"]):
        source.UpdateState(t)
        live = _read_native_output(source)
        current = source.exportCheckpointState(t)
        assert actual["time_ns"] == t
        for key in live:
            assert actual["out"][key] == pytest.approx(live[key], abs=1e-12, rel=0)
        assert actual["native_stored_charge_j"] == pytest.approx(
            current.storedChargeJ, abs=1e-12, rel=0,
        )
        assert actual["native_previous_time_s"] == pytest.approx(
            current.previousTimeS, abs=1e-12, rel=0,
        )

    print("R9K_REBUILT_NATIVE_CPP_BATTERY_MULTI_STEP_PARITY_PASS:" +
          json.dumps({"epochs": list(_EPOCHS), "source_pid": os.getpid(),
                      "child_pid": child["child_pid"], "whole_resume": False}))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--r9k-child":
        _run_child(sys.argv[2])
    else:
        raise SystemExit("R9K_TEST_ONLY_CHILD")
