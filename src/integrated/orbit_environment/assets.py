"""Environment asset resolution for WMM and SPICE-backed Basilisk runs.

The resolver is intentionally deterministic and local-only.  It never downloads
support files at runtime; high-fidelity runs either resolve the requested local
assets or fail explicitly when ``strict_resource_loading`` is enabled.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
from typing import Iterable

PACKAGE_ROOT = Path(__file__).resolve().parents[3]
THIRD_PARTY_DIR = PACKAGE_ROOT / "third_party"
DEFAULT_WMM_DIR = THIRD_PARTY_DIR / "wmm"
DEFAULT_SPICE_DIR = THIRD_PARTY_DIR / "spice"

DEFAULT_SPICE_KERNEL_NAMES: tuple[str, ...] = (
    "naif0012.tls",
    "de430.bsp",
    "pck00010.tpc",
    "gm_de431.tpc",
    "de-403-masses.tpc",
)


@dataclass(frozen=True)
class ResolvedAsset:
    """A local environment support-data file."""

    name: str
    path: str
    sha256: str
    size_bytes: int

    @property
    def directory(self) -> str:
        return str(Path(self.path).parent)


def _sha256(path: Path) -> str:
    h = sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _asset(path: Path) -> ResolvedAsset:
    resolved = path.expanduser().resolve()
    return ResolvedAsset(
        name=resolved.name,
        path=str(resolved),
        sha256=_sha256(resolved),
        size_bytes=resolved.stat().st_size,
    )


def _candidate_dirs(
    explicit_path: str | None,
    default_dir: Path,
    *,
    specific_env: str | None = None,
    asset_subdir: str | None = None,
) -> list[Path]:
    dirs: list[Path] = []
    candidate_paths: list[str] = []
    if explicit_path:
        candidate_paths.append(explicit_path)
    if specific_env and os.getenv(specific_env):
        candidate_paths.append(str(os.environ[specific_env]))
    for value in candidate_paths:
        explicit = Path(value).expanduser()
        if explicit.is_file():
            dirs.append(explicit.parent)
        else:
            dirs.append(explicit)
    asset_root = os.getenv("SAT_SIM_ASSET_ROOT")
    if asset_root:
        root = Path(asset_root).expanduser()
        dirs.append(root / asset_subdir if asset_subdir else root)
    dirs.append(default_dir)
    # During local development and tests the user may provide assets in /mnt/data
    # without copying them into the package yet.  This is intentionally lower
    # priority than explicit paths and packaged third_party assets.
    mnt_data = Path("/mnt/data")
    if mnt_data.exists():
        dirs.append(mnt_data)
    unique: list[Path] = []
    seen: set[str] = set()
    for item in dirs:
        key = str(item.expanduser().resolve()) if item.exists() else str(item.expanduser())
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def resolve_wmm_asset(path: str | None = None, *, strict: bool = False) -> tuple[ResolvedAsset | None, tuple[str, ...]]:
    """Resolve the WMM coefficient file.

    ``path`` may be either a direct ``*.COF`` file or a directory containing one.
    """

    searched: list[str] = []
    if path and Path(path).expanduser().is_file():
        candidate = Path(path).expanduser()
        if candidate.suffix.upper() == ".COF":
            return _asset(candidate), tuple(searched)
        searched.append(str(candidate))
    for directory in _candidate_dirs(path, DEFAULT_WMM_DIR, specific_env="SAT_SIM_WMM_PATH", asset_subdir="wmm"):
        searched.append(str(directory))
        if not directory.exists():
            continue
        files = sorted(directory.glob("*.COF")) + sorted(directory.glob("*.cof"))
        if files:
            return _asset(files[0]), tuple(searched)
    if strict:
        raise FileNotFoundError(f"WMM coefficient file not found; searched: {searched}")
    return None, tuple(searched)


def resolve_spice_assets(
    path: str | None = None,
    kernel_names: Iterable[str] = DEFAULT_SPICE_KERNEL_NAMES,
    *,
    strict: bool = False,
) -> tuple[tuple[ResolvedAsset, ...], tuple[str, ...], tuple[str, ...]]:
    """Resolve a SPICE kernel bundle by file name.

    ``path`` may be a directory or a single kernel file.  Single-file paths are
    useful for smoke tests but strict SPICE profiles normally require all files
    in ``kernel_names``.
    """

    names = tuple(str(name) for name in kernel_names)
    found: dict[str, Path] = {}
    searched: list[str] = []

    if path and Path(path).expanduser().is_file():
        file_path = Path(path).expanduser()
        searched.append(str(file_path))
        if file_path.name in names:
            found[file_path.name] = file_path

    for directory in _candidate_dirs(path, DEFAULT_SPICE_DIR, specific_env="SAT_SIM_SPICE_PATH", asset_subdir="spice"):
        searched.append(str(directory))
        if not directory.exists():
            continue
        for name in names:
            candidate = directory / name
            if name not in found and candidate.exists():
                found[name] = candidate

    missing = tuple(name for name in names if name not in found)
    if missing and strict:
        raise FileNotFoundError(f"SPICE kernels missing: {missing}; searched: {searched}")
    return tuple(_asset(found[name]) for name in names if name in found), tuple(missing), tuple(searched)


def write_environment_asset_manifest(root: Path, *, wmm_path: str | None = None, spice_path: str | None = None) -> dict:
    """Resolve packaged environment assets and write a JSON-serialisable manifest."""

    wmm, wmm_searched = resolve_wmm_asset(wmm_path, strict=False)
    spice, spice_missing, spice_searched = resolve_spice_assets(spice_path, strict=False)
    return {
        "schema_version": "environment-asset-manifest-v1",
        "wmm": None if wmm is None else wmm.__dict__,
        "wmm_searched": list(wmm_searched),
        "spice": [item.__dict__ for item in spice],
        "spice_missing": list(spice_missing),
        "spice_searched": list(spice_searched),
        "package_root": str(root.resolve()),
    }
