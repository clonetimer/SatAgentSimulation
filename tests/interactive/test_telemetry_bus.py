from __future__ import annotations

import pytest

from sat_sim.interactive.telemetry_bus import TelemetryBus
from sat_sim.interactive.models import StreamGap, TelemetryFrame


def test_stream_sequences_are_independent_and_replay_is_incremental() -> None:
    bus = TelemetryBus("session-1", capacity_per_stream=3)
    assert bus.publish("fast", 0.0, {"x": 0}).sequence == 0
    assert bus.publish("fast", 1.0, {"x": 1}).sequence == 1
    assert bus.publish("housekeeping", 1.0, {"soc": 0.9}).sequence == 0
    replay = bus.read_after("fast", 0)
    assert [frame.sequence for frame in replay.frames] == [1]
    assert replay.gap is False
    assert bus.streams() == ("fast", "housekeeping")


def test_ring_overflow_reports_gap_and_time_regression_is_rejected() -> None:
    bus = TelemetryBus("session-1", capacity_per_stream=2)
    for sequence in range(4):
        bus.publish("fast", float(sequence), {"x": sequence})
    replay = bus.read_after("fast", 0)
    assert replay.gap is True
    assert replay.oldest_sequence == 2
    assert [frame.sequence for frame in replay.frames] == [2, 3]
    with pytest.raises(ValueError, match="monotonic"):
        bus.publish("fast", 2.5, {"x": -1})


def test_slow_subscriber_gets_explicit_gap_without_blocking_fast_subscriber() -> None:
    bus = TelemetryBus("session-1", capacity_per_stream=20)
    slow = bus.subscribe(("fast",), capacity=2, overflow_policy="drop_oldest")
    fast = bus.subscribe(("fast",), capacity=20, overflow_policy="drop_oldest")
    for sequence in range(5):
        bus.publish("fast", float(sequence), {"x": sequence})
    slow_messages = slow.poll()
    assert isinstance(slow_messages[0], StreamGap)
    assert slow_messages[0].dropped_count == 3
    assert [item.sequence for item in slow_messages if isinstance(item, TelemetryFrame)] == [3, 4]
    fast_messages = fast.poll()
    assert [item.sequence for item in fast_messages if isinstance(item, TelemetryFrame)] == list(range(5))
    assert not any(isinstance(item, StreamGap) for item in fast_messages)


def test_disconnect_overflow_policy_and_ring_replay_after_sequence() -> None:
    bus = TelemetryBus("session-1", capacity_per_stream=3)
    for sequence in range(5):
        bus.publish("fast", float(sequence), {"x": sequence})
    replay = bus.subscribe(("fast",), capacity=3, after_sequences={"fast": 0})
    messages = replay.poll()
    assert isinstance(messages[0], StreamGap)
    assert messages[0].next_sequence == 2
    assert messages[0].reason_code == "BUFFER_EVICTED"
    assert [item.sequence for item in messages if isinstance(item, TelemetryFrame)] == [2, 3, 4]

    disconnected = bus.subscribe(("fast",), capacity=1, overflow_policy="disconnect", after_sequences={"fast": 4})
    bus.publish("fast", 5.0, {"x": 5})
    bus.publish("fast", 6.0, {"x": 6})
    assert disconnected.closed is True
    assert disconnected.close_reason == "SUBSCRIBER_BACKPRESSURE"
