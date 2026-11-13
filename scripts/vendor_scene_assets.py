#!/usr/bin/env python3
"""Stage pinned, offline Globe.gl + Three.js assets for the satellite scene.

All runtime assets are local. npm registry integrity metadata verifies exact
package tarballs; no CDN, online imagery, eval(), or WebAssembly is required.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

PACKAGES = {
    "globe.gl": "2.46.2",
    "three-globe": "2.45.6",
    "three": "0.186.1",
}
REQUIRED = {
    "globe.gl": ("package/dist/globe.gl.min.js", "package/LICENSE"),
    "three-globe": (
        "package/example/img/earth-blue-marble.jpg",
        "package/example/img/earth-topology.png",
        "package/LICENSE",
    ),
    "three": ("package/build/three.module.js", "package/build/three.core.js", "package/LICENSE"),
}


def pack(npm: str, name: str, version: str, temp: Path) -> tuple[Path, dict[str, object]]:
    completed = subprocess.run(
        [npm, "pack", f"{name}@{version}", "--json", "--pack-destination", str(temp)],
        check=True, capture_output=True, text=True,
    )
    records = json.loads(completed.stdout)
    if not isinstance(records, list) or len(records) != 1:
        raise RuntimeError(f"npm pack returned an unexpected result for {name}.")
    record = records[0]
    archive = temp / str(record["filename"])
    if not archive.is_file():
        raise RuntimeError(f"npm pack archive missing for {name}.")
    return archive, record


def copy_member(bundle: tarfile.TarFile, member_name: str, destination: Path) -> None:
    try:
        member = bundle.getmember(member_name)
    except KeyError as exc:
        raise RuntimeError(f"Required file missing from npm package: {member_name}") from exc
    if not member.isfile():
        raise RuntimeError(f"Expected regular file in npm package: {member_name}")
    source = bundle.extractfile(member)
    if source is None:
        raise RuntimeError(f"Cannot read npm package entry: {member_name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source, destination.open("wb") as target:
        shutil.copyfileobj(source, target)


def vendor(root: Path) -> dict[str, object]:
    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("Node.js 22/npm is required to stage the pinned globe runtime.")
    output = root / "src/sat_sim/web/vendor/globe"
    with tempfile.TemporaryDirectory(prefix="sat-sim-globe-") as temp_name:
        temp = Path(temp_name)
        archives = {
            name: pack(npm, name, version, temp)
            for name, version in PACKAGES.items()
        }
        staged = temp / "staged"
        staged.mkdir()
        for package, members in REQUIRED.items():
            archive, _ = archives[package]
            with tarfile.open(archive, "r:gz") as bundle:
                for member in members:
                    if package == "globe.gl":
                        relative = Path("globe.gl.min.js") if member.endswith(".js") else Path("licenses/globe.gl-LICENSE")
                    elif package == "three":
                        relative = Path("three.module.js") if member.endswith("three.module.js") else Path("three.core.js") if member.endswith("three.core.js") else Path("licenses/three-LICENSE")
                    else:
                        if "earth-blue-marble" in member:
                            relative = Path("textures/earth-blue-marble.jpg")
                        elif "earth-topology" in member:
                            relative = Path("textures/earth-topology.png")
                        else:
                            relative = Path("licenses/three-globe-LICENSE")
                    copy_member(bundle, member, staged / relative)
        if output.exists():
            shutil.rmtree(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(staged, output)
        manifest = {
            "packages": {
                name: {
                    "version": PACKAGES[name],
                    "integrity": record.get("integrity"),
                    "license": "MIT",
                }
                for name, (_, record) in archives.items()
            },
            "runtime_files": sum(1 for path in output.rglob("*") if path.is_file()),
            "runtime_bytes": sum(path.stat().st_size for path in output.rglob("*") if path.is_file()),
            "renderer": "globe.gl UMD bundle backed by Three.js",
            "imagery": "Bundled earth-blue-marble texture from three-globe example assets",
            "network_policy": "All runtime scripts and imagery served locally; no CDN or external requests.",
            "csp_policy": "No unsafe-eval, worker eval exception, or wasm-unsafe-eval required.",
        }
        (output / "vendor-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return manifest


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        result = vendor(root)
    except (OSError, RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError, tarfile.TarError) as exc:
        print(f"P9-C globe asset staging failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
