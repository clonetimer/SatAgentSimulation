from __future__ import annotations

import pytest

from sat_sim_kernel import (
    DataType,
    DuplicateIdError,
    EffectKind,
    EffectSpec,
    EvidenceSpec,
    KernelValidationError,
    LocalId,
    ModelDefinition,
    ModelKind,
    ModelPath,
    ModelRef,
    ParameterSpec,
    PortDirection,
    PortSpec,
    ReferenceResolutionError,
)


def test_reaction_wheel_definition_is_immutable_and_hashable_by_content() -> None:
    definition = ModelDefinition(
        model_ref=ModelRef.parse("component.reaction_wheel@1.0.0"),
        kind=ModelKind.COMPONENT,
        title="Reaction wheel",
        parameters=(
            ParameterSpec(LocalId("max_torque_nm"), DataType.NUMBER, unit="N*m", default_is_set=True, default=0.2, minimum=0.0),
            ParameterSpec(LocalId("wheel_index"), DataType.INTEGER, default_is_set=True, default=0, minimum=0),
        ),
        inputs=(
            PortSpec(LocalId("torque_command_nm"), PortDirection.INPUT, DataType.NUMBER, unit="N*m"),
        ),
        outputs=(
            PortSpec(LocalId("speed_rad_s"), PortDirection.OUTPUT, DataType.NUMBER, unit="rad/s"),
        ),
        evidence=(
            EvidenceSpec(LocalId("speed_trace"), ModelPath("reaction_wheel.speed_rad_s"), DataType.NUMBER, unit="rad/s"),
        ),
        effects=(
            EffectSpec(
                LocalId("rw_jamming"),
                EffectKind.FAULT,
                parameters=(ParameterSpec(LocalId("wheel_index"), DataType.INTEGER, required=True, minimum=0),),
                evidence_ids=(LocalId("speed_trace"),),
            ),
        ),
    )
    assert len(definition.content_sha256) == 64
    definition.parameter("max_torque_nm").validate(0.1)
    with pytest.raises(KernelValidationError):
        definition.parameter("max_torque_nm").validate(-0.1)


def test_duplicate_and_invalid_definition_fields_fail() -> None:
    duplicate = ParameterSpec(LocalId("gain"), DataType.NUMBER)
    with pytest.raises(DuplicateIdError):
        ModelDefinition(
            ModelRef.parse("component.controller@1"),
            ModelKind.COMPONENT,
            "Controller",
            parameters=(duplicate, duplicate),
        )
    with pytest.raises(KernelValidationError):
        ParameterSpec(LocalId("gain"), DataType.NUMBER, required=True, default_is_set=True, default=1.0)
    with pytest.raises(KernelValidationError):
        ParameterSpec(LocalId("mode"), DataType.ENUM)


def test_effect_must_reference_declared_evidence() -> None:
    with pytest.raises(ReferenceResolutionError):
        ModelDefinition(
            model_ref=ModelRef.parse("component.reaction_wheel@1"),
            kind=ModelKind.COMPONENT,
            title="RW",
            effects=(
                EffectSpec(LocalId("rw_jamming"), EffectKind.FAULT, evidence_ids=(LocalId("missing_trace"),)),
            ),
        )
