"""TaskSpec to BSKSim-style scenario factory."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .event_manager import parse_bsk_events
from .scenario_base import BSKScenarioBase, FoundationOrbitAttitudeScenario
from .types import BSKScenarioConfig

FOUNDATION_CAPABILITY_ID = "whole_spacecraft.bsksim_foundation.v1"
ADCS_BSKSIM_CAPABILITY_ID = "subsystem.adcs_bsksim.v1"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _simulation_value(spec: Mapping[str, Any], key: str, default: float) -> float:
    sim = _mapping(spec.get("simulation"))
    if key == "step_s" and key not in sim:
        solver = _mapping(sim.get("solver"))
        value = solver.get("step_s", default)
    else:
        value = sim.get(key, default)
    try:
        return float(value)
    except Exception:
        return float(default)


def config_from_task_spec(spec: Mapping[str, Any]) -> BSKScenarioConfig:
    model = _mapping(spec.get("model"))
    target = _mapping(model.get("target"))
    params = _mapping(spec.get("parameters"))
    values = _mapping(params.get("values"))
    outputs = _mapping(spec.get("outputs"))
    plots = outputs.get("plots") if isinstance(outputs.get("plots"), Sequence) and not isinstance(outputs.get("plots"), (str, bytes)) else []
    mode = str(target.get("mode") or values.get("target_mode") or "standby")
    task = _mapping(spec.get("task"))
    return BSKScenarioConfig(
        scenario_id=str(task.get("id") or spec.get("task_id") or "bsksim_foundation_task"),
        capability_id=str(model.get("capability_id") or spec.get("capability_id") or FOUNDATION_CAPABILITY_ID),
        duration_s=_simulation_value(spec, "duration_s", 60.0),
        step_s=_simulation_value(spec, "step_s", 1.0),
        sample_s=_simulation_value(spec, "sample_s", 10.0),
        mode_request=mode,
        parameters=values,
        events=parse_bsk_events(spec),
        requested_outputs=tuple(str(x) for x in plots),
    )


def create_scenario(spec: Mapping[str, Any]) -> BSKScenarioBase:
    config = config_from_task_spec(spec)
    if config.capability_id not in {FOUNDATION_CAPABILITY_ID, "whole_spacecraft.basilisk_6dof.v1", "whole_spacecraft.orbit_adcs_fidelity.v1"}:
        # The foundation scenario is also useful for previewing future migration
        # of existing whole-spacecraft capabilities, but callers should not use
        # it to claim full native migration for unrelated adapters.
        pass
    return FoundationOrbitAttitudeScenario(config)
