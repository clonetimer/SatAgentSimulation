from __future__ import annotations

import pytest

from sat_sim_kernel import (
    DataType,
    EvidenceSpec,
    LocalId,
    ModelDefinition,
    ModelKind,
    ModelPath,
    ModelRef,
    PortDirection,
    PortSpec,
)


@pytest.fixture
def source_model() -> ModelDefinition:
    return ModelDefinition(
        model_ref=ModelRef.parse("component.sensor@1.0.0"),
        kind=ModelKind.COMPONENT,
        title="Sensor",
        outputs=(
            PortSpec(LocalId("measurement"), PortDirection.OUTPUT, DataType.NUMBER, unit="deg"),
        ),
        evidence=(
            EvidenceSpec(LocalId("measurement_trace"), ModelPath("sensor.measurement"), DataType.NUMBER, unit="deg"),
        ),
    )


@pytest.fixture
def sink_model() -> ModelDefinition:
    return ModelDefinition(
        model_ref=ModelRef.parse("component.controller@1.0.0"),
        kind=ModelKind.COMPONENT,
        title="Controller",
        inputs=(
            PortSpec(LocalId("measurement"), PortDirection.INPUT, DataType.NUMBER, unit="deg"),
        ),
        outputs=(
            PortSpec(LocalId("command"), PortDirection.OUTPUT, DataType.NUMBER, unit="N*m"),
        ),
    )
