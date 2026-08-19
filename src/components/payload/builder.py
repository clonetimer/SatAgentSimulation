"""payload component builder module.

Provides both Python model config builders and Basilisk-native component factories.
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic
from .schemas import PayloadInstrumentConfig
from .faults import apply_payload_faults
from .faults import FaultSpec

from dataclasses import dataclass


_messaging = None
_sysModel = None

try:
    from Basilisk.architecture import messaging, sysModel
    _messaging = messaging
    _sysModel = sysModel
except ImportError as exc:
    record_runtime_diagnostic(
        code='OPTIONAL_DEPENDENCY_IMPORT_UNAVAILABLE',
        category=DiagnosticCategory.OPTIONAL_DEPENDENCY_PROBE,
        location='src/components/payload/builder.py:<module>:01',
        exception=exc,
        strict=False,
    )




@dataclass(frozen=True)
class PayloadInstrumentState:
    cumulative_observation_s: float = 0.0
    cumulative_data_bits: float = 0.0
    cumulative_energy_wh: float = 0.0
    cumulative_heat_j: float = 0.0


@dataclass(frozen=True)
class PayloadInstrumentStepInput:
    dt_s: float
    mode: str = "observation"
    requested: bool = True
    eps_allows: bool = True
    thermal_allows: bool = True
    adcs_pointing_ready: bool = True
    pointing_error_deg: float = 0.0
    data_rate_bps_override: float | None = None


@dataclass(frozen=True)
class PayloadInstrumentStepResult:
    enabled: bool
    power_w: float
    heat_w: float
    generated_bps: float
    generated_bits: float
    block_reasons: tuple[str, ...]
    cumulative_observation_s: float
    cumulative_data_bits: float
    cumulative_energy_wh: float
    cumulative_heat_j: float


@dataclass(frozen=True)
class PayloadInstrumentProfileResult:
    enabled: tuple[bool, ...]
    power_w: tuple[float, ...]
    heat_w: tuple[float, ...]
    generated_bps: tuple[float, ...]
    generated_bits: tuple[float, ...]
    cumulative_data_bits: tuple[float, ...]
    block_reasons: tuple[tuple[str, ...], ...]


def basilisk_available() -> bool:
    """Return True when Basilisk messaging/sysModel modules are available."""
    return _messaging is not None and _sysModel is not None


def _block_reasons(cfg: PayloadInstrumentConfig, step: PayloadInstrumentStepInput) -> tuple[str, ...]:
    reasons: list[str] = []
    if not step.requested:
        reasons.append("not_requested")
    if step.mode not in cfg.allowed_modes:
        reasons.append("mode_not_observation")
    if not step.eps_allows:
        reasons.append("eps_block")
    if not step.thermal_allows:
        reasons.append("thermal_block")
    if not step.adcs_pointing_ready:
        reasons.append("adcs_not_ready")
    if step.pointing_error_deg > cfg.max_pointing_error_deg:
        reasons.append("pointing_error_exceeds_limit")
    return tuple(reasons)


def step_payload_instrument(
    state: PayloadInstrumentState,
    cfg: PayloadInstrumentConfig,
    step: PayloadInstrumentStepInput,
) -> tuple[PayloadInstrumentState, PayloadInstrumentStepResult]:
    """Advance the reduced-order payload instrument by one step."""
    if step.dt_s <= 0.0:
        raise ValueError("PayloadInstrumentStepInput.dt_s must be positive")
    reasons = _block_reasons(cfg, step)
    enabled = len(reasons) == 0
    power_w = float(cfg.observation_power_w if enabled else cfg.standby_power_w)
    generated_bps = 0.0
    if enabled:
        generated_bps = float(cfg.data_rate_bps if step.data_rate_bps_override is None else max(0.0, step.data_rate_bps_override))
    generated_bits = generated_bps * step.dt_s
    heat_w = power_w * max(0.0, min(1.0, cfg.heat_fraction))
    obs_s = state.cumulative_observation_s + (step.dt_s if enabled else 0.0)
    data_bits = state.cumulative_data_bits + generated_bits
    energy_wh = state.cumulative_energy_wh + power_w * step.dt_s / 3600.0
    heat_j = state.cumulative_heat_j + heat_w * step.dt_s
    next_state = PayloadInstrumentState(obs_s, data_bits, energy_wh, heat_j)
    result = PayloadInstrumentStepResult(
        enabled=enabled,
        power_w=power_w,
        heat_w=heat_w,
        generated_bps=generated_bps,
        generated_bits=generated_bits,
        block_reasons=reasons,
        cumulative_observation_s=obs_s,
        cumulative_data_bits=data_bits,
        cumulative_energy_wh=energy_wh,
        cumulative_heat_j=heat_j,
    )
    return next_state, result


def simulate_payload_profile(
    initial_state: PayloadInstrumentState,
    cfg: PayloadInstrumentConfig,
    steps,
) -> tuple[PayloadInstrumentState, PayloadInstrumentProfileResult, tuple[PayloadInstrumentStepResult, ...]]:
    state = initial_state
    rows: list[PayloadInstrumentStepResult] = []
    for step in steps:
        state, result = step_payload_instrument(state, cfg, step)
        rows.append(result)
    profile = PayloadInstrumentProfileResult(
        enabled=tuple(r.enabled for r in rows),
        power_w=tuple(r.power_w for r in rows),
        heat_w=tuple(r.heat_w for r in rows),
        generated_bps=tuple(r.generated_bps for r in rows),
        generated_bits=tuple(r.generated_bits for r in rows),
        cumulative_data_bits=tuple(r.cumulative_data_bits for r in rows),
        block_reasons=tuple(r.block_reasons for r in rows),
    )
    return state, profile, tuple(rows)


def _build_nominal_payload_config_base_impl(
    name: str = "nadir_imager",
    observation_power_w: float = 18.0,
    standby_power_w: float = 3.0,
    data_rate_bps: float = 250_000.0,
    heat_fraction: float = 0.85,
    max_pointing_error_deg: float = 0.25,
    allowed_modes: tuple[str, ...] = ("observation", "payload", "NOMINAL_OBSERVATION"),
    fault_specs: list[FaultSpec] | None = None,
) -> PayloadInstrumentConfig:
    """Build a nominal PayloadInstrumentConfig with default values."""
    return PayloadInstrumentConfig(
        name=name,
        observation_power_w=observation_power_w,
        standby_power_w=standby_power_w,
        data_rate_bps=data_rate_bps,
        heat_fraction=heat_fraction,
        max_pointing_error_deg=max_pointing_error_deg,
        allowed_modes=allowed_modes,
    )


def build_nominal_payload_profile(
    dt_s: float = 1.0,
) -> tuple[PayloadInstrumentStepInput, ...]:
    """Build a nominal payload step profile exercising various block conditions."""
    return (
        PayloadInstrumentStepInput(dt_s=dt_s, mode="observation", pointing_error_deg=0.05),
        PayloadInstrumentStepInput(dt_s=dt_s, mode="observation", pointing_error_deg=0.10),
        PayloadInstrumentStepInput(dt_s=dt_s, mode="observation", pointing_error_deg=0.40),
        PayloadInstrumentStepInput(dt_s=dt_s, mode="downlink", pointing_error_deg=0.05),
        PayloadInstrumentStepInput(dt_s=dt_s, mode="observation", eps_allows=False),
        PayloadInstrumentStepInput(dt_s=dt_s, mode="observation", thermal_allows=False),
        PayloadInstrumentStepInput(dt_s=dt_s, mode="observation", adcs_pointing_ready=False),
        PayloadInstrumentStepInput(dt_s=dt_s, mode="observation", pointing_error_deg=0.05),
    )


def require_basilisk_payload() -> None:
    try:
        from Basilisk.simulation import simpleInstrument  # noqa: F401
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Basilisk simpleInstrument is unavailable: {exc}") from exc


def build_simple_instrument(
    model_tag: str,
    baud_bps: float,
    data_name: str = "payload_science",
    data_rate_bps: float | None = None,
):
    """Create a Basilisk ``SimpleInstrument`` payload component."""
    require_basilisk_payload()
    from Basilisk.simulation import simpleInstrument
    instrument = simpleInstrument.SimpleInstrument()
    instrument.ModelTag = model_tag
    instrument.nodeBaudRate = max(0.0, float(baud_bps))
    instrument.nodeDataName = str(data_name)
    if data_rate_bps is not None:
        instrument.nodeBaudRate = max(0.0, float(data_rate_bps))
    return instrument

# Component fault/degradation compatibility wrappers
from .degradation import PayloadDegradation, PayloadDegradationRate
from .degradation import apply_payload_degradation, compute_degradation_state
from .faults import FaultSpec as _ComponentFaultSpec

_build_nominal_payload_config_base = _build_nominal_payload_config_base_impl

def build_nominal_payload_config(
    *args,
    degradation: PayloadDegradation | None = None,
    degradation_rate: PayloadDegradationRate | None = None,
    years_elapsed: float = 0.0,
    fault_specs: list[_ComponentFaultSpec] | None = None,
    **kwargs,
):
    """Build config with canonical degradation-rate, degradation-state and fault support."""
    if "degradation" in kwargs:
        degradation = kwargs.pop("degradation")
    if "degradation_rate" in kwargs:
        degradation_rate = kwargs.pop("degradation_rate")
    if "years_elapsed" in kwargs:
        years_elapsed = kwargs.pop("years_elapsed")
    if "fault_specs" in kwargs:
        fault_specs = kwargs.pop("fault_specs")
    config = _build_nominal_payload_config_base(*args, **kwargs)
    if degradation is None and degradation_rate is not None and float(years_elapsed) > 0.0:
        degradation = compute_degradation_state(degradation_rate, years_elapsed)
    if degradation is not None:
        config = apply_payload_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_payload_faults(config, fault_specs)
    return config

