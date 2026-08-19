"""Basilisk-native grouped multi-rate message recorders.

Each telemetry stream owns an independent group of Basilisk message recorder
modules.  Recorder groups are attached to the same task as the message-writing
models and use ``message.recorder(samplingTime)`` for their own cadence.  This
keeps recorded timestamps aligned with current message data and avoids
post-run stride selection as the source of sample times.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class RecorderGroupSpec:
    stream_id: str
    sample_s: float
    format: str
    fields: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RecorderGroupSpec":
        return cls(
            stream_id=str(value.get("stream_id") or "").strip(),
            sample_s=float(value.get("sample_s")),
            format=str(value.get("format") or "csv"),
            fields=tuple(str(item) for item in (value.get("fields") or ())),
        )


@dataclass
class BasiliskRecorderGroup:
    spec: RecorderGroupSpec
    recorders: dict[str, Any]
    task_name: str


def attach_grouped_message_recorders(
    *,
    sim: Any,
    task_name: str,
    message_handles: Mapping[str, Any],
    streams: Sequence[Mapping[str, Any]],
) -> dict[str, BasiliskRecorderGroup]:
    """Create one Basilisk recorder set per configured telemetry stream.

    All recorder modules are attached to ``task_name``.  The per-stream
    ``samplingTime`` controls the native recording cadence inside Basilisk.
    """

    if not streams:
        return {}
    from Basilisk.utilities import macros  # type: ignore

    groups: dict[str, BasiliskRecorderGroup] = {}
    for raw in streams:
        spec = RecorderGroupSpec.from_mapping(raw)
        if not spec.stream_id:
            raise ValueError("telemetry stream_id is required")
        if spec.stream_id in groups:
            raise ValueError(f"duplicate telemetry stream_id: {spec.stream_id}")
        sampling_ns = macros.sec2nano(spec.sample_s)
        recorders: dict[str, Any] = {}
        for source_name, message in message_handles.items():
            recorder = message.recorder(sampling_ns)
            recorder.ModelTag = f"telemetry_{spec.stream_id}_{source_name}"
            sim.AddModelToTask(task_name, recorder)
            recorders[str(source_name)] = recorder
        groups[spec.stream_id] = BasiliskRecorderGroup(spec=spec, recorders=recorders, task_name=task_name)
    return groups


def group_manifest(groups: Mapping[str, BasiliskRecorderGroup]) -> dict[str, Any]:
    return {
        "schema_version": "sat-sim.basilisk-recorder-groups.v1",
        "recorder_mode": "basilisk_message_recorder_groups",
        "same_writer_task": True,
        "post_run_downsampling": False,
        "groups": [
            {
                "stream_id": group.spec.stream_id,
                "sample_s": group.spec.sample_s,
                "format": group.spec.format,
                "fields": list(group.spec.fields),
                "task_name": group.task_name,
                "recorder_sources": sorted(group.recorders),
                "recorder_count": len(group.recorders),
            }
            for group in groups.values()
        ],
    }


__all__ = [
    "RecorderGroupSpec",
    "BasiliskRecorderGroup",
    "attach_grouped_message_recorders",
    "group_manifest",
]
