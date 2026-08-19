from __future__ import annotations

import pytest

from sat_sim_kernel import (
    DataType,
    KernelValidationError,
    LocalId,
    ModelConnection,
    ModelDefinition,
    ModelGraphDefinition,
    ModelKind,
    ModelNode,
    ModelRef,
    PortDirection,
    PortEndpoint,
    PortSpec,
    ReferenceResolutionError,
)


def endpoint(node: str, port: str) -> PortEndpoint:
    return PortEndpoint(LocalId(node), LocalId(port))


def test_graph_resolves_port_contracts(source_model: ModelDefinition, sink_model: ModelDefinition) -> None:
    graph = ModelGraphDefinition(
        graph_ref=ModelRef.parse("subsystem.adcs@1.0.0"),
        kind=ModelKind.SUBSYSTEM,
        title="ADCS",
        nodes=(
            ModelNode(LocalId("sensor"), source_model.model_ref),
            ModelNode(LocalId("controller"), sink_model.model_ref),
        ),
        connections=(ModelConnection(endpoint("sensor", "measurement"), endpoint("controller", "measurement")),),
        exposed_outputs=(endpoint("controller", "command"),),
    )
    models = {source_model.model_ref: source_model, sink_model.model_ref: sink_model}
    graph.validate_with_resolver(models.__getitem__)
    assert len(graph.content_sha256) == 64


def test_control_feedback_cycle_requires_explicit_feedback_flag() -> None:
    nodes = (
        ModelNode(LocalId("controller"), ModelRef.parse("component.controller@1")),
        ModelNode(LocalId("plant"), ModelRef.parse("component.plant@1")),
    )
    forward = ModelConnection(endpoint("controller", "command"), endpoint("plant", "command"))
    return_path = ModelConnection(endpoint("plant", "measurement"), endpoint("controller", "measurement"))
    with pytest.raises(KernelValidationError, match="unmarked cycle"):
        ModelGraphDefinition(ModelRef.parse("subsystem.loop@1"), ModelKind.SUBSYSTEM, "Loop", nodes, (forward, return_path))
    graph = ModelGraphDefinition(
        ModelRef.parse("subsystem.loop@1"),
        ModelKind.SUBSYSTEM,
        "Loop",
        nodes,
        (forward, ModelConnection(return_path.source, return_path.target, feedback=True)),
    )
    assert graph.connections[1].feedback is True


def test_graph_rejects_unknown_nodes_and_port_unit_mismatch(source_model: ModelDefinition) -> None:
    with pytest.raises(ReferenceResolutionError):
        ModelGraphDefinition(
            ModelRef.parse("subsystem.bad@1"),
            ModelKind.SUBSYSTEM,
            "Bad",
            (ModelNode(LocalId("sensor"), source_model.model_ref),),
            (ModelConnection(endpoint("sensor", "measurement"), endpoint("missing", "input")),),
        )

    wrong_sink = ModelDefinition(
        ModelRef.parse("component.controller@2"),
        ModelKind.COMPONENT,
        "Controller",
        inputs=(PortSpec(LocalId("measurement"), PortDirection.INPUT, DataType.NUMBER, unit="rad"),),
    )
    graph = ModelGraphDefinition(
        ModelRef.parse("subsystem.bad_units@1"),
        ModelKind.SUBSYSTEM,
        "Bad units",
        (
            ModelNode(LocalId("sensor"), source_model.model_ref),
            ModelNode(LocalId("controller"), wrong_sink.model_ref),
        ),
        (ModelConnection(endpoint("sensor", "measurement"), endpoint("controller", "measurement")),),
    )
    models = {source_model.model_ref: source_model, wrong_sink.model_ref: wrong_sink}
    with pytest.raises(KernelValidationError, match="unit mismatch"):
        graph.validate_with_resolver(models.__getitem__)
