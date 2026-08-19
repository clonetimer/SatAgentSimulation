"""Label schema constants for standard capability outputs."""
from __future__ import annotations

FAULT_LABEL_FIELDS: tuple[str, ...] = (
    "event_id",
    "task_id",
    "case_id",
    "target",
    "target_type",
    "event_type",
    "fault_type",
    "degradation_type",
    "onset_time_s",
    "end_time_s",
    "magnitude",
    "label",
)

__all__ = ["FAULT_LABEL_FIELDS"]
