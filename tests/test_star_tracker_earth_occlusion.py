"""A full Earth disk masks the engineering star measurement until it clears."""
import math

import pytest


def test_earth_limb_entry_partial_overlap_and_exit():
    messaging = pytest.importorskip("Basilisk.architecture.messaging")
    from sat_sim.bsk_engine.project_native_modules import StarTrackerFaultInjector

    gate = StarTrackerFaultInjector([])
    gate.earth_occultation_half_angle_rad = math.radians(10)
    raw = messaging.STSensorMsgPayload()
    raw.qInrtl2Case = [1, 0, 0, 0]
    raw_msg = messaging.STSensorMsg().write(raw)
    state_msg = messaging.SCStatesMsg()
    gate.starInMsg.subscribeTo(raw_msg)
    gate.spacecraftStateInMsg.subscribeTo(state_msg)
    # At this radius Earth has apparent half-angle 60 degrees.
    radius = 6371000 / math.sin(math.radians(60))
    for tick, (deg, valid) in enumerate([(85, 1), (55, 1), (45, 0), (55, 1), (85, 1)]):
        angle = math.radians(deg)
        state = messaging.SCStatesMsgPayload()
        state.r_BN_N = [-radius * math.sin(angle), 0, -radius * math.cos(angle)]
        state_msg.write(state)
        gate.UpdateState(tick)
        assert gate.validOutMsg.read().deviceStatus == valid
        assert list(gate.starOutMsg.read().qInrtl2Case) == [valid, 0, 0, 0]
        assert list(raw_msg.read().qInrtl2Case) == [1, 0, 0, 0]
