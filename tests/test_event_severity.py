from __future__ import annotations

import pytest

from components.fault_base import ParameterEffect
from subsystems.event_severity import event_severity, scale_parameter_effects


def test_event_severity_prefers_explicit_value_and_supports_ui_magnitude() -> None:
    assert event_severity({"severity": 0.7, "magnitude": 0.2}) == pytest.approx(0.7)
    assert event_severity({"magnitude": 0.4}) == pytest.approx(0.4)
    assert event_severity({}) == pytest.approx(1.0)
    assert event_severity({"parameters": {"severity": 0.6}}) == pytest.approx(0.6)


def test_continuous_effect_severity_interpolates_from_nominal_state() -> None:
    effects = (
        ParameterEffect("rate", "multiply", 0.35),
        ParameterEffect("heat", "add", 8.0),
        ParameterEffect("capacity", "degrade_multiply", 0.6),
        ParameterEffect("growth", "grow_add", 10.0),
    )
    scaled = scale_parameter_effects(effects, 0.8)
    assert [item.magnitude for item in scaled] == pytest.approx([0.48, 6.4, 0.48, 8.0])


def test_binary_override_and_absolute_clamp_keep_discrete_semantics() -> None:
    effects = (
        ParameterEffect("channel_enabled", "override", 0.0),
        ParameterEffect("rate_limit", "clamp_max", 100.0),
    )
    scaled = scale_parameter_effects(effects, 0.2)
    assert [item.magnitude for item in scaled] == pytest.approx([0.0, 100.0])


@pytest.mark.parametrize("value", [-0.01, 1.01, float("nan"), float("inf"), "invalid"])
def test_event_severity_rejects_values_outside_normalized_range(value) -> None:
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        event_severity({"severity": value})
