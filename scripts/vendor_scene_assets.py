#!/usr/bin/env python3
"""Stage pinned Three.js and a local Natural Earth II texture for P9-C.

npm verifies each exact package tarball using registry integrity metadata. Only
the runtime modules, their small GLTF/controls helpers, and one offline earth
texture are copied into the Python package.
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
    "three": "0.187.0",
    "cesium": "1.147.0",
}
THREE_FILES = (
    "package/build/three.module.js",
    "package/examples/jsm/controls/OrbitControls.js",
    "package/examples/jsm/loaders/GLTFLoader.js",
    "package/examples/jsm/utils/BufferGeometryUtils.js",
    "package/examples/jsm/utils/SkeletonUtils.js",
)
EARTH_FILE = "package/Build/Cesium/Assets/Textures/NaturalEarthII/0/0/0.jpg"


def copy_tar_member(bundle: tarfile.TarFile, member_name: str, destination: Path) -> None:
    member = bundle.getmember(member_name)
    if not member.isfile():
        raise RuntimeError(f"Expected regular file in npm package: {member_name}")
    source = bundle.extractfile(member)
    if source is None:
        raise RuntimeError(f"Cannot read npm package entry: {member_name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source, destination.open("wb") as target:
        shutil.copyfileobj(source, target)


def pack_npm(npm: str, package: str, version: str, temp: Path) -> tuple[Path, dict[str, object]]:
    result = subprocess.run(
        [npm, "pack", f"{package}@{version}", "--json", "--pack-destination", str(temp)],
        check=True, capture_output=True, text=True,
    )
    records = json.loads(result.stdout)
    if not isinstance(records, list) or len(records) != 1:
        raise RuntimeError(f"npm pack returned an unexpected result for {package}.")
    record = records[0]
    archive = temp / str(record["filename"])
    if not archive.is_file():
        raise RuntimeError(f"npm pack archive missing for {package}.")
    return archive, record


def vendor(root: Path) -> dict[str, object]:
    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("Node.js 22/npm is required to stage the P9-C scene runtime.")
    three_out = root / "src/sat_sim/web/vendor/three"
    earth_out = root / "src/sat_sim/web/vendor/earth"
    three_out.parent.mkdir(parents=True, exist_ok=True)
    earth_out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="sat-sim-three-") as temp_name:
        temp = Path(temp_name)
        three_archive, three_record = pack_npm(npm, "three", PACKAGES["three"], temp)
        cesium_archive, cesium_record = pack_npm(npm, "cesium", PACKAGES["cesium"], temp)

        staged_three = temp / "three"
        staged_earth = temp / "earth"
        staged_three.mkdir()
        staged_earth.mkdir()
        with tarfile.open(three_archive, "r:gz") as bundle:
            for name in THREE_FILES:
                copy_tar_member(bundle, name, staged_three / name.removeprefix("package/"))
            copy_tar_member(bundle, "package/LICENSE", staged_three / "LICENSE")
        with tarfile.open(cesium_archive, "r:gz") as bundle:
            copy_tar_member(bundle, EARTH_FILE, staged_earth / "naturalearth-ii-level0.jpg")

        for output in (three_out, earth_out):
            if output.exists():
                shutil.rmtree(output)
        shutil.copytree(staged_three, three_out)
        shutil.copytree(staged_earth, earth_out)

        manifest = {
            "three": {
                "version": PACKAGES["three"],
                "integrity": three_record.get("integrity"),
                "license": "MIT",
                "runtime_files": len(THREE_FILES),
            },
            "earth_texture": {
                "package": "cesium",
                "version": PACKAGES["cesium"],
                "integrity": cesium_record.get("integrity"),
                "license": "Apache-2.0 package; Natural Earth II source imagery",
                "path": "vendor/earth/naturalearth-ii-level0.jpg",
            },
            "csp": "No unsafe-eval or wasm-unsafe-eval required by the scene renderer.",
        }
        (root / "src/sat_sim/web/vendor/scene-runtime-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        manifest["runtime_bytes"] = sum(
            path.stat().st_size
            for folder in (three_out, earth_out)
            for path in folder.rglob("*") if path.is_file()
        )
        return manifest


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        result = vendor(root)
    except (OSError, RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError, tarfile.TarError) as exc:
        print(f"P9-C scene asset staging failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
