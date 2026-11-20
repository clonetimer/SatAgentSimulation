"""统计入口区分未就绪占位消息与真实空电池，不能抹掉后续异常。"""
import numpy as np
import pytest

from scripts.run_external_reference_alignment import battery_sample_mask


@pytest.mark.parametrize("initial_capacity", [0.0, 1e-12])
def test_unready_battery_startup_excludes_native_zero_and_unified_display_floor(initial_capacity):
    mask = battery_sample_mask(
        np.array([0., .3, .6]), np.array([0., 10., 11.]),
        np.array([initial_capacity, 20., 20.]), np.array([0., 2., 3.]),
    )
    assert mask.tolist() == [False, True, True]


def test_real_empty_battery_with_valid_capacity_remains_an_observation():
    mask = battery_sample_mask(
        np.array([0., .3]), np.array([0., 0.]),
        np.array([20., 20.]), np.array([0., -1.]),
    )
    assert mask.tolist() == [True, True]


@pytest.mark.parametrize("later_capacity", [0., 1e-12, -1., np.nan])
def test_later_invalid_battery_message_fails_instead_of_being_silently_filtered(later_capacity):
    with pytest.raises(ValueError):
        battery_sample_mask(
            np.array([0., .3]), np.array([10., 0.]),
            np.array([20., later_capacity]), np.array([2., 0.]),
        )


def test_nonzero_startup_power_is_not_excused_as_an_unready_zero_message():
    with pytest.raises(ValueError):
        battery_sample_mask(
            np.array([0., .3]), np.array([0., 10.]),
            np.array([1e-12, 20.]), np.array([1., 2.]),
        )
