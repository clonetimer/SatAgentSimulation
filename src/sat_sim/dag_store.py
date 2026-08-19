"""File-backed store for persistent Simulation DAG state and mutation history."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .dag_mutations import DAGMutation, MutationValidation, SimulationDAG
from .dag_validation import apply_mutation_to_dag, validate_dag, with_state_hash


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class DAGStore:
    """Small JSON-file store with optimistic concurrency by DAG state hash."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.dag_path = self.root / "dag.json"
        self.mutation_log_path = self.root / "mutations.jsonl"

    def exists(self) -> bool:
        return self.dag_path.is_file()

    def load(self) -> SimulationDAG:
        payload = json.loads(self.dag_path.read_text(encoding="utf-8"))
        return with_state_hash(SimulationDAG.model_validate(payload))

    def create(
        self,
        *,
        dag_id: str,
        actor: str,
        mutation_id: str,
        source_request: str | None = None,
        provenance: Mapping[str, Any] | None = None,
        base_dag_hash: str | None = None,
    ) -> tuple[SimulationDAG, DAGMutation]:
        if self.exists():
            raise ValueError("DAG already exists")
        now = _utc_now()
        dag = with_state_hash(SimulationDAG(
            dag_id=dag_id,
            created_at=now,
            updated_at=now,
            source_request=source_request,
            provenance=dict(provenance or {}),
        ))
        validation = validate_dag(dag)
        mutation = DAGMutation(
            mutation_id=mutation_id,
            base_dag_hash=base_dag_hash,
            actor=actor,
            operation="create_dag",
            payload={
                "dag_id": dag_id,
                "source_request": source_request,
                "provenance": dict(provenance or {}),
            },
            validation_result=validation,
            committed=validation.ok,
            new_dag_hash=dag.state_hash if validation.ok else None,
        )
        if validation.ok:
            self._write_dag(dag)
        self._append_mutation(mutation)
        return dag, mutation

    def commit(self, mutation: DAGMutation | Mapping[str, Any]) -> tuple[SimulationDAG, DAGMutation]:
        current = self.load()
        request = mutation if isinstance(mutation, DAGMutation) else DAGMutation.model_validate(dict(mutation))
        if request.base_dag_hash != current.state_hash:
            rejected = request.model_copy(update={
                "validation_result": MutationValidation(
                    ok=False,
                    errors=[{
                        "code": "DAG_STALE_BASE_HASH",
                        "path": "$.base_dag_hash",
                        "message": "base_dag_hash does not match the current DAG state_hash",
                        "current_dag_hash": current.state_hash,
                    }],
                ),
                "committed": False,
                "new_dag_hash": None,
            })
            self._append_mutation(rejected)
            return current, rejected

        try:
            next_dag = apply_mutation_to_dag(current, request.operation, request.payload)
            next_payload = next_dag.model_dump(mode="json")
            next_payload["created_at"] = current.created_at
            next_payload["updated_at"] = _utc_now()
            next_dag = with_state_hash(SimulationDAG.model_validate(next_payload))
            validation = validate_dag(next_dag)
        except Exception as exc:
            validation = MutationValidation(ok=False, errors=[{"code": "DAG_MUTATION_INVALID", "path": "$.payload", "message": str(exc)}])
            rejected = request.model_copy(update={"validation_result": validation, "committed": False, "new_dag_hash": None})
            self._append_mutation(rejected)
            return current, rejected

        committed = validation.ok
        recorded = request.model_copy(update={
            "validation_result": validation,
            "committed": committed,
            "new_dag_hash": next_dag.state_hash if committed else None,
        })
        if committed:
            self._write_dag(next_dag)
        self._append_mutation(recorded)
        return (next_dag if committed else current), recorded

    def _write_dag(self, dag: SimulationDAG) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.dag_path.write_text(dag.model_dump_json(indent=2) + "\n", encoding="utf-8")

    def _append_mutation(self, mutation: DAGMutation) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.mutation_log_path.open("a", encoding="utf-8") as handle:
            handle.write(mutation.model_dump_json() + "\n")


__all__ = ["DAGStore"]
