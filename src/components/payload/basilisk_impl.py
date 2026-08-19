"""Basilisk availability smoke for the payload component.

Basilisk does not provide a mission-specific optical payload model in this
recovered baseline. This smoke records the payload as a Basilisk integration
boundary rather than executing a Python runner.
"""
from __future__ import annotations

from dataclasses import dataclass

from .builder import build_nominal_payload_config


@dataclass(frozen=True)
class PayloadBasiliskSmokeResult:
    backend: str
    available: bool
    status: str
    modules: tuple[str, ...]
    summary: dict[str, object]
    note: str


def run_basilisk_payload_smoke(*_, **__) -> PayloadBasiliskSmokeResult:
    cfg = build_nominal_payload_config(
        name="nadir_imager",
        observation_power_w=18.0,
        standby_power_w=3.0,
        data_rate_bps=250_000.0,
        heat_fraction=0.85,
        max_pointing_error_deg=0.25,
        allowed_modes=("observation", "payload", "NOMINAL_OBSERVATION"),
    )
    try:
        import Basilisk  # noqa: F401
        available = True
        status = "bridge_boundary_available"
    except Exception:
        available = False
        status = "basilisk_not_available"
    return PayloadBasiliskSmokeResult(
        backend="mixed",
        available=available,
        status=status,
        modules=("payload_power_msg", "payload_data_msg", "payload_heat_msg", "payload_pointing_gate"),
        summary={
            "instrument_name": cfg.name,
            "observation_power_w": cfg.observation_power_w,
            "standby_power_w": cfg.standby_power_w,
            "data_rate_bps": cfg.data_rate_bps,
            "heat_fraction": cfg.heat_fraction,
            "max_pointing_error_deg": cfg.max_pointing_error_deg,
            "allowed_modes": list(cfg.allowed_modes),
        },
        note="Reduced-order payload model; later whole-spacecraft integration maps outputs into EPS/Thermal/Comm/Data/Gate messages.",
    )
