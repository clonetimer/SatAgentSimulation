"""Fail-closed TC idempotency / atomic restart pilot guards."""
import copy

import pytest

from sat_sim.interactive.native_tc_reconciliation import (
    NativeTcPilotJournal, recovery_plan, validate_tc_snapshot, _hash,
)

def _journal(tmp_path):
    journal = NativeTcPilotJournal(tmp_path / "tc-pilot.json")
    journal.create(session_id="native-rw-adcs-test", checkpoint_ns=2_000_000_000)
    return journal

def test_future_tc_once_and_acked_not_replayed(tmp_path):
    j = _journal(tmp_path)
    assert j.enqueue(command_id="tc-1", target_mode="nadir",
                     execute_at_ns=2_200_000_000) == "QUEUED"
    assert j.enqueue(command_id="tc-1", target_mode="nadir",
                     execute_at_ns=2_200_000_000) == "DUPLICATE_NO_REPLAY"
    assert j.plan_recovery()["safe_future"] == ["tc-1"]
    claimed = j.claim_due(command_id="tc-1", now_ns=2_200_000_000)
    assert claimed["state"] == "EXECUTING"
    with pytest.raises(ValueError, match="UNCERTAIN_INFLIGHT"):
        NativeTcPilotJournal(j.path).plan_recovery()
    j.acknowledge_applied(command_id="tc-1", at_ns=2_200_000_000)
    assert j.plan_recovery()["safe_future"] == []
    assert j.plan_recovery()["already_acked"] == ["tc-1"]
    with pytest.raises(ValueError, match="ALREADY_ACKED"):
        j.claim_due(command_id="tc-1", now_ns=2_400_000_000)

def test_ambiguous_inflight_is_never_auto_reissued(tmp_path):
    j = _journal(tmp_path)
    j.enqueue(command_id="tc-2", target_mode="nadir",
              execute_at_ns=2_200_000_000)
    j.claim_due(command_id="tc-2", now_ns=2_200_000_000)
    with pytest.raises(ValueError, match="UNCERTAIN_INFLIGHT"):
        j.plan_recovery()
    with pytest.raises(ValueError, match="UNCERTAIN_INFLIGHT"):
        j.claim_due(command_id="tc-2", now_ns=2_400_000_000)

def test_duplicate_conflict_bad_order_early_claim(tmp_path):
    j = _journal(tmp_path)
    j.enqueue(command_id="tc-3", target_mode="nadir",
              execute_at_ns=2_400_000_000)
    j.enqueue(command_id="tc-4", target_mode="inertial3d",
              execute_at_ns=2_200_000_000)
    with pytest.raises(ValueError, match="DUPLICATE_CONFLICT"):
        j.enqueue(command_id="tc-3", target_mode="inertial3d",
                  execute_at_ns=2_400_000_000)
    with pytest.raises(ValueError, match="COMMAND_NOT_DUE"):
        j.claim_due(command_id="tc-3", now_ns=2_000_000_000)
    with pytest.raises(ValueError, match="DUE_ORDER"):
        j.claim_due(command_id="tc-3", now_ns=2_400_000_000)

def test_integrity_tamper_and_false_resume_even_when_rehashed(tmp_path):
    j = _journal(tmp_path)
    doc = j.read()
    doc["resume_supported"] = True
    doc.pop("digest_sha256")
    doc["digest_sha256"] = _hash(doc)
    with pytest.raises(ValueError, match="FALSE_RESUME"):
        recovery_plan(doc)
    doc = j.read()
    doc["checkpoint_ns"] = 2_200_000_000
    with pytest.raises(ValueError, match="DIGEST"):
        validate_tc_snapshot(doc)

def test_incomplete_atomic_staging_denied(tmp_path):
    j = _journal(tmp_path)
    j.stage.write_text("partial", encoding="utf-8")
    with pytest.raises(ValueError, match="PARTIAL_STAGING"):
        j.read()
    with pytest.raises(ValueError, match="PARTIAL_STAGING"):
        j.enqueue(command_id="tc", target_mode="nadir",
                  execute_at_ns=2_200_000_000)
