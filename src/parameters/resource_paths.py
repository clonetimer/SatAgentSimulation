"""Resolve packaged and project-level parameter registry resources."""
from __future__ import annotations

import os
from pathlib import Path


def parameter_registry_candidates(
    name: str = "demo_parameter_registry_v1.json",
    *,
    root: str | Path | None = None,
) -> tuple[Path, ...]:
    candidates: list[Path] = []

    direct = os.getenv("SAT_SIM_PARAMETER_REGISTRY", "").strip()
    if direct and name == "demo_parameter_registry_v1.json":
        candidates.append(Path(direct).expanduser())

    roots: list[Path] = []
    if root is not None:
        roots.append(Path(root).expanduser())
    project_env = os.getenv("SAT_SIM_PROJECT_ROOT", "").strip()
    if project_env:
        roots.append(Path(project_env).expanduser())
    roots.extend((Path.cwd(), Path(__file__).resolve().parents[2]))

    seen_roots: set[str] = set()
    for item in roots:
        resolved = item.resolve()
        key = str(resolved)
        if key in seen_roots:
            continue
        seen_roots.add(key)
        candidates.append(resolved / "configs" / "parameters" / name)

    # Wheel-safe resource bundled inside the lightweight top-level parameters package.
    candidates.append(Path(__file__).resolve().parent / "data" / name)

    # Optional system installation convention.
    candidates.append(Path(__file__).resolve().parents[2] / "share" / "sat_sim" / "configs" / "parameters" / name)

    deduped: list[Path] = []
    seen: set[str] = set()
    for item in candidates:
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return tuple(deduped)


def resolve_parameter_registry(
    name: str = "demo_parameter_registry_v1.json",
    *,
    root: str | Path | None = None,
    require_exists: bool = False,
) -> Path:
    candidates = parameter_registry_candidates(name, root=root)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    if require_exists:
        searched = "\n".join(f"- {item}" for item in candidates)
        raise FileNotFoundError(f"parameter registry {name!r} was not found; searched:\n{searched}")
    return candidates[0]


__all__ = ["parameter_registry_candidates", "resolve_parameter_registry"]
