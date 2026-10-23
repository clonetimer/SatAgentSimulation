"""R9-J runtime ABI gate for a *rebuilt* Basilisk SimpleBattery native patch.

Official bsk==2.11.0 does not implement these new SWIG functions. A source
patch is NOT proof of runtime readiness and is never used as a fallback
implementation for protected native battery memory.
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any


def inspect_battery_native_checkpoint_abi() -> dict[str, Any]:
    try:
        from Basilisk.simulation import simpleBattery
    except ImportError:
        return {
            "basilisk_available": False,
            "bsk_distribution_version": None,
            "native_cpp_patch_abi_available": False,
            "evidence_level": "SOURCE_ONLY_NOT_COMPILED",
            "reason": "R9J_BASILISK_NOT_INSTALLED",
            "whole_spacecraft_resume_supported": False,
        }
    try:
        installed = version("bsk")
    except PackageNotFoundError:
        installed = None
    required = (
        hasattr(simpleBattery, "SimpleBatteryCheckpointState")
        and callable(getattr(simpleBattery.SimpleBattery, "exportCheckpointState", None))
        and callable(getattr(simpleBattery.SimpleBattery, "importCheckpointState", None))
    )
    return {
        "basilisk_available": True,
        "bsk_distribution_version": installed,
        "native_cpp_patch_abi_available": bool(required),
        "evidence_level": (
            "ABI_DETECTED_NOT_PARITY_PROVEN"
            if required else "SOURCE_ONLY_UNPATCHED_WHEEL"
        ),
        "reason": (
            "R9J_NATIVE_ABI_DETECTED_UNVALIDATED"
            if required else "R9J_NATIVE_CPP_REBUILD_REQUIRED"
        ),
        "whole_spacecraft_resume_supported": False,
    }


def require_battery_native_checkpoint_abi() -> None:
    result = inspect_battery_native_checkpoint_abi()
    if not result["native_cpp_patch_abi_available"]:
        raise RuntimeError(result["reason"])
