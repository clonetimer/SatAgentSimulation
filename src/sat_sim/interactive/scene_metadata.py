from __future__ import annotations

from typing import Any, Mapping

from sat_sim.bsk_engine.orbital_environment import DEFAULT_EPOCH
from .runtime_factory import ADCS_UNIFIED_CAPABILITY_ID, WHOLE_UNIFIED_CAPABILITY_ID


ORBIT_POSITION_FRAME = "J2000_INERTIAL"
ORBIT_POSITION_CAPABILITIES = frozenset({
    WHOLE_UNIFIED_CAPABILITY_ID,
    ADCS_UNIFIED_CAPABILITY_ID,
})


def realtime_scene_metadata(spec: Any) -> dict[str, str | None]:
    """Expose only the runtime epoch/frame required to visualize native orbit telemetry."""
    task_spec = spec.task_spec if isinstance(getattr(spec, "task_spec", None), Mapping) else {}
    simulation = task_spec.get("simulation") if isinstance(task_spec.get("simulation"), Mapping) else {}
    parameters = task_spec.get("parameters") if isinstance(task_spec.get("parameters"), Mapping) else {}
    values = parameters.get("values") if isinstance(parameters.get("values"), Mapping) else parameters
    epoch_utc = values.get("epoch_utc") or DEFAULT_EPOCH
    time_system = str(simulation.get("time_system") or "UTC")
    position_frame = ORBIT_POSITION_FRAME if spec.capability_id in ORBIT_POSITION_CAPABILITIES else None
    return {
        "epoch_utc": str(epoch_utc),
        "time_system": time_system,
        "orbit_position_frame": position_frame,
    }
