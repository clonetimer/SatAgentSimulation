"""Guard tests for native FSW integral-memory export/import (no Basilisk needed)."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from sat_sim.interactive.native_fsw_memory_pilot import (
    PROFILE, capture_mrp_feedback_memory, restore_mrp_feedback_memory_fresh,
    validate_mrp_feedback_memory,
)


class Box:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def feedback(*, advanced=False):
    return Box(
        priorTime=1_800_000_000 if advanced else 0,
        int_sigma=[0.06, -0.03, 0.02] if advanced else [0.0, 0.0, 0.0],
        z=[0.06, -0.03, 0.02] if advanced else [0.0, 0.0, 0.0],
        ISCPntB_B=[100., 0., 0., 0., 80., 0., 0., 0., 60.],
        knownTorquePntB_B=[0., 0., 0.],
        K=0.15, P=8.0, Ki=0.08, integralLimit=12.0,
        controlLawType=0, rwConfigParams=Box(numRW=0),
    )


def _resign(data):
    without_hash = dict(data)
    without_hash.pop("digest_sha256")
    data["digest_sha256"] = hashlib.sha256(json.dumps(
        without_hash, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def test_fsw_memory_roundtrip_is_strict_and_nonproduction() -> None:
    document = capture_mrp_feedback_memory(feedback(advanced=True), checkpoint_ns=2_000_000_000)
    assert document["profile"] == PROFILE
    assert document["entire_fsw_state_restored"] is False
    assert document["message_history_restored"] is False
    assert document["full_spacecraft_resume_supported"] is False
    validate_mrp_feedback_memory(document)
    fresh = feedback()
    restore_mrp_feedback_memory_fresh(fresh, document)
    assert fresh.priorTime == 1_800_000_000
    assert fresh.int_sigma == [0.06, -0.03, 0.02]
    with pytest.raises(ValueError, match="FSW_CHECKPOINT_FRESH_GRAPH_OR_CONFIG_REQUIRED"):
        restore_mrp_feedback_memory_fresh(fresh, document)


def test_fsw_memory_rejects_forged_claim_or_changed_integral_state() -> None:
    document = capture_mrp_feedback_memory(feedback(advanced=True), checkpoint_ns=2_000_000_000)
    modified = copy.deepcopy(document)
    modified["fields"]["memory"]["int_sigma"][0] = 19.0
    with pytest.raises(ValueError, match="FSW_CHECKPOINT_DIGEST_INVALID"):
        validate_mrp_feedback_memory(modified)
    forged = copy.deepcopy(document)
    forged["full_spacecraft_resume_supported"] = True
    _resign(forged)
    with pytest.raises(ValueError, match="FSW_CHECKPOINT_UNSUPPORTED_RESUME_CLAIM"):
        validate_mrp_feedback_memory(forged)
    wrong_prior = copy.deepcopy(document)
    wrong_prior["fields"]["prior_time_ns"] = 1_200_000_000
    _resign(wrong_prior)
    with pytest.raises(ValueError, match="FSW_CHECKPOINT_PRIOR_TIME_INVALID"):
        validate_mrp_feedback_memory(wrong_prior)


def test_fsw_memory_rejects_incompatible_model_and_time_grid() -> None:
    record = capture_mrp_feedback_memory(feedback(advanced=True), checkpoint_ns=2_000_000_000)
    fresh = feedback()
    fresh.Ki = -1.0
    with pytest.raises(ValueError, match="FSW_CHECKPOINT_FRESH_GRAPH_OR_CONFIG_REQUIRED"):
        restore_mrp_feedback_memory_fresh(fresh, record)
    assert fresh.priorTime == 0
    missing = feedback(advanced=True)
    missing.rwConfigParams.numRW = 3
    with pytest.raises(ValueError, match="FSW_CHECKPOINT_RW_CONFIGURATION_UNSUPPORTED"):
        capture_mrp_feedback_memory(missing, checkpoint_ns=2_000_000_000)
    with pytest.raises(ValueError, match="FSW_CHECKPOINT_TIME_INVALID"):
        capture_mrp_feedback_memory(feedback(advanced=True), checkpoint_ns=2_100_000_000)
