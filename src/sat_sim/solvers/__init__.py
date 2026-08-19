"""HF-2 deterministic fixed-step solver foundation."""

from .core import (
    FixedStepSolverConfig,
    SolverError,
    build_solver_contract,
    euler_step,
    propagate_fixed_step,
    rk4_step,
)

__all__ = [
    "FixedStepSolverConfig",
    "SolverError",
    "build_solver_contract",
    "euler_step",
    "propagate_fixed_step",
    "rk4_step",
]
