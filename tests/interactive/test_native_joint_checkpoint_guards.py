"""Fast cross-owner R5 guards independent of native Basilisk runtime."""
import copy

import pytest

from sat_sim.interactive.native_joint_checkpoint import (
    CP_NS, FAULT_START_NS, FAULT_END_NS, TC_DUE_NS,
    _cross_owner_consistency,
)


def _owners():
    session = "sat-joint"
    physics = {
        "checkpoint_sim_time_ns": CP_NS,
        "native_scheduler": {
            "state": {
                "current_ns": CP_NS,
                "next_ns": TC_DUE_NS,
                "processes": {
                    "dynamics": {"scheduled_ns": TC_DUE_NS},
                    "observer": {"scheduled_ns": 2_400_000_000},
                },
            },
        },
        "fsw_integrator": {"prior_time_ns": FAULT_START_NS},
    }
    mode = {
        "checkpoint_ns": CP_NS, "current_mode": "inertial3d",
        "transitions": [{
            "event_id": "tc-1", "from_mode": "inertial3d",
            "to_mode": "nadir", "status": "pending",
            "requested_ns": FAULT_START_NS, "resolved_ns": None,
        }],
    }
    fault = {
        "checkpoint_ns": CP_NS,
        "phase": "active", "start_ns": FAULT_START_NS,
        "end_ns": FAULT_END_NS, "effect_route": "zero_motor_command",
    }
    tc = {
        "checkpoint_ns": CP_NS, "session_id": session,
        "epoch": 1, "commands": [{
            "command_id": "tc-1", "state": "QUEUED",
            "target_mode": "nadir", "execute_at_ns": TC_DUE_NS,
        }],
    }
    return dict(session_id=session, clock_ns=CP_NS, physics=physics,
                mode=mode, fault=fault, tc=tc)


def test_supported_exact_overlap():
    _cross_owner_consistency(**_owners())


@pytest.mark.parametrize("change,pattern", [
    (("physics", "checkpoint_sim_time_ns", CP_NS + 200_000_000), "OWNER_TIME"),
    (("tc", "checkpoint_ns", CP_NS + 200_000_000), "OWNER_TIME"),
    (("tc", "session_id", "different"), "SESSION_OR_EPOCH"),
    (("tc", "epoch", 2), "SESSION_OR_EPOCH"),
    (("mode", "current_mode", "nadir"), "MODE_NOT_PENDING"),
    (("fault", "phase", "cleared"), "ACTIVE_FAULT"),
    (("fault", "effect_route", "fsw_allocator"), "ACTIVE_FAULT"),
])
def test_owner_mismatch_rejected(change, pattern):
    params = _owners()
    part, field, value = change
    params[part][field] = value
    with pytest.raises(ValueError, match=pattern):
        _cross_owner_consistency(**params)


def test_scheduler_priority_phase_and_due_guard():
    params = _owners()
    params["physics"]["native_scheduler"]["state"]["processes"]["observer"][
        "scheduled_ns"
    ] = TC_DUE_NS
    with pytest.raises(ValueError, match="CLOCK_PHASE"):
        _cross_owner_consistency(**params)


@pytest.mark.parametrize("field,value", [
    ("command_id", "other-command"),
    ("target_mode", "inertial3d"),
])
def test_tc_and_mode_event_identity_must_match(field, value):
    params = _owners()
    params["tc"]["commands"][0][field] = value
    with pytest.raises(ValueError, match="TC_MODE_CAUSALITY"):
        _cross_owner_consistency(**params)


def test_mode_request_cannot_be_from_future():
    params = _owners()
    params["mode"]["transitions"][-1]["requested_ns"] = TC_DUE_NS
    with pytest.raises(ValueError, match="MODE_CAUSALITY"):
        _cross_owner_consistency(**params)
