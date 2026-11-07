"""Frozen holdout failure-class regressions (not replacement holdout scores).

Only parser, explicit parameter and mode inference contracts are tested here.
No test in this file asserts improved real-model inference performance.
"""
from __future__ import annotations

import pytest

from sat_sim.capability_agent import (
    _explicit_time_requirements,
    requested_health_mode,
)
from sat_sim.capability_registry import get_capability
from sat_sim.explicit_parameters import (
    ExplicitParameterError,
    explicit_parameter_assignments,
)


DECLARED_BATTERY = get_capability("component.battery.v1").data["parameters"]


@pytest.mark.parametrize(
    "text",
    [
        "Use component.battery.v1 in mode=default, run 16 seconds.",
        "Use default battery settings, with no injected faults.",
        "使用电池默认配置，保持正常工作。",
        "mode: default，仿真 12 秒，使用 default outputs。",
        "No fault injection; use the registered defaults.",
        "The default nominal mode should not be a fault scenario.",
    ],
)
def test_default_is_not_a_fault_health_mode(text):
    assert requested_health_mode(text) == "nominal"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Use component.battery.v1, mode=fault and inject a fault at 3s.", "fault"),
        ("Inject a battery open circuit fault.", "fault"),
        ("mode=degradation; simulate battery aging.", "degradation"),
        ("Create a normal battery simulation without faults.", "nominal"),
    ],
)
def test_explicit_modes_remain_distinguishable(text, expected):
    assert requested_health_mode(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("仿真10秒、每1秒采样", {"duration_s": 10.0, "sample_s": 1.0}),
        ("仿真时长12秒、采样间隔2秒", {"duration_s": 12.0, "sample_s": 2.0}),
        ("仿真12秒. 每2秒采样", {"duration_s": 12.0, "sample_s": 2.0}),
        ("仿真时长12.5秒、采样间隔0.5秒", {"duration_s": 12.5, "sample_s": 0.5}),
    ],
)
def test_chinese_enumeration_and_sentence_dot_keep_time_bindings(text, expected):
    assert _explicit_time_requirements(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("capacity_wh=160、initial_soc=0.65", {"capacity_wh": 160, "initial_soc": 0.65}),
        ("capacity_wh=160, initial_soc=0.65.", {"capacity_wh": 160, "initial_soc": 0.65}),
        ("initial_soc=0.65. 请生成仿真。", {"initial_soc": 0.65}),
        ("initial_soc=0.65、capacity_wh=160.", {"initial_soc": 0.65, "capacity_wh": 160}),
    ],
)
def test_registered_numeric_assignments_accept_sentence_delimiters(text, expected):
    assert explicit_parameter_assignments(text, DECLARED_BATTERY) == expected


@pytest.mark.parametrize(
    "text",
    [
        "initial_soc=0.6.5",
        "capacity_wh=1e999",
        "unknown_parameter=1、initial_soc=0.65",
    ],
)
def test_punctuation_fixes_do_not_accept_bad_or_unknown_literals(text):
    with pytest.raises(ExplicitParameterError):
        explicit_parameter_assignments(text, DECLARED_BATTERY)
