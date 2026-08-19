"""Unified exception hierarchy and validation utilities.

This module re-exports exceptions from components.exceptions for backward
compatibility and provides a centralized import point for error handling.

The actual implementation is in components/exceptions.py to avoid circular
imports.
"""
from __future__ import annotations

from components.exceptions import (
    SatSimBaseException,
    ConfigError,
    ConfigRangeError,
    ConfigValueError,
    ConfigMissingError,
    ComponentError,
    SimulationError,
    validate_positive,
    validate_non_negative,
    validate_between_zero_one,
    validate_not_none,
    validate_range,
)

__all__ = [
    "SatSimBaseException",
    "ConfigError",
    "ConfigRangeError",
    "ConfigValueError",
    "ConfigMissingError",
    "ComponentError",
    "SimulationError",
    "validate_positive",
    "validate_non_negative",
    "validate_between_zero_one",
    "validate_not_none",
    "validate_range",
]
