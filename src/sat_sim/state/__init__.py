"""HF-2 spacecraft state-vector foundation."""

from .core import (
    AttitudeState,
    CartesianState,
    SpacecraftState,
    StateError,
    build_state_contract,
    normalize_quaternion,
    quaternion_norm,
)

__all__ = [
    "AttitudeState",
    "CartesianState",
    "SpacecraftState",
    "StateError",
    "build_state_contract",
    "normalize_quaternion",
    "quaternion_norm",
]
