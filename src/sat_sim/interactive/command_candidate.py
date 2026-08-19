"""Human-confirmed natural-language command candidate boundary."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal, Mapping

from pydantic import Field

from .command_catalog import CommandCatalog
from .models import ActorRole, FrozenModel, Identifier, Telecommand, utc_now


class NaturalLanguageCommandCandidate(FrozenModel):
    schema_version: Literal["interactive-command-candidate.v1"] = "interactive-command-candidate.v1"
    candidate_id: Identifier
    session_id: Identifier
    session_revision: int = Field(ge=0)
    source_text: str = Field(min_length=1, max_length=4096)
    operation: Identifier
    target: Identifier
    parameters: dict[str, Any]
    execute_at_sim_time_s: float = Field(ge=0.0)
    actor_id: Identifier
    actor_role: ActorRole
    confirmation_required: Literal[True] = True
    status: Literal["PENDING_CONFIRMATION"] = "PENDING_CONFIRMATION"
    created_at: datetime = Field(default_factory=utc_now)


class TelecommandCandidateCompiler:
    """Validate model output as data; never execute or import generated content."""

    ALLOWED_OUTPUT_FIELDS = {"operation", "target", "parameters", "execute_at_sim_time_s"}

    def __init__(self, catalog: CommandCatalog | None = None) -> None:
        self.catalog = catalog or CommandCatalog.default()

    def compile(
        self,
        source_text: str,
        model_output: Mapping[str, Any],
        *,
        session_id: str,
        session_revision: int,
        actor_id: str,
        actor_role: ActorRole,
    ) -> NaturalLanguageCommandCandidate:
        unknown = set(model_output) - self.ALLOWED_OUTPUT_FIELDS
        if unknown:
            raise ValueError(f"MODEL_OUTPUT_FIELDS_DENIED: {sorted(unknown)}")
        canonical_output = json.dumps(dict(model_output), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        candidate_id = "candidate:" + hashlib.sha256(
            f"{session_id}\n{session_revision}\n{source_text}\n{canonical_output}".encode("utf-8")
        ).hexdigest()[:24]
        draft = Telecommand(
            command_id=candidate_id,
            session_id=session_id,
            session_revision=session_revision,
            operation=str(model_output.get("operation") or ""),
            target=str(model_output.get("target") or ""),
            parameters=dict(model_output.get("parameters") or {}),
            actor_id=actor_id,
            actor_role=actor_role,
            execute_at_sim_time_s=float(model_output.get("execute_at_sim_time_s", 0.0)),
        )
        self.catalog.validate(draft)
        return NaturalLanguageCommandCandidate(
            candidate_id=candidate_id,
            session_id=session_id,
            session_revision=session_revision,
            source_text=source_text,
            operation=draft.operation,
            target=draft.target,
            parameters=draft.parameters,
            execute_at_sim_time_s=draft.execute_at_sim_time_s,
            actor_id=actor_id,
            actor_role=actor_role,
        )

    def confirm(
        self,
        candidate: NaturalLanguageCommandCandidate,
        *,
        confirmed: bool,
        command_id: str,
        expires_at: datetime | None = None,
    ) -> Telecommand:
        if not confirmed:
            raise ValueError("HUMAN_CONFIRMATION_REQUIRED")
        command = Telecommand(
            command_id=command_id,
            session_id=candidate.session_id,
            session_revision=candidate.session_revision,
            operation=candidate.operation,
            target=candidate.target,
            parameters=candidate.parameters,
            actor_id=candidate.actor_id,
            actor_role=candidate.actor_role,
            execute_at_sim_time_s=candidate.execute_at_sim_time_s,
            expires_at=expires_at,
        )
        self.catalog.validate(command)
        return command
