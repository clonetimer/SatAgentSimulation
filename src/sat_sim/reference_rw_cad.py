"""Pinned, display-only BIRDS-4 reaction-wheel CAD asset gate (DT-4.1).

This is not a spacecraft-B installation datum, as-built approval, or physics
parameter source. The only served file is the separately exported RW assembly.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

RW_SOURCE_RELATIVE = Path("build/birds4/output/B4_RW_Assembly_v55.glb")
RW_SOURCE_COMMIT = "3bab47a0f53410ef79005d21321ff659726f0a1c"
RW_ARTIFACT_RUN_ID = 43692477458
RW_EXPECTED_SIZE = 1410016
RW_EXPECTED_SHA256 = "3019d97d4990553bcfab39d9cbe0ea15bad9a294f517481b65df1ec71ce4d795"


def verified_reference_rw_glb() -> Path:
    """Fail closed unless the exact artifact-backed RW GLB is locally staged."""
    configured = os.getenv("SAT_SIM_BIRDS4_RW_GLB")
    candidate = (Path(configured).expanduser() if configured
                 else Path(__file__).resolve().parents[2] / RW_SOURCE_RELATIVE).resolve()
    if not candidate.is_file():
        raise FileNotFoundError("DT41_RW_REFERENCE_ASSET_NOT_STAGED")
    if candidate.stat().st_size != RW_EXPECTED_SIZE:
        raise ValueError("DT41_RW_REFERENCE_SIZE_MISMATCH")
    digest = hashlib.file_digest(candidate.open("rb"), "sha256").hexdigest()
    if digest != RW_EXPECTED_SHA256:
        raise ValueError("DT41_RW_REFERENCE_SHA256_MISMATCH")
    return candidate
