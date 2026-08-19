"""Build a reviewable simulation-evidence graph for AstroGraph projection.

The graph is a candidate projection only. It is never written to Neo4j by this
module and cannot become formal knowledge without named-expert review.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .diagnostic_pipeline_registry import DiagnosticPipeline
from .fault_mechanism_library import FaultMechanism

SIMULATION_EVIDENCE_GRAPH_SCHEMA_VERSION = "sat-sim.simulation-evidence-graph.v1"


def _sha(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def build_simulation_evidence_graph(
    *,
    fault_root: str | Path,
    nominal_root: str | Path,
    mechanism: FaultMechanism,
    pipeline: DiagnosticPipeline,
    physics_report: Mapping[str, Any],
    diagnostic_report: Mapping[str, Any],
    feature_report: Mapping[str, Any],
    stage_results: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    fault_root = Path(fault_root)
    nominal_root = Path(nominal_root)
    fault_contract = _read_json(fault_root / "astrograph" / "dataset_contract.json")
    nominal_contract = _read_json(nominal_root / "astrograph" / "dataset_contract.json")
    fault_gt = fault_contract.get("ground_truth") or {}
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    def node(node_id: str, kind: str, **properties: Any) -> None:
        nodes[node_id] = {"node_id": node_id, "kind": kind, "properties": properties}

    def edge(source: str, relation: str, target: str, **properties: Any) -> None:
        edges.append({"source": source, "relation": relation, "target": target, "properties": properties})

    fault_node = f"fault:{mechanism.fault_id}"
    mechanism_node = f"mechanism:{mechanism.mechanism_id}"
    pipeline_node = f"pipeline:{pipeline.pipeline_id}"
    node(fault_node, "Fault", fault_id=mechanism.fault_id, status=mechanism.status)
    node(mechanism_node, "FaultMechanism", mechanism_id=mechanism.mechanism_id, subsystem=mechanism.raw.get("subsystem"), component=mechanism.raw.get("component"))
    node(pipeline_node, "DiagnosticPipeline", pipeline_id=pipeline.pipeline_id, status=pipeline.status)
    edge(fault_node, "HAS_MECHANISM", mechanism_node)
    edge(fault_node, "DIAGNOSED_BY", pipeline_node)
    edge(pipeline_node, "USES_MECHANISM", mechanism_node)

    concept_ids: set[str] = set()
    for cause in mechanism.raw.get("causes") or []:
        concept_id = str(cause.get("cause_id"))
        node(f"concept:{concept_id}", "MechanismConcept", concept_id=concept_id, concept_kind="cause", description=cause.get("description"))
        concept_ids.add(concept_id)
        edge(mechanism_node, "HAS_CAUSE", f"concept:{concept_id}")
    for effect in mechanism.raw.get("physical_effects") or []:
        concept_id = str(effect.get("effect_id"))
        node(f"concept:{concept_id}", "MechanismConcept", concept_id=concept_id, concept_kind="physical_effect", description=effect.get("description"), state_variable=effect.get("state_variable"), direction=effect.get("direction"))
        concept_ids.add(concept_id)
        edge(mechanism_node, "HAS_EFFECT", f"concept:{concept_id}")
    for propagation in mechanism.raw.get("propagation_chain") or []:
        source_id = str(propagation.get("from"))
        target_id = str(propagation.get("to"))
        for concept_id in (source_id, target_id):
            if concept_id not in concept_ids:
                node(f"concept:{concept_id}", "MechanismConcept", concept_id=concept_id, concept_kind="propagation_state")
                concept_ids.add(concept_id)
        edge(f"concept:{source_id}", str(propagation.get("relation") or "PROPAGATES_TO").upper(), f"concept:{target_id}", stage_id=propagation.get("stage_id"))

    for observable in mechanism.raw.get("observables") or []:
        channel = str(observable.get("channel"))
        observable_node = f"telemetry:{channel}"
        node(observable_node, "TelemetryChannel", channel=channel, evidence_role=observable.get("evidence_role"), model_input_eligible=bool(observable.get("model_input_eligible")), expected_relation=observable.get("expected_relation"))
        edge(mechanism_node, "OBSERVED_AS", observable_node)

    pair_id = str(fault_gt.get("pair_id") or diagnostic_report.get("pair_id") or "unknown")
    fault_experiment = f"experiment:{fault_contract.get('metadata', {}).get('experiment_id') or fault_contract.get('dataset_id')}"
    nominal_experiment = f"experiment:{nominal_contract.get('metadata', {}).get('experiment_id') or nominal_contract.get('dataset_id')}"
    fault_dataset = f"dataset:{fault_contract.get('dataset_id')}"
    nominal_dataset = f"dataset:{nominal_contract.get('dataset_id')}"
    node(fault_experiment, "SimulationExperiment", case_role="fault", pair_id=pair_id, fidelity_level=(fault_contract.get("simulation", {}).get("fidelity") or {}).get("fidelity_level"))
    node(nominal_experiment, "SimulationExperiment", case_role="nominal", pair_id=pair_id, fidelity_level=(nominal_contract.get("simulation", {}).get("fidelity") or {}).get("fidelity_level"))
    node(fault_dataset, "Dataset", dataset_id=fault_contract.get("dataset_id"), training_ready=bool((fault_contract.get("quality") or {}).get("training_ready")))
    node(nominal_dataset, "Dataset", dataset_id=nominal_contract.get("dataset_id"), training_ready=bool((nominal_contract.get("quality") or {}).get("training_ready")))
    edge(fault_experiment, "PRODUCED", fault_dataset)
    edge(nominal_experiment, "PRODUCED", nominal_dataset)
    edge(fault_dataset, "PAIRED_WITH", nominal_dataset, pair_id=pair_id)
    edge(fault_experiment, "INJECTS", fault_node)
    edge(fault_dataset, "VALIDATES", mechanism_node, physics_decision=physics_report.get("decision"), diagnostic_decision=diagnostic_report.get("decision"))

    signature_id = str(diagnostic_report.get("signature_id") or "")
    if signature_id:
        signature_node = f"signature:{signature_id}"
        node(signature_node, "DiagnosticSignature", signature_id=signature_id, signature_sha256=diagnostic_report.get("signature_sha256"), decision=diagnostic_report.get("decision"), diagnostic_score=diagnostic_report.get("diagnostic_score"))
        edge(pipeline_node, "USES_SIGNATURE", signature_node)
        edge(fault_dataset, "HAS_SIGNATURE_EVIDENCE", signature_node)
        for criterion in diagnostic_report.get("criteria") or []:
            criterion_id = str(criterion.get("criterion_id") or "unknown")
            criterion_node = f"criterion:{pair_id}:{criterion_id}"
            node(criterion_node, "SignatureCriterionEvidence", criterion_id=criterion_id, channel=criterion.get("channel"), status=criterion.get("status"), observed=criterion.get("observed"), expected=criterion.get("expected"), evidence_role=criterion.get("evidence_role"), contributes_to_diagnostic_score=criterion.get("contributes_to_diagnostic_score"))
            edge(signature_node, "HAS_CRITERION", criterion_node)
            edge(fault_dataset, "SUPPORTS", criterion_node)

    feature_node = f"feature-vector:{feature_report.get('feature_vector_sha256')}"
    node(feature_node, "DiagnosticFeatureVector", status=feature_report.get("status"), feature_contract_id=feature_report.get("feature_contract_id"), missing_required_model_channels=feature_report.get("missing_required_model_channels"), blocked_derived_features=feature_report.get("blocked_derived_features"))
    edge(fault_dataset, "DERIVES", feature_node)
    edge(pipeline_node, "CONSUMES", feature_node)

    for stage in stage_results:
        stage_id = str(stage.get("stage_id") or "unknown")
        stage_node = f"pipeline-stage:{pipeline.pipeline_id}:{stage_id}"
        node(stage_node, "PipelineStageExecution", stage_id=stage_id, stage_kind=stage.get("kind"), status=stage.get("status"), implementation_ref=stage.get("implementation_ref"))
        edge(pipeline_node, "HAS_STAGE", stage_node)

    graph: dict[str, Any] = {
        "schema_version": SIMULATION_EVIDENCE_GRAPH_SCHEMA_VERSION,
        "graph_id": f"simulation-evidence:{pair_id}:{pipeline.pipeline_id}",
        "fault_id": mechanism.fault_id,
        "pair_id": pair_id,
        "nodes": [nodes[key] for key in sorted(nodes)],
        "edges": sorted(edges, key=lambda item: (item["source"], item["relation"], item["target"])),
        "neo4j_projection": {
            "status": "CANDIDATE_ONLY",
            "formal_knowledge_publishable": False,
            "named_expert_review_required": True,
            "allowed_operations": ["preview", "export_candidate"],
            "forbidden_operations": ["automatic_merge_to_formal_kg", "automatic_fact_assertion"],
        },
        "leakage_guard": {
            "simulator_truth_may_validate_mechanism": True,
            "simulator_truth_may_train_model": False,
        },
    }
    graph["graph_sha256"] = _sha(graph)
    return graph


__all__ = ["SIMULATION_EVIDENCE_GRAPH_SCHEMA_VERSION", "build_simulation_evidence_graph"]
