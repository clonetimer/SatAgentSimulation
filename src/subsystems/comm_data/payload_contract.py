"""Payload-to-Comm/Data interface contract helpers for component completeness.

These helpers do not perform whole-spacecraft integration.  They define the
data-side contract needed later: a payload subsystem result contributes its
generated_bps into the Comm/Data queue input.
"""
from __future__ import annotations

from .schemas import CommDataStepInput


def comm_step_from_payload_result(
    payload_result,
    *,
    dt_s: float,
    mode: str,
    spacecraft_pos_ecef_m: tuple[float, float, float],
    eps_allows_downlink: bool = True,
    downlink_requested: bool = True,
    access_override: bool | None = None,
    slant_range_m: float | None = None,
) -> CommDataStepInput:
    generated_bps = float(getattr(payload_result, "generated_bps"))
    return CommDataStepInput(
        dt_s=dt_s,
        mode=mode,
        spacecraft_pos_ecef_m=spacecraft_pos_ecef_m,
        generated_bps=max(0.0, generated_bps),
        slant_range_m=slant_range_m,
        access_override=access_override,
        eps_allows_downlink=eps_allows_downlink,
        downlink_requested=downlink_requested,
    )
