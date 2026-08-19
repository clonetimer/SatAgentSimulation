from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from sat_sim.interactive import FEATURE_FLAG_ENV, interactive_enabled
from sat_sim.interactive.models import CommandAck, CommandState, InteractiveSessionSpec, SessionEvent, SessionState, Telecommand, TelemetryFrame

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "src" / "sat_sim" / "schemas"


def _validate(schema_name: str, payload: dict) -> None:
    schema = json.loads((SCHEMAS / schema_name).read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(payload)


def test_interactive_feature_flag_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(FEATURE_FLAG_ENV, raising=False)
    assert interactive_enabled() is False
    monkeypatch.setenv(FEATURE_FLAG_ENV, "true")
    assert interactive_enabled() is True


def test_models_validate_against_public_schemas() -> None:
    session = InteractiveSessionSpec(session_id="session-1", capability_id="whole_spacecraft.bsksim_foundation.v1", task_spec={})
    command = Telecommand(
        command_id="command-1",
        session_id=session.session_id,
        session_revision=0,
        operation="session.mode.set",
        target="whole_spacecraft",
        actor_id="operator-1",
        actor_role="operator",
        execute_at_sim_time_s=1.0,
    )
    ack = CommandAck(command_id=command.command_id, session_id=session.session_id, state=CommandState.RECEIVED, sequence=0)
    frame = TelemetryFrame(session_id=session.session_id, stream="foundation.trace", sequence=0, sim_time_s=0.0, values={"orbit.radius_m": 7e6})
    event = SessionEvent(
        session_id=session.session_id, sequence=0, previous_state=SessionState.CREATED,
        state=SessionState.PREPARING, revision=1, sim_time_s=0.0,
    )
    _validate("interactive_session.schema.json", session.model_dump(mode="json"))
    _validate("telecommand.schema.json", command.model_dump(mode="json"))
    _validate("command_ack.schema.json", ack.model_dump(mode="json"))
    _validate("telemetry_frame.schema.json", frame.model_dump(mode="json"))
    _validate("session_event.schema.json", event.model_dump(mode="json"))


def test_contracts_reject_unknown_fields_invalid_rate_and_unsafe_identifier() -> None:
    with pytest.raises(ValidationError):
        InteractiveSessionSpec(session_id="session-1", capability_id="capability", task_spec={}, rate=3.0)
    with pytest.raises(ValidationError):
        InteractiveSessionSpec(session_id="../session", capability_id="capability", task_spec={})
    with pytest.raises(ValidationError):
        InteractiveSessionSpec(session_id="session-1", capability_id="capability", task_spec={}, unknown=True)


def test_scope_matrix_covers_the_frozen_31_object_inventory() -> None:
    matrix = json.loads((ROOT / "configs" / "interactive" / "realtime_capability_matrix.json").read_text(encoding="utf-8"))
    scope = json.loads((ROOT / "configs" / "acceptance" / "object_scope.json").read_text(encoding="utf-8"))
    expected = {item["object_id"] for item in scope["objects"]}
    actual = {item["object_id"] for item in matrix["objects"]}
    assert actual == expected
    assert len(matrix["initial_release_capabilities"]) == 4
    assert all(item["status"] == "controllable" for item in matrix["initial_release_capabilities"])
    for capability in matrix["initial_release_capabilities"]:
        for command in capability["commands"]:
            assert {"operation", "required_role", "parameters", "units", "effect_fields"} <= command.keys()
            assert command["effect_fields"]
