"""Errors for the engine-independent execution boundary."""
from __future__ import annotations


class ExecutionContractError(Exception):
    """Base error for execution-contract failures."""


class ExecutionRequestError(ExecutionContractError, ValueError):
    """Raised when an execution request violates a contract invariant."""


class AdapterRegistrationError(ExecutionContractError, ValueError):
    """Raised when an adapter registration is invalid or duplicated."""


class AdapterNotFoundError(ExecutionContractError, LookupError):
    """Raised when no adapter is registered for a logical adapter key."""


class AdapterResultError(ExecutionContractError, TypeError):
    """Raised when an adapter returns an invalid result."""


__all__ = [
    "ExecutionContractError",
    "ExecutionRequestError",
    "AdapterRegistrationError",
    "AdapterNotFoundError",
    "AdapterResultError",
]
