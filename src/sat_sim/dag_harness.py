"""Grounded Simulation DAG harness.

This module keeps the Agent-facing graph layer small and deterministic.  It does
not execute simulations; it validates a persistent DAG and exports registered
TaskSpec/planning artifacts through existing platform code.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .dag_mutations import DAGMutation, SimulationDAG, dag_schema, mutation_schema
from .dag_store import DAGStore
from .dag_validation import validate_dag
from .execution_planner import PlanningResult, plan_task_spec
from .task_models import canonicalize_task_spec
from .unified_agent import normalize_form_task_spec


@dataclass(frozen=True)
class DAGExportResult:
    ok: bool
    task_spec: dict[str, Any] | None = None
    planning: PlanningResult | None = None
    errors: tuple[dict[str, Any], ...] = ()


class SimulationDAGHarness:
    """High-level API used by tests, future CLI commands and Agent tools."""

    def __init__(self, store: DAGStore) -> None:
        self.store = store

    @classmethod
    def from_root(cls, root: str) -> "SimulationDAGHarness":
        return cls(DAGStore(root))

    def create_dag(
        self,
        *,
        dag_id: str,
        actor: str,
        mutation_id: str,
        source_request: str | None = None,
        provenance: Mapping[str, Any] | None = None,
    ) -> tuple[SimulationDAG, DAGMutation]:
        return self.store.create(
            dag_id=dag_id,
            actor=actor,
            mutation_id=mutation_id,
            source_request=source_request,
            provenance=provenance,
        )

    def commit(self, mutation: DAGMutation | Mapping[str, Any]) -> tuple[SimulationDAG, DAGMutation]:
        return self.store.commit(mutation)

    def current(self) -> SimulationDAG:
        return self.store.load()

    def export_taskspec(self, *, node_id: str | None = None) -> DAGExportResult:
        dag = self.current()
        validation = validate_dag(dag)
        if not validation.ok:
            return DAGExportResult(False, errors=tuple(validation.errors))

        candidates = [
            node for node in dag.nodes
            if node.node_type == "taskspec_draft" and (node_id is None or node.node_id == node_id)
        ]
        if not candidates:
            return DAGExportResult(False, errors=({
                "code": "DAG_TASKSPEC_DRAFT_MISSING",
                "path": "$.nodes",
                "message": "no taskspec_draft node is available for export",
            },))
        if len(candidates) > 1 and node_id is None:
            return DAGExportResult(False, errors=({
                "code": "DAG_TASKSPEC_DRAFT_AMBIGUOUS",
                "path": "$.nodes",
                "message": "multiple taskspec_draft nodes exist; pass node_id",
            },))

        payload = candidates[0].payload
        try:
            if isinstance(payload.get("task_spec"), Mapping):
                task_spec = canonicalize_task_spec(payload["task_spec"])
            elif isinstance(payload.get("form"), Mapping):
                task_spec = normalize_form_task_spec(payload["form"])
            else:
                return DAGExportResult(False, errors=({
                    "code": "DAG_TASKSPEC_PAYLOAD_MISSING",
                    "path": f"$.nodes[{candidates[0].node_id}].payload",
                    "message": "taskspec_draft payload requires task_spec or form",
                },))
        except Exception as exc:
            return DAGExportResult(False, errors=({
                "code": "DAG_TASKSPEC_EXPORT_FAILED",
                "path": f"$.nodes[{candidates[0].node_id}].payload",
                "message": str(exc),
            },))
        return DAGExportResult(True, task_spec=task_spec)

    def export_execution_plan(self, *, node_id: str | None = None) -> DAGExportResult:
        exported = self.export_taskspec(node_id=node_id)
        if not exported.ok or exported.task_spec is None:
            return exported
        planning = plan_task_spec(exported.task_spec)
        if not planning.ok:
            errors = tuple(
                {"code": issue.code, "path": issue.path, "message": issue.message}
                for issue in planning.validation.issues
            )
            return DAGExportResult(False, task_spec=exported.task_spec, planning=planning, errors=errors)
        return DAGExportResult(True, task_spec=exported.task_spec, planning=planning)


__all__ = ["DAGExportResult", "SimulationDAGHarness", "dag_schema", "mutation_schema"]
