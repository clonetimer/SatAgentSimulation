from __future__ import annotations

import pytest

from sat_sim_kernel import (
    DuplicateIdError,
    ImplementationId,
    KernelValidationError,
    ModelImplementationProfile,
    ModelRef,
)


def test_python_and_basilisk_profiles_can_share_one_model_definition() -> None:
    model_ref = ModelRef.parse("component.reaction_wheel@1.0.0")
    python_profile = ModelImplementationProfile(
        ImplementationId.parse("implementation.reaction_wheel.python@1.0.0"),
        model_ref,
        engine="python",
        fidelity="reduced_order",
        adapter_key="reaction-wheel.python",
        supported_outputs=("speed_rad_s",),
        supported_effects=("rw_jamming",),
    )
    basilisk_profile = ModelImplementationProfile(
        ImplementationId.parse("implementation.reaction_wheel.basilisk@1.0.0"),
        model_ref,
        engine="basilisk",
        fidelity="high",
        adapter_key="reaction-wheel.basilisk",
        supported_outputs=("speed_rad_s", "motor_torque_nm"),
        supported_effects=("rw_jamming", "rw_motor_failure"),
    )
    assert python_profile.model_ref == basilisk_profile.model_ref
    assert python_profile.content_sha256 != basilisk_profile.content_sha256


def test_invalid_or_duplicate_implementation_coverage_fails() -> None:
    with pytest.raises(KernelValidationError):
        ModelImplementationProfile(
            ImplementationId.parse("implementation.rw.python@1"),
            ModelRef.parse("component.reaction_wheel@1"),
            engine="Python 3",
            fidelity="reduced_order",
            adapter_key="rw.python",
        )
    with pytest.raises(DuplicateIdError):
        ModelImplementationProfile(
            ImplementationId.parse("implementation.rw.python@1"),
            ModelRef.parse("component.reaction_wheel@1"),
            engine="python",
            fidelity="reduced_order",
            adapter_key="rw.python",
            supported_outputs=("speed", "speed"),
        )
