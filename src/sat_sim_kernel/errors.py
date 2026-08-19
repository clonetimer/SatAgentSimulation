"""Exceptions for the engine-independent satellite model kernel."""
from __future__ import annotations


class KernelError(Exception):
    """Base class for model-kernel failures."""


class KernelValidationError(KernelError, ValueError):
    """Raised when a kernel contract violates an invariant."""


class DuplicateIdError(KernelValidationError):
    """Raised when a contract contains duplicate identifiers."""


class ReferenceResolutionError(KernelValidationError):
    """Raised when a referenced model, node, port, effect, or evidence is absent."""


__all__ = [
    "KernelError",
    "KernelValidationError",
    "DuplicateIdError",
    "ReferenceResolutionError",
]
