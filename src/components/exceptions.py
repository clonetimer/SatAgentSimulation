"""Satellite simulation library exception types.

This module defines unified exception types for the sat_sim library.
All exceptions inherit from SatSimBaseException for easy catching.

Error codes:
    - SS001-SS099: Configuration errors
    - SS100-SS199: Simulation errors
    - SS200-SS299: Component errors
    - SS300-SS399: Subsystem errors
    - SS400-SS499: Integration errors
    - SS500-SS599: Validation errors
"""
from __future__ import annotations

from typing import Any, Optional


class SatSimBaseException(Exception):
    """Base exception for all sat_sim errors."""
    
    error_code: str
    message: str
    
    def __init__(self, message: str, error_code: str = "SS000"):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
    
    def __str__(self) -> str:
        return f"[{self.error_code}] {self.message}"


class ConfigError(SatSimBaseException):
    """Configuration error - invalid or missing configuration."""
    
    def __init__(self, message: str, error_code: str = "SS001"):
        super().__init__(message, error_code)


class ConfigValueError(ConfigError):
    """Invalid configuration value."""
    
    def __init__(self, param_name: str, value: Any, expected: str):
        message = f"Invalid value for '{param_name}': {value!r}. Expected: {expected}"
        super().__init__(message, "SS002")


class ConfigMissingError(ConfigError):
    """Missing required configuration parameter."""
    
    def __init__(self, param_name: str, context: str = ""):
        if context:
            message = f"Missing required parameter '{param_name}' in {context}"
        else:
            message = f"Missing required parameter '{param_name}'"
        super().__init__(message, "SS003")


class ConfigRangeError(ConfigError):
    """Configuration value out of valid range."""
    
    def __init__(self, param_name: str, value: float, min_val: float, max_val: float):
        message = f"Parameter '{param_name}'={value} is out of range [{min_val}, {max_val}]"
        super().__init__(message, "SS004")


class SimulationError(SatSimBaseException):
    """Simulation execution error."""
    
    def __init__(self, message: str, error_code: str = "SS101"):
        super().__init__(message, error_code)


class SimulationInitializationError(SimulationError):
    """Failed to initialize simulation."""
    
    def __init__(self, reason: str):
        message = f"Failed to initialize simulation: {reason}"
        super().__init__(message, "SS102")


class SimulationExecutionError(SimulationError):
    """Failed to execute simulation."""
    
    def __init__(self, reason: str):
        message = f"Simulation execution failed: {reason}"
        super().__init__(message, "SS103")


class ComponentError(SatSimBaseException):
    """Component operation error."""
    
    def __init__(self, component_name: str, message: str):
        full_message = f"Component '{component_name}': {message}"
        super().__init__(full_message, "SS201")


class ComponentStateError(ComponentError):
    """Invalid component state."""
    
    def __init__(self, component_name: str, current_state: str, expected_state: str):
        message = f"Component is in state '{current_state}', expected '{expected_state}'"
        super().__init__(component_name, message)


class ComponentNotFoundError(ComponentError):
    """Component not found in registry."""
    
    def __init__(self, component_name: str, registry: str = ""):
        if registry:
            message = f"Component '{component_name}' not found in {registry}"
        else:
            message = f"Component '{component_name}' not found"
        super().__init__(component_name, message)


class SubsystemError(SatSimBaseException):
    """Subsystem operation error."""
    
    def __init__(self, subsystem_name: str, message: str):
        full_message = f"Subsystem '{subsystem_name}': {message}"
        super().__init__(full_message, "SS301")


class SubsystemNotReadyError(SubsystemError):
    """Subsystem is not ready for operation."""
    
    def __init__(self, subsystem_name: str):
        message = "Subsystem is not ready (not initialized or failed)"
        super().__init__(subsystem_name, message)


class IntegrationError(SatSimBaseException):
    """Inter-subsystem integration error."""
    
    def __init__(self, message: str, error_code: str = "SS401"):
        super().__init__(message, error_code)


class MessageConnectionError(IntegrationError):
    """Failed to connect messages between components."""
    
    def __init__(self, from_component: str, to_component: str, message_type: str):
        message = f"Failed to connect {message_type} from '{from_component}' to '{to_component}'"
        super().__init__(message, "SS402")


class ValidationError(SatSimBaseException):
    """Validation error."""
    
    def __init__(self, message: str, error_code: str = "SS501"):
        super().__init__(message, error_code)


class ThermalValidationError(ValidationError):
    """Thermal validation error."""
    
    def __init__(self, node_name: str, message: str):
        full_message = f"Thermal validation failed for '{node_name}': {message}"
        super().__init__(full_message, "SS502")


class PowerValidationError(ValidationError):
    """Power validation error."""
    
    def __init__(self, component: str, message: str):
        full_message = f"Power validation failed for '{component}': {message}"
        super().__init__(full_message, "SS503")


def validate_non_negative(value: float, name: str) -> None:
    """Validate that a value is non-negative."""
    if value < 0:
        raise ConfigRangeError(name, value, 0.0, float('inf'))


def validate_positive(value: float, name: str) -> None:
    """Validate that a value is positive."""
    if value <= 0:
        raise ConfigRangeError(name, value, 0.0, float('inf'))


def validate_range(value: float, name: str, min_val: float, max_val: float) -> None:
    """Validate that a value is within a range."""
    if not (min_val <= value <= max_val):
        raise ConfigRangeError(name, value, min_val, max_val)


def validate_between_zero_one(value: float, name: str) -> None:
    """Validate that a value is between 0 and 1 (inclusive)."""
    validate_range(value, name, 0.0, 1.0)


def validate_not_none(value: Any, name: str) -> None:
    """Validate that a value is not None."""
    if value is None:
        raise ConfigMissingError(name)


def validate_not_empty(value: Any, name: str) -> None:
    """Validate that a value is not empty (for strings, lists, dicts)."""
    if not value:
        raise ConfigValueError(name, value, "non-empty value")
