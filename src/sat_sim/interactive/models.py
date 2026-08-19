"""Versioned immutable contracts for interactive sessions, TC, ACK, and TM."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

INTERACTIVE_SCHEMA_VERSION = "interactive.v1"
ALLOWED_RATES = (0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0)
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SessionState(StrEnum):
    CREATED = "CREATED"
    PREPARING = "PREPARING"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"
    INTERRUPTED = "INTERRUPTED"


class CommandState(StrEnum):
    RECEIVED = "RECEIVED"
    VALIDATED = "VALIDATED"
    QUEUED = "QUEUED"
    EXECUTING = "EXECUTING"
    ACKED = "ACKED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class ActorRole(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    FAULT_OPERATOR = "fault_operator"
    ADMIN = "admin"


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class InteractiveSessionSpec(FrozenModel):
    schema_version: Literal["interactive.v1"] = INTERACTIVE_SCHEMA_VERSION
    session_id: Identifier
    capability_id: Identifier
    task_spec: dict[str, Any]
    quantum_s: float = Field(default=0.5, gt=0.0, le=10.0)
    rate: float = 1.0
    paced: bool = True
    max_sim_time_s: float = Field(default=3600.0, gt=0.0, le=86400.0)
    max_wall_time_s: float = Field(default=14400.0, gt=0.0, le=86400.0)
    idle_timeout_s: float = Field(default=300.0, gt=0.0, le=3600.0)
    heartbeat_interval_s: float = Field(default=15.0, gt=0.0, le=300.0)
    control_queue_capacity: int = Field(default=128, ge=1, le=4096)
    command_queue_capacity: int = Field(default=1024, ge=1, le=10000)
    telemetry_buffer_frames: int = Field(default=10000, ge=1, le=1000000)
    drift_tolerance_s: float = Field(default=0.2, gt=0.0, le=10.0)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("rate")
    @classmethod
    def validate_rate(cls, value: float) -> float:
        value = float(value)
        if value not in ALLOWED_RATES:
            raise ValueError(f"rate must be one of {ALLOWED_RATES}")
        return value


class SessionSnapshot(FrozenModel):
    schema_version: Literal["interactive.v1"] = INTERACTIVE_SCHEMA_VERSION
    session_id: Identifier
    capability_id: Identifier
    state: SessionState
    revision: int = Field(ge=0)
    sim_time_s: float = Field(ge=0.0)
    rate: float
    paced: bool
    worker_alive: bool
    pace_observation_count: int = Field(default=0, ge=0)
    last_drift_s: float = 0.0
    max_abs_drift_s: float = Field(default=0.0, ge=0.0)
    timing_status: Literal["GOOD", "DEGRADED"] = "GOOD"
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("rate")
    @classmethod
    def validate_rate(cls, value: float) -> float:
        value = float(value)
        if value not in ALLOWED_RATES:
            raise ValueError(f"rate must be one of {ALLOWED_RATES}")
        return value


class SessionEvent(FrozenModel):
    schema_version: Literal["interactive.v1"] = INTERACTIVE_SCHEMA_VERSION
    session_id: Identifier
    sequence: int = Field(ge=0)
    previous_state: SessionState
    state: SessionState
    revision: int = Field(ge=1)
    sim_time_s: float = Field(ge=0.0)
    reason_code: Identifier = "OK"
    wall_time: datetime = Field(default_factory=utc_now)


class Telecommand(FrozenModel):
    schema_version: Literal["interactive.v1"] = INTERACTIVE_SCHEMA_VERSION
    command_id: Identifier
    session_id: Identifier
    session_revision: int = Field(ge=0)
    operation: Identifier
    target: Identifier
    parameters: dict[str, Any] = Field(default_factory=dict)
    actor_id: Identifier
    actor_role: ActorRole
    execute_at_sim_time_s: float = Field(ge=0.0)
    expires_at: datetime | None = None
    received_at: datetime = Field(default_factory=utc_now)


class CommandAck(FrozenModel):
    schema_version: Literal["interactive.v1"] = INTERACTIVE_SCHEMA_VERSION
    command_id: Identifier
    session_id: Identifier
    state: CommandState
    reason_code: Identifier = "OK"
    detail: str = Field(default="", max_length=1024)
    sim_time_s: float | None = Field(default=None, ge=0.0)
    sequence: int = Field(ge=0)
    timestamp: datetime = Field(default_factory=utc_now)


class TelemetryFrame(FrozenModel):
    schema_version: Literal["interactive.v1"] = INTERACTIVE_SCHEMA_VERSION
    session_id: Identifier
    stream: Identifier
    sequence: int = Field(ge=0)
    sim_time_s: float = Field(ge=0.0)
    wall_time: datetime = Field(default_factory=utc_now)
    quality: Literal["GOOD", "SUSPECT", "INVALID", "GAP"] = "GOOD"
    values: dict[str, Any]


class StreamGap(FrozenModel):
    schema_version: Literal["interactive.v1"] = INTERACTIVE_SCHEMA_VERSION
    session_id: Identifier
    stream: Identifier
    after_sequence: int = Field(ge=-1)
    next_sequence: int = Field(ge=0)
    dropped_count: int = Field(ge=1)
    reason_code: Literal["BUFFER_EVICTED", "SUBSCRIBER_BACKPRESSURE"]
