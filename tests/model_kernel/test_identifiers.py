from __future__ import annotations

import pytest

from sat_sim_kernel import ImplementationId, KernelValidationError, LocalId, ModelId, ModelPath, ModelRef, ObjectRef


def test_versioned_identifiers_round_trip() -> None:
    model_ref = ModelRef.parse("component.reaction_wheel@1.0.0")
    implementation = ImplementationId.parse("implementation.reaction_wheel.basilisk@1.0.0+satfix1")
    assert str(model_ref.model_id) == "component.reaction_wheel"
    assert str(model_ref) == "component.reaction_wheel@1.0.0"
    assert str(implementation) == "implementation.reaction_wheel.basilisk@1.0.0+satfix1"


@pytest.mark.parametrize(
    "factory,value",
    [
        (ModelId, "reaction_wheel"),
        (ModelId, "Component.reaction_wheel"),
        (ModelRef.parse, "component.reaction_wheel"),
        (ModelRef.parse, "component.reaction_wheel@latest"),
        (LocalId, "Bad ID"),
        (ObjectRef, "spacecraft"),
        (ModelPath, "spacecraft..adcs"),
    ],
)
def test_invalid_identifiers_fail(factory, value: str) -> None:
    with pytest.raises(KernelValidationError):
        factory(value)


def test_object_and_model_paths_support_indexed_nodes() -> None:
    assert str(ObjectRef("spacecraft.adcs.rw[0].inject_jamming")) == "spacecraft.adcs.rw[0].inject_jamming"
    assert str(ModelPath("spacecraft.adcs.rw[0].speed_rad_s")) == "spacecraft.adcs.rw[0].speed_rad_s"
