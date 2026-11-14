#!/usr/bin/env python3
"""Reproducibly convert pinned BIRDS-4 STEP references to GLB.

The output is a reference-CAD artifact, not a verified as-built installation
registry. Source Git blob SHA-1 and byte size are checked before CAD import.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import struct
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

MANIFEST = Path("assets/cad-sources/birds4/birds4-cad-source-manifest.json")
SELECTED = (
    "BIRDS4_FM_Final v7.step",
    "B4_RW_Assembly v55.step",
    "B4_OBC v3.step",
    "B4_COM_board v19.step",
    "B4_battery v1.step",
    "B4_FM_+X_panel v1.step",
)


def git_blob_sha1(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def verify_source_bytes(data: bytes, expected_size: int, expected_git_blob_sha1: str) -> dict[str, str | int]:
    if len(data) != expected_size:
        raise ValueError(f"source size mismatch: expected {expected_size}, got {len(data)}")
    actual_git_sha = git_blob_sha1(data)
    if actual_git_sha != expected_git_blob_sha1:
        raise ValueError(f"Git blob SHA-1 mismatch: expected {expected_git_blob_sha1}, got {actual_git_sha}")
    return {
        "size_bytes": len(data),
        "git_blob_sha1": actual_git_sha,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def parse_glb(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if len(data) < 20:
        raise ValueError("GLB is shorter than its 20-byte header and JSON chunk")
    magic, version, declared_length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2 or declared_length != len(data):
        raise ValueError("invalid GLB header, version, or declared length")
    json_length, json_type = struct.unpack_from("<I4s", data, 12)
    if json_type != b"JSON" or 20 + json_length > len(data):
        raise ValueError("invalid GLB JSON chunk")
    document = json.loads(data[20:20 + json_length].decode("utf-8").rstrip(" \0"))
    if document.get("asset", {}).get("version") != "2.0":
        raise ValueError("exported asset is not glTF 2.0")
    nodes = document.get("nodes", [])
    meshes = document.get("meshes", [])
    scenes = document.get("scenes", [])
    if not nodes or not meshes or not scenes:
        raise ValueError("GLB has no scene, node hierarchy, or geometry meshes")
    return {
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "node_count": len(nodes),
        "mesh_count": len(meshes),
        "scene_count": len(scenes),
        "root_node_indices": document.get("scenes", [{}])[0].get("nodes", []),
    }


def _matrix4(location: Any) -> list[list[float]]:
    transform = location.wrapped.Transformation()
    return [[float(transform.Value(row, col)) for col in range(1, 5)] for row in range(1, 5)]


def _multiply4(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def _step_length_unit(path: Path) -> str:
    text = path.read_bytes()[:2_000_000].decode("latin-1").upper()
    units = set(re.findall(r"SI_UNIT\(\s*(\.MILLI\.|\$)\s*,\s*\.METRE\.\s*\)", text))
    if units == {".MILLI."}:
        return "MM"
    if units == {"$"}:
        return "M"
    raise ValueError(f"STEP length unit is missing, mixed, or unsupported: {sorted(units)}")


def _walk_assembly(node: Any, parent_path: str | None, parent_world: list[list[float]] | None, rows: list[dict[str, Any]], scale_to_m: float) -> None:
    local = _matrix4(node.loc)
    world = _multiply4(parent_world, local) if parent_world is not None else local
    path = f"{parent_path}/{node.name}" if parent_path else node.name
    local_m = [row[:] for row in local]
    world_m = [row[:] for row in world]
    for matrix in (local_m, world_m):
        matrix[0][3] *= scale_to_m
        matrix[1][3] *= scale_to_m
        matrix[2][3] *= scale_to_m
    rows.append({
        "source_node_name": str(node.name),
        "source_node_path": path,
        "parent_node_path": parent_path,
        "direct_shape_count": len(node.shapes),
        "child_count": len(node.children),
        "local_transform_source_unit": local,
        "local_transform_m": local_m,
        "world_transform_m": world_m,
    })
    for child in node.children:
        _walk_assembly(child, path, world, rows, scale_to_m)


def _download_pinned(record: dict[str, Any], cache: Path, source_commit: str, cad_directory: str) -> tuple[Path, dict[str, Any]]:
    relative = record["path"]
    url_path = urllib.parse.quote(relative, safe="")
    url = (
        "https://raw.githubusercontent.com/BIRDSOpenSource/BIRDS4-CAD/"
        + source_commit + "/"
        + urllib.parse.quote(cad_directory, safe="") + "/" + url_path
    )
    target = cache / Path(relative).name
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file():
        request = urllib.request.Request(url, headers={"User-Agent": "SatAgentSimulation-CAD-ingestion/1.0"})
        with urllib.request.urlopen(request, timeout=180) as response, target.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
    data = target.read_bytes()
    verified = verify_source_bytes(data, record["size_bytes"], record["git_blob_sha1"])
    return target, {"source_url": url, **verified}


def convert_one(record: dict[str, Any], source_cache: Path, output_root: Path, source_commit: str, cad_directory: str) -> dict[str, Any]:
    import cadquery as cq

    source_path, source_integrity = _download_pinned(record, source_cache, source_commit, cad_directory)
    source_unit = _step_length_unit(source_path)
    assembly = cq.Assembly.load(str(source_path), unit=source_unit)
    scale_to_m = 0.001 if source_unit == "MM" else 1.0
    node_rows: list[dict[str, Any]] = []
    _walk_assembly(assembly, None, None, node_rows, scale_to_m)

    # glTF uses metres. Apply one root scale so the STEP hierarchy and all
    # relative placements remain intact while converting millimetres to metres.
    if scale_to_m != 1.0:
        from OCP.gp import gp_Pnt, gp_Trsf
        scale_transform = gp_Trsf()
        scale_transform.SetScale(gp_Pnt(0.0, 0.0, 0.0), scale_to_m)
        assembly.loc = cq.Location(scale_transform) * assembly.loc

    bounds = assembly.toCompound().BoundingBox()
    envelope = {"x_m": float(bounds.xlen), "y_m": float(bounds.ylen), "z_m": float(bounds.zlen)}
    if not all(math.isfinite(v) and v > 0 for v in envelope.values()):
        raise ValueError(f"invalid model envelope: {envelope}")

    output_root.mkdir(parents=True, exist_ok=True)
    stem = Path(record["path"]).stem.replace(" ", "_").replace("+", "plus").replace("-", "minus")
    glb_path = output_root / f"{stem}.glb"
    assembly.export(str(glb_path), tolerance=0.0005, angularTolerance=0.1)
    glb = parse_glb(glb_path)
    if not node_rows or not any(row["direct_shape_count"] for row in node_rows):
        raise ValueError("STEP import did not produce named shape-bearing assembly nodes")

    report = {
        "source": {
            "repository": "BIRDSOpenSource/BIRDS4-CAD",
            "commit": source_commit,
            "path": record["path"],
            **source_integrity,
        },
        "conversion": {
            "engine": "CadQuery",
            "cadquery_version": cq.__version__,
            "source_step_length_unit": source_unit,
            "source_to_gltf_scale": scale_to_m,
            "output_unit": "metres",
            "format": "glTF 2.0 binary GLB",
            "linear_deflection_m": 0.0005,
            "angular_tolerance_rad": 0.1,
        },
        "geometry": {
            "assembly_node_count": len(node_rows),
            "shape_bearing_node_count": sum(1 for row in node_rows if row["direct_shape_count"]),
            "nominal_envelope_m": envelope,
            **glb,
        },
        "promotion_status": "reference_cad_imported_not_as_built_verified",
        "coordinate_note": "Local and world transforms are exported as imported by CadQuery. They require review against the BIRDS-4 FM assembly procedure and datum drawings before promotion to verified equipment installation transforms.",
    }
    (output_root / f"{stem}.assembly-tree.json").write_text(
        json.dumps({"source_path": record["path"], "nodes": node_rows}, indent=2),
        encoding="utf-8",
    )
    (output_root / f"{stem}.report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--source-cache", type=Path, default=Path("build/birds4/source"))
    parser.add_argument("--output", type=Path, default=Path("build/birds4/output"))
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    records = {item["path"]: item for item in manifest["cad_files"]}
    missing = [name for name in SELECTED if name not in records]
    if missing:
        raise SystemExit(f"manifest missing selected STEP files: {missing}")

    results = []
    for name in SELECTED:
        print(f"[CAD] Importing {name}", flush=True)
        results.append(convert_one(
            records[name], args.source_cache, args.output,
            manifest["source_commit"], manifest["cad_directory"]
        ))
    summary = {
        "schema_version": "birds4-cad-conversion-report.v1",
        "source_commit": manifest["source_commit"],
        "selected_step_count": len(results),
        "converted_count": len(results),
        "results": results,
        "status": "PASS",
        "promotion_status": "reference_only_not_as_built_verified",
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "conversion-summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps({"status": summary["status"], "converted": len(results)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
