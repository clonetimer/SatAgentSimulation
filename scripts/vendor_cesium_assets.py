#!/usr/bin/env python3
"""Fetch and stage the pinned CesiumJS prebuilt runtime for the web workbench.

The npm registry verifies the exact package tarball using its published integrity
metadata. Only the prebuilt Build/Cesium runtime and Apache-2.0 license are copied;
no npm dependency tree or build-time toolchain is shipped in the application.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

CESIUM_VERSION = "1.147.0"
PACKAGE_PREFIX = "package/Build/Cesium/"
REQUIRED_FILES = (
    "Cesium.js",
    "Widgets/widgets.css",
    "Assets/Textures/NaturalEarthII",
)


def _safe_relative(name: str) -> PurePosixPath | None:
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        return None
    return path


def vendor(output: Path) -> dict[str, object]:
    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("Node.js/npm is required to stage the pinned CesiumJS runtime.")
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sat-sim-cesium-") as temp_name:
        temp = Path(temp_name)
        completed = subprocess.run(
            [npm, "pack", f"cesium@{CESIUM_VERSION}", "--json", "--pack-destination", str(temp)],
            check=True, capture_output=True, text=True,
        )
        packed = json.loads(completed.stdout)
        if not isinstance(packed, list) or len(packed) != 1:
            raise RuntimeError("npm pack did not return exactly one package record.")
        package_record = packed[0]
        archive = temp / str(package_record["filename"])
        if not archive.is_file():
            raise RuntimeError("npm pack archive was not created.")
        staged = temp / "staged"
        staged.mkdir()
        found: set[str] = set()
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle.getmembers():
                if not member.name.startswith(PACKAGE_PREFIX) or not member.isfile():
                    continue
                relative = _safe_relative(member.name[len(PACKAGE_PREFIX):])
                if relative is None:
                    raise RuntimeError(f"Unsafe path in Cesium package: {member.name}")
                source = bundle.extractfile(member)
                if source is None:
                    raise RuntimeError(f"Cannot read Cesium package entry: {member.name}")
                destination = staged.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target)
                found.add(relative.as_posix())
            license_member = bundle.extractfile("package/LICENSE.md")
            if license_member is None:
                raise RuntimeError("Cesium package is missing LICENSE.md.")
            (staged / "LICENSE.md").write_bytes(license_member.read())
        missing = [name for name in REQUIRED_FILES if not any(item == name or item.startswith(name + "/") for item in found)]
        if missing:
            raise RuntimeError(f"Cesium package is missing required runtime assets: {missing}")
        if output.exists():
            shutil.rmtree(output)
        shutil.copytree(staged, output)
        manifest = {
            "package": "cesium",
            "version": CESIUM_VERSION,
            "integrity": package_record.get("integrity"),
            "license": "Apache-2.0",
            "runtime_files": len(found),
            "runtime_bytes": sum(path.stat().st_size for path in output.rglob("*") if path.is_file()),
            "worker_files": sorted(name for name in found if name.startswith("Workers/") or name.startswith("ThirdParty/Workers/")),
            "offline_imagery": "Assets/Textures/NaturalEarthII",
        }
        (output / "vendor-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "src/sat_sim/web/vendor/cesium",
    )
    args = parser.parse_args()
    try:
        result = vendor(args.output)
    except (OSError, RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError, tarfile.TarError) as exc:
        print(f"Cesium asset staging failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
