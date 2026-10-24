"""Optional *official* Basilisk Vizard recorder for native finite spacecraft runs.

This uses Basilisk.utilities.vizSupport + C++ simulation.vizInterface.
No mock protobuf writer, no Python satellite solver and no Unity renderer
inside the web server. Paths must come from a trusted local runner, never from
an untrusted TaskSpec or HTTP query. Streaming is deliberately unsupported.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def validate_local_vizard_destination(path: str | Path) -> Path:
    """Only write an explicit, previously selected local .bin file.

    Caller owns filesystem authorization; this function does NOT certify a
    path from a remote user as safe for server-side writing.
    """
    selected = Path(path).expanduser()
    if selected.suffix.lower() != ".bin":
        raise ValueError("VIZARD_OUTPUT_MUST_BE_BIN")
    if selected.is_symlink():
        raise ValueError("VIZARD_OUTPUT_SYMLINK_FORBIDDEN")
    selected.parent.mkdir(parents=True, exist_ok=True)
    target = selected.resolve()
    if not target.parent.is_dir():
        raise ValueError("VIZARD_OUTPUT_PARENT_MISSING")
    if target.exists():
        raise FileExistsError("VIZARD_OUTPUT_ALREADY_EXISTS")
    return target


def connect_native_vizard_recording(
    sim: Any, task_name: str, spacecraft: Any, *, rw_effector: Any,
    output_file: str | Path,
) -> tuple[Any, Path]:
    """Attach vizInterface to the *same* Basilisk task before InitializeSimulation."""
    from Basilisk.utilities import vizSupport  # type: ignore

    if not getattr(vizSupport, "vizFound", False):
        raise RuntimeError("VIZARD_NATIVE_VIZ_INTERFACE_UNAVAILABLE")
    output = validate_local_vizard_destination(output_file)
    viz = vizSupport.enableUnityVisualization(
        sim, task_name, spacecraft,
        rwEffectorList=rw_effector,
        saveFile=str(output),
    )
    if viz is None:
        raise RuntimeError("VIZARD_NATIVE_RECORDER_FAILED_TO_ATTACH")
    return viz, output


def verify_native_vizard_recording(output_file: str | Path) -> dict[str, Any]:
    path = Path(output_file)
    if not path.is_file():
        raise RuntimeError("VIZARD_NATIVE_RECORDING_MISSING")
    size = path.stat().st_size
    if size < 64:
        raise RuntimeError("VIZARD_NATIVE_RECORDING_EMPTY")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "schema_version": "sat-sim.vizard-native-recording.v1",
        "format": "basilisk_vizInterface_binary",
        "status": "RECORDED",
        "filename": path.name,
        "bytes": size,
        "sha256": digest.hexdigest(),
        "recording_is_from_genuine_basilisk": True,
        "viewer": "external_Vizard_desktop",
        "embedded_browser_vizard_available": False,
        "live_vizard_stream_available": False,
        "cross_process_state_resume_supported": False,
    }
