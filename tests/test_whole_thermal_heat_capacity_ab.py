"""Basilisk-scheduled *project* thermal-network physical perturbation acceptance.

The model is not an official Basilisk thermal module. Real Basilisk runs
schedule the same project thermal nodes under identical spacecraft conditions.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from sat_sim.capability_registry import get_adapter_for_capability, get_capability

WHOLE = "whole_spacecraft.unified_native.v1"


def test_real_basilisk_scheduled_payload_heat_capacity_ab() -> None:
    pytest.importorskip("Basilisk")
    spec = yaml.safe_load(
        Path("examples/whole_spacecraft_unified_native_nominal.yaml").read_text(encoding="utf-8")
    )
    spec["simulation"].update({"duration_s": 45., "step_s": .2, "sample_s": .5})
    spec["parameters"]["values"].update({
        "initial_payload_temp_k": 280.,
        "payload_power_w": 180.,
        "initial_soc": .9,
        "payload_thermal_mass_kg": 3.,
        "payload_specific_heat_j_kg_k": 600.,
    })
    high_inertia = deepcopy(spec)
    high_inertia["parameters"]["values"]["payload_thermal_mass_kg"] = 15.
    contract = get_capability(WHOLE).data
    adapter = get_adapter_for_capability(WHOLE)
    assert not [e for e in adapter.validate(spec, contract) if e.severity == "error"]
    assert not [e for e in adapter.validate(high_inertia, contract) if e.severity == "error"]
    a = adapter.run(spec, contract)
    b = adapter.run(high_inertia, contract)
    def temp_change(result):
        rows = result.trace_rows
        assert len(rows) > 3
        return float(rows[-1]["thermal.payload_temp_k"]) - float(rows[0]["thermal.payload_temp_k"])
    light = temp_change(a)
    heavy = temp_change(b)
    # Same heat flow and model topology, five times the effective heat
    # capacity. The genuine scheduled node must warm more slowly.
    assert light > .05
    assert 0. < heavy < .8 * light
    assert a.metadata["model_source_boundary"]["external_or_proxy"] is not None
