"""HF-2 deterministic fixed-step solver primitives."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence
import math

from sat_sim.time_systems import build_time_grid

HF_SOLVER_SCHEMA_VERSION = "hf2.solvers.v1"
ScalarOrVector = float | tuple[float, ...]
DerivativeFunction = Callable[[float, ScalarOrVector], ScalarOrVector]


class SolverError(ValueError):
    """Raised when solver configuration or state values are invalid."""


def _positive(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) <= 0:
        raise SolverError(f"{name} must be a positive finite number")
    return float(value)


def _nonnegative(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
        raise SolverError(f"{name} must be a non-negative finite number")
    return float(value)


def _state_tuple(value: ScalarOrVector) -> tuple[float, ...]:
    if isinstance(value, bool):
        raise SolverError("state values must be numeric")
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise SolverError("state value must be finite")
        return (float(value),)
    items = tuple(float(x) for x in value)
    if not items or any(not math.isfinite(x) for x in items):
        raise SolverError("state vector must contain finite numeric values")
    return items


def _restore_shape(values: Sequence[float], reference: ScalarOrVector) -> ScalarOrVector:
    if isinstance(reference, (int, float)) and not isinstance(reference, bool):
        return float(values[0])
    return tuple(float(x) for x in values)


def _add_scaled(state: ScalarOrVector, derivative: ScalarOrVector, scale: float) -> ScalarOrVector:
    x = _state_tuple(state)
    dx = _state_tuple(derivative)
    if len(x) != len(dx):
        raise SolverError("derivative dimension does not match state dimension")
    return _restore_shape(tuple(xi + scale * dxi for xi, dxi in zip(x, dx)), state)


def _combine_rk4(state: ScalarOrVector, k1: ScalarOrVector, k2: ScalarOrVector, k3: ScalarOrVector, k4: ScalarOrVector, step_s: float) -> ScalarOrVector:
    x = _state_tuple(state)
    ks = [_state_tuple(k) for k in (k1, k2, k3, k4)]
    if any(len(k) != len(x) for k in ks):
        raise SolverError("RK4 derivative dimension mismatch")
    out = []
    for idx, xi in enumerate(x):
        out.append(xi + (step_s / 6.0) * (ks[0][idx] + 2.0 * ks[1][idx] + 2.0 * ks[2][idx] + ks[3][idx]))
    return _restore_shape(tuple(out), state)


@dataclass(frozen=True)
class FixedStepSolverConfig:
    """Serializable deterministic fixed-step solver configuration."""

    method: str = "euler"
    step_s: float = 10.0
    duration_s: float = 300.0
    rtol: float = 1e-9
    atol: float = 1e-12
    deterministic_seed: int = 0
    include_endpoint: bool = True
    schema_version: str = HF_SOLVER_SCHEMA_VERSION

    def __post_init__(self) -> None:
        method = str(self.method or "").strip().lower()
        if method not in {"euler", "rk4"}:
            raise SolverError("method must be 'euler' or 'rk4' for HF-2 foundation")
        object.__setattr__(self, "method", method)
        object.__setattr__(self, "step_s", _positive(self.step_s, "step_s"))
        object.__setattr__(self, "duration_s", _positive(self.duration_s, "duration_s"))
        if self.step_s > self.duration_s:
            raise SolverError("step_s must not exceed duration_s")
        object.__setattr__(self, "rtol", _nonnegative(self.rtol, "rtol"))
        object.__setattr__(self, "atol", _nonnegative(self.atol, "atol"))
        if isinstance(self.deterministic_seed, bool) or not isinstance(self.deterministic_seed, int) or self.deterministic_seed < 0:
            raise SolverError("deterministic_seed must be a non-negative integer")

    @classmethod
    def from_simulation(cls, simulation: dict[str, Any] | None) -> "FixedStepSolverConfig":
        sim = simulation if isinstance(simulation, dict) else {}
        solver = sim.get("solver") if isinstance(sim.get("solver"), dict) else {}
        return cls(
            method=str(solver.get("method", "euler")),
            step_s=float(solver.get("step_s", sim.get("sample_s", 10.0))),
            duration_s=float(sim.get("duration_s", 300.0)),
            rtol=float(solver.get("rtol", 1e-9)),
            atol=float(solver.get("atol", 1e-12)),
            deterministic_seed=int(solver.get("deterministic_seed", sim.get("seed", 0) or 0)),
            include_endpoint=bool(solver.get("include_endpoint", True)),
        )

    def time_grid(self) -> tuple[float, ...]:
        return build_time_grid(duration_s=self.duration_s, sample_s=self.step_s, include_endpoint=self.include_endpoint).times_s

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "solver_family": "deterministic_fixed_step",
            "method": self.method,
            "step_s": self.step_s,
            "duration_s": self.duration_s,
            "rtol": self.rtol,
            "atol": self.atol,
            "deterministic_seed": self.deterministic_seed,
            "include_endpoint": self.include_endpoint,
            "adaptive_step_allowed": False,
            "benchmark_status": "pending_HF9_benchmark_scenarios",
        }


def euler_step(fn: DerivativeFunction, t_s: float, state: ScalarOrVector, step_s: float) -> ScalarOrVector:
    step = _positive(step_s, "step_s")
    deriv = fn(float(t_s), state)
    return _add_scaled(state, deriv, step)


def rk4_step(fn: DerivativeFunction, t_s: float, state: ScalarOrVector, step_s: float) -> ScalarOrVector:
    step = _positive(step_s, "step_s")
    k1 = fn(float(t_s), state)
    k2 = fn(float(t_s) + step / 2.0, _add_scaled(state, k1, step / 2.0))
    k3 = fn(float(t_s) + step / 2.0, _add_scaled(state, k2, step / 2.0))
    k4 = fn(float(t_s) + step, _add_scaled(state, k3, step))
    return _combine_rk4(state, k1, k2, k3, k4, step)


def propagate_fixed_step(fn: DerivativeFunction, initial_state: ScalarOrVector, config: FixedStepSolverConfig) -> list[dict[str, Any]]:
    """Propagate scalar/vector state and return a serializable trace."""

    if not isinstance(config, FixedStepSolverConfig):
        raise SolverError("config must be FixedStepSolverConfig")
    times = config.time_grid()
    state: ScalarOrVector = initial_state
    trace = [{"time_s": times[0], "state": list(_state_tuple(state)) if not isinstance(state, (int, float)) else float(state)}]
    for idx in range(len(times) - 1):
        t = times[idx]
        dt = times[idx + 1] - times[idx]
        if config.method == "euler":
            state = euler_step(fn, t, state, dt)
        elif config.method == "rk4":
            state = rk4_step(fn, t, state, dt)
        else:  # pragma: no cover - guarded by config validation
            raise SolverError(f"unsupported method {config.method!r}")
        trace.append({"time_s": times[idx + 1], "state": list(_state_tuple(state)) if not isinstance(state, (int, float)) else float(state)})
    return trace


def build_solver_contract(simulation: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return HF-2 solver contract metadata."""

    cfg = FixedStepSolverConfig.from_simulation(simulation)
    payload = cfg.to_dict()
    payload.update({
        "contract_status": "implemented_hf2_foundation",
        "allowed_methods": ["euler", "rk4"],
        "determinism_policy": "fixed_step_no_randomness_except_recorded_seed",
        "known_limits": [
            "Adaptive solvers and stiffness handling are not enabled until model-specific benchmarks exist.",
            "Solver accuracy is not a high-fidelity claim without HF9 tolerance-envelope benchmarks.",
        ],
    })
    return payload


__all__ = [
    "HF_SOLVER_SCHEMA_VERSION",
    "FixedStepSolverConfig",
    "SolverError",
    "euler_step",
    "rk4_step",
    "propagate_fixed_step",
    "build_solver_contract",
]
