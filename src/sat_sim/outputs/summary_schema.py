"""Summary schema constants for standard capability outputs."""
from __future__ import annotations

STANDARD_SUMMARY_FIELDS: tuple[str, ...] = (
    "task_id",
    "case_id",
    "status",
    "duration_s",
    "sample_s",
    "target_level",
    "target_name",
    "mode",
    "qoi",
    "events",
)

__all__ = ["STANDARD_SUMMARY_FIELDS"]
