"""Per-stream sequence allocation and bounded telemetry replay buffers."""
from __future__ import annotations

import threading
import queue
from uuid import uuid4
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from .models import StreamGap, TelemetryFrame


@dataclass(frozen=True)
class TelemetryRead:
    frames: tuple[TelemetryFrame, ...]
    gap: bool
    oldest_sequence: int | None
    latest_sequence: int | None


class TelemetrySubscription:
    def __init__(
        self,
        session_id: str,
        streams: frozenset[str],
        *,
        capacity: int,
        overflow_policy: str,
        after_sequences: dict[str, int],
        initial_gap_reasons: dict[str, str] | None = None,
    ) -> None:
        if capacity <= 0:
            raise ValueError("subscriber capacity must be positive")
        if overflow_policy not in {"drop_oldest", "disconnect"}:
            raise ValueError("unsupported overflow policy")
        self.subscription_id = uuid4().hex
        self.session_id = session_id
        self.streams = streams
        self.capacity = capacity
        self.overflow_policy = overflow_policy
        self._queue: queue.Queue[TelemetryFrame] = queue.Queue(maxsize=capacity)
        self._last_sequences = dict(after_sequences)
        self._gap_reasons = dict(initial_gap_reasons or {})
        self._closed = False
        self._close_reason: str | None = None
        self._lock = threading.Lock()

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def close_reason(self) -> str | None:
        with self._lock:
            return self._close_reason

    def accepts(self, stream: str) -> bool:
        return stream in self.streams

    def offer(self, frame: TelemetryFrame) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                self._queue.put_nowait(frame)
                return
            except queue.Full:
                if self.overflow_policy == "disconnect":
                    self._closed = True
                    self._close_reason = "SUBSCRIBER_BACKPRESSURE"
                    return
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass
                self._queue.put_nowait(frame)

    def poll(self, timeout_s: float = 0.0, max_items: int = 100) -> tuple[TelemetryFrame | StreamGap, ...]:
        if max_items <= 0:
            raise ValueError("max_items must be positive")
        frames: list[TelemetryFrame] = []
        try:
            frames.append(self._queue.get(timeout=timeout_s))
        except queue.Empty:
            return ()
        while len(frames) < max_items:
            try:
                frames.append(self._queue.get_nowait())
            except queue.Empty:
                break
        output: list[TelemetryFrame | StreamGap] = []
        for frame in frames:
            previous = self._last_sequences.get(frame.stream, -1)
            expected = previous + 1
            if frame.sequence > expected:
                output.append(StreamGap(
                    session_id=self.session_id,
                    stream=frame.stream,
                    after_sequence=previous,
                    next_sequence=frame.sequence,
                    dropped_count=frame.sequence - expected,
                    reason_code=self._gap_reasons.pop(frame.stream, "SUBSCRIBER_BACKPRESSURE"),
                ))
            if frame.sequence > previous:
                output.append(frame)
                self._last_sequences[frame.stream] = frame.sequence
        return tuple(output)


class TelemetryBus:
    def __init__(self, session_id: str, capacity_per_stream: int = 10000) -> None:
        if capacity_per_stream <= 0:
            raise ValueError("capacity_per_stream must be positive")
        self._session_id = session_id
        self._capacity = capacity_per_stream
        self._buffers: dict[str, deque[TelemetryFrame]] = defaultdict(lambda: deque(maxlen=self._capacity))
        self._next_sequence: dict[str, int] = defaultdict(int)
        self._subscribers: dict[str, TelemetrySubscription] = {}
        self._lock = threading.Lock()

    def publish(self, stream: str, sim_time_s: float, values: dict[str, Any], quality: str = "GOOD") -> TelemetryFrame:
        with self._lock:
            sequence = self._next_sequence[stream]
            frame = TelemetryFrame(session_id=self._session_id, stream=stream, sequence=sequence, sim_time_s=sim_time_s, values=values, quality=quality)
            buffer = self._buffers[stream]
            if buffer and sim_time_s < buffer[-1].sim_time_s:
                raise ValueError("telemetry sim_time_s must be monotonic per stream")
            buffer.append(frame)
            self._next_sequence[stream] = sequence + 1
            subscribers = tuple(self._subscribers.values())
        for subscriber in subscribers:
            if subscriber.accepts(stream):
                subscriber.offer(frame)
        return frame

    def read_after(self, stream: str, after_sequence: int = -1) -> TelemetryRead:
        with self._lock:
            frames = tuple(self._buffers.get(stream, ()))
        if not frames:
            return TelemetryRead((), False, None, None)
        oldest, latest = frames[0].sequence, frames[-1].sequence
        gap = after_sequence < oldest - 1
        return TelemetryRead(tuple(frame for frame in frames if frame.sequence > after_sequence), gap, oldest, latest)

    def streams(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._buffers))

    def subscribe(
        self,
        streams: tuple[str, ...],
        *,
        capacity: int = 256,
        overflow_policy: str = "drop_oldest",
        after_sequences: dict[str, int] | None = None,
    ) -> TelemetrySubscription:
        normalized = frozenset(str(stream) for stream in streams if str(stream))
        if not normalized:
            raise ValueError("at least one stream is required")
        after = {str(key): int(value) for key, value in (after_sequences or {}).items()}
        with self._lock:
            initial_gap_reasons = {
                stream: "BUFFER_EVICTED"
                for stream in normalized
                if self._buffers.get(stream)
                and after.get(stream, -1) < self._buffers[stream][0].sequence - 1
            }
            subscriber = TelemetrySubscription(
                self._session_id, normalized, capacity=capacity,
                overflow_policy=overflow_policy, after_sequences=after,
                initial_gap_reasons=initial_gap_reasons,
            )
            self._subscribers[subscriber.subscription_id] = subscriber
            replay = [
                frame
                for stream in normalized
                for frame in self._buffers.get(stream, ())
                if frame.sequence > after.get(stream, -1)
            ]
        for frame in sorted(replay, key=lambda item: (item.wall_time, item.stream, item.sequence)):
            subscriber.offer(frame)
        return subscriber

    def unsubscribe(self, subscription_id: str) -> bool:
        with self._lock:
            return self._subscribers.pop(subscription_id, None) is not None
