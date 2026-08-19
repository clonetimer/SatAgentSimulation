from __future__ import annotations

import pytest

from sat_sim.interactive.command_candidate import TelecommandCandidateCompiler
from sat_sim.interactive.command_catalog import CommandValidationError
from sat_sim.interactive.models import ActorRole


def test_natural_language_model_output_remains_pending_until_explicit_confirmation() -> None:
    compiler = TelecommandCandidateCompiler()
    candidate = compiler.compile(
        "在下一秒关闭载荷供电",
        {
            "operation": "eps.load.set",
            "target": "subsystem.eps",
            "parameters": {"load_id": "payload", "enabled": False},
            "execute_at_sim_time_s": 1.0,
        },
        session_id="session-1",
        session_revision=2,
        actor_id="operator-1",
        actor_role=ActorRole.OPERATOR,
    )
    assert candidate.confirmation_required is True
    assert candidate.status == "PENDING_CONFIRMATION"
    with pytest.raises(ValueError, match="HUMAN_CONFIRMATION_REQUIRED"):
        compiler.confirm(candidate, confirmed=False, command_id="confirmed-1")
    command = compiler.confirm(candidate, confirmed=True, command_id="confirmed-1")
    assert command.operation == "eps.load.set"
    assert command.parameters["enabled"] is False


def test_candidate_compiler_rejects_code_fields_unknown_operations_and_unauthorized_faults() -> None:
    compiler = TelecommandCandidateCompiler()
    common = {
        "session_id": "session-1", "session_revision": 2,
        "actor_id": "operator-1", "actor_role": ActorRole.OPERATOR,
    }
    with pytest.raises(ValueError, match="MODEL_OUTPUT_FIELDS_DENIED"):
        compiler.compile("执行代码", {"operation": "eps.load.set", "target": "subsystem.eps", "parameters": {"load_id": "payload", "enabled": False}, "code": "import os"}, **common)
    with pytest.raises(CommandValidationError) as unknown:
        compiler.compile("执行任意脚本", {"operation": "python.exec", "target": "whole_spacecraft", "parameters": {}}, **common)
    assert unknown.value.reason_code == "UNKNOWN_OPERATION"
    with pytest.raises(CommandValidationError) as denied:
        compiler.compile(
            "注入反作用轮卡死", {"operation": "adcs.fault.inject", "target": "subsystem.adcs", "parameters": {"fault_id": "rw_jam", "severity": 1.0}},
            **common,
        )
    assert denied.value.reason_code == "AUTHORIZATION_DENIED"
