"""Physical A/B regression for *project* thermal components on Basilisk scheduler.

These are true Basilisk SimBaseClass.CreateNewTask/ExecuteSimulation runs, not
post-processed curves. They do NOT assert official Basilisk thermal modules.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from subsystems.thermal.network_config import build_nominal_thermal_network_config
from subsystems.thermal.thermal_network import run_thermal_network_simulation


def test_basilisk_scheduled_radiator_area_ab_rejects_more_heat_and_cools_payload() -> None:
    pytest.importorskip("Basilisk")
    base = build_nominal_thermal_network_config()
    # Expose the radiator's normal deployment regime without violating the
    # payload thermal-node operating bounds (<= 340 K).
    nodes = dict(base.nodes)
    nodes["payload"] = replace(nodes["payload"], initial_temp_k=320.)
    small_radiators = dict(base.radiators)
    small_radiators["payload"] = replace(
        small_radiators["payload"], area_m2=.1,
        min_temp_k=280., max_temp_k=300.,
    )
    large_radiators = dict(small_radiators)
    large_radiators["payload"] = replace(small_radiators["payload"], area_m2=.8)
    run_cfg = replace(base, nodes=nodes, duration_s=60., step_s=1.)
    small, small_trace = run_thermal_network_simulation(
        replace(run_cfg, radiators=small_radiators), eclipse_factor=0.,
    )
    large, large_trace = run_thermal_network_simulation(
        replace(run_cfg, radiators=large_radiators), eclipse_factor=0.,
    )
    def payload_rows(trace):
        return [row for row in trace if row.node_name == "payload" and row.time_s > 0.]

    a, b = payload_rows(small_trace), payload_rows(large_trace)
    assert len(a) == len(b) >= 40
    assert max(x.radiator_rejected_w for x in a) > 0.
    assert max(x.radiator_rejected_w for x in b) > 2 * max(
        x.radiator_rejected_w for x in a
    )
    assert large.final_temps_k["payload"] < small.final_temps_k["payload"] - 1.
    assert large.radiator_activation_count["payload"] > 0
    assert small.radiator_activation_count["payload"] > 0


def test_basilisk_scheduled_battery_heater_setpoint_ab_changes_on_time_and_temp() -> None:
    pytest.importorskip("Basilisk")
    base = build_nominal_thermal_network_config()
    nodes = dict(base.nodes)
    nodes["battery"] = replace(nodes["battery"], initial_temp_k=285.)
    heater_off = dict(base.heaters)
    heater_off["battery"] = replace(
        heater_off["battery"], power_w=30., setpoint_k=275.,
    )
    heater_on = dict(heater_off)
    heater_on["battery"] = replace(heater_off["battery"], setpoint_k=295.)
    run_cfg = replace(base, nodes=nodes, duration_s=120., step_s=1.)
    low, low_trace = run_thermal_network_simulation(
        replace(run_cfg, heaters=heater_off), eclipse_factor=0.,
    )
    high, high_trace = run_thermal_network_simulation(
        replace(run_cfg, heaters=heater_on), eclipse_factor=0.,
    )
    assert len(low_trace) == len(high_trace) >= 100
    assert low.heater_activation_count["battery"] == 0
    assert high.heater_activation_count["battery"] > 0
    assert high.final_temps_k["battery"] > low.final_temps_k["battery"] + .1
    battery_heat = [r.heater_power_w for r in high_trace if r.node_name == "battery"]
    assert max(battery_heat) == pytest.approx(30 * .95)
