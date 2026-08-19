"""Builder entrypoints for v4.3 orbit/environment service layer."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .basilisk_impl import (
    OrbEnvBasiliskGraph,
    attach_gravity_factory_to_spacecraft,
    attach_orbital_environment_graph_to_task,
    build_orbital_environment_graph,
)
from .harness import run_backend
from .schemas import BasiliskOrbEnvConfig


def build_or_run_environment(backend: str = "python", save_dir: str | Path | None = None) -> dict:
    return run_backend(backend=backend, save_dir=save_dir)


def build_basilisk_orbital_environment(
    sim: Any,
    sc_state_in_msg: Any,
    cfg: BasiliskOrbEnvConfig | None = None,
) -> OrbEnvBasiliskGraph:
    """Build Basilisk orbit environment modules.

    This is the primary public API for the Basilisk orbit environment builder.

    Parameters
    ----------
    sim : SimulationBaseClass
        Basilisk simulation instance
    sc_state_in_msg : Any
        SCStatesMsg from the spacecraft module
    cfg : BasiliskOrbEnvConfig, optional
        Configuration for the environment modules

    Returns
    -------
    OrbEnvBasiliskGraph
        Container with all created objects and messages
    """
    return build_orbital_environment_graph(sim, sc_state_in_msg, cfg)


def attach_basilisk_orbital_environment_to_task(
    sim: Any,
    task_name: str,
    graph: OrbEnvBasiliskGraph,
) -> None:
    """Attach environment modules to a Basilisk task.

    Parameters
    ----------
    sim : SimulationBaseClass
        Basilisk simulation instance
    task_name : str
        Name of the task to attach to
    graph : OrbEnvBasiliskGraph
        Environment graph created by build_basilisk_orbital_environment
    """
    attach_orbital_environment_graph_to_task(sim, task_name, graph)


__all__ = [
    "build_or_run_environment",
    "build_basilisk_orbital_environment",
    "attach_basilisk_orbital_environment_to_task",
    "attach_gravity_factory_to_spacecraft",
    "OrbEnvBasiliskGraph",
    "BasiliskOrbEnvConfig",
]
