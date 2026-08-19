"""Soft real-time interactive simulation contracts and runtimes.

This package is opt-in and does not change the existing batch execution path.
All capabilities remain engineering simulation only.
"""
from __future__ import annotations

import os

FEATURE_FLAG_ENV = "SAT_SIM_INTERACTIVE_ENABLED"


def interactive_enabled() -> bool:
    """Return whether the optional interactive surface is enabled."""
    return os.getenv(FEATURE_FLAG_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


__all__ = ["FEATURE_FLAG_ENV", "interactive_enabled"]
