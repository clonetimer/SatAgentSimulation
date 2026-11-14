#!/usr/bin/env python3
"""用途：将固定版本的 BIRDS-4 STEP 参考模型转换为米制 GLB。
参数：--manifest、--source-cache、--output；输入源文件必须匹配上游 Git blob SHA-1 与字节数。
输出：GLB、装配树、SHA-256 与转换报告；仅作为参考 CAD，不签发 as-built 安装验证。

The output is a reference-CAD artifact, not a verified as-built installation registry.
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


def apply_glb_root_scale(path: Path, scale: float) -> None:
    """Apply a uniform source-unit to metre scale at glTF scene roots."""
    if scale == 1.0:
        return
    data = path.read_bytes()
    magic, version, declared_length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2 or declared_length != len(data):
        raise ValueError("cannot scale an invalid GLB")
    chunks = []
    offset = 12
    document = None
    while offset < len(data):
        chunk_length, chunk_type = struct.unpack_from("<I4s", data, offset)
        offset += 8
        chunk = data[offset:offset + chunk_length]
        if len(chunk) != chunk_length:
            raise ValueError("truncated GLB chunk")
        offset += chunk_length
        if chunk_type == b"JSON":
            document = json.loads(chunk.decode("utf-8").rstrip(" \0"))
        else:
            chunks.append((chunk_type, chunk))
    if document is None:
        raise ValueError("GLB has no JSON chunk")
    nodes = document.get("nodes", [])
    scenes = document.get("scenes", [])
    if not scenes:
        raise ValueError("GLB has no scenes to scale")
    root_indices = scenes[document.get("scene", 0)].get("nodes", [])
    if not root_indices:
        raise ValueError("GLB default scene has no root nodes")
    for index in root_indices:
        node = nodes[index]
        if "matrix" in node:
            matrix = node["matrix"]
            if len(matrix) != 16:
                raise ValueError("invalid glTF node matrix")
            for component in (0, 1, 2, 4, 5, 6, 8, 9, 10, 12, 13, 14):
                matrix[component] *= scale
        else:
            node["scale"] = [float(value) * scale for value in node.get("scale", [1, 1, 1])]
            node["translation"] = [float(value) * scale for value in node.get("translation", [0, 0, 0])]
    json_chunk = json.dumps(document, separators=(",", ":")).encode("utf-8")
    json_chunk += b" " * ((4 - len(json_chunk) % 4) % 4)
    encoded_chunks = [(b"JSON", json_chunk), *chunks]
    body = b"".join(struct.pack("<I4s", len(chunk), kind) + chunk for kind, chunk in encoded_chunks)
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)


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


def _unique_instance_name(name: str, existing: set[str]) -> tuple[str, int]:
    if name not in existing:
        return name, 1
    occurrence = 2
    while f"{name}__instance_{occurrence:03d}" in existing:
        occurrence += 1
    return f"{name}__instance_{occurrence:03d}", occurrence


def _import_step_assembly(path: Path):
    """Import STEP while preserving repeated CAD component occurrences.

    CadQuery's XCAF importer currently uses definition names as instance names.
    Real assemblies reuse the same part definition multiple times. We suffix
    duplicate occurrence names and retain the original CAD label in metadata.
    """
    import cadquery as cq

    original_add = cq.Assembly.add
    original_getitem = cq.Assembly.__getitem__

    def add_with_unique_occurrence(self, arg, **kwargs):
        requested = kwargs.get("name")
        if requested is None and isinstance(arg, cq.Assembly):
            requested = arg.name
        if isinstance(requested, str) and requested in self.objects:
            unique, occurrence = _unique_instance_name(requested, set(self.objects))
            kwargs["name"] = unique
            metadata = dict(kwargs.get("metadata") or getattr(arg, "metadata", {}) or {})
            metadata["source_component_name"] = requested
            metadata["duplicate_occurrence"] = occurrence
            kwargs["metadata"] = metadata
            latest = dict(self.metadata.get("_cad_last_instance_by_source_name", {}))
            latest[requested] = unique
            self.metadata["_cad_last_instance_by_source_name"] = latest
        return original_add(self, arg, **kwargs)

    def getitem_by_source_name(self, name):
        latest = self.metadata.get("_cad_last_instance_by_source_name", {}).get(name)
        return original_getitem(self, latest or name)

    cq.Assembly.add = add_with_unique_occurrence
    cq.Assembly.__getitem__ = getitem_by_source_name
    try:
        return cq.Assembly.importStep(str(path))
    finally:
        cq.Assembly.add = original_add
        cq.Assembly.__getitem__ = original_getitem


def _matrix4(location: Any) -> list[list[float]]:
    transform = location.wrapped.Transformation()
    rotation = [
        [float(transform.Value(row, col)) for col in range(1, 4)]
        for row in range(1, 4)
    ]
    translation = transform.TranslationPart()
    return [
        rotation[0] + [float(translation.X())],
        rotation[1] + [float(translation.Y())],
        rotation[2] + [float(translation.Z())],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _multiply4(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def detect_source_unit(assembly: Any) -> tuple[str, float, dict[str, float]]:
    """Infer the STEP model scale from the documented 1U envelope.

    CadQuery honours a unit declared in STEP; the import unit is only a fallback
    when no unit is declared. A BIRDS-4 1U assembly should be roughly 100 mm
    across, so the measured full-assembly bounding box distinguishes mm from m.
    """
    bounds = assembly.toCompound().BoundingBox()
    envelope = {
        "x": float(bounds.xlen),
        "y": float(bounds.ylen),
        "z": float(bounds.zlen),
    }
    maximum = max(envelope.values())
    if 50.0 <= maximum <= 250.0:
        return "MM", 0.001, envelope
    if 0.05 <= maximum <= 0.25:
        return "M", 1.0, envelope
    raise ValueError(
        "BIRDS-4 FM envelope is outside the documented 1U scale window; "
        f"cannot safely infer STEP units: {envelope}"
    )

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
        "source_label_name": str(node.metadata.get("source_component_name", node.name)),
        "duplicate_occurrence": node.metadata.get("duplicate_occurrence", 1),
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


def audit_reference_assembly(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify source labels conservatively; never promote CAD to as-built inventory."""
    import re
    counts = {"fm_named": 0, "em_named": 0, "b3_named": 0, "other": 0}
    labels: dict[str, int] = {}
    categories = {
        "reaction_wheel": re.compile(r"\bRW\b|reaction.?wheel", re.I),
        "obc": re.compile(r"\bOBC\b", re.I),
        "communications": re.compile(r"\bCOM\b|transceiver|trx", re.I),
        "battery": re.compile(r"battery", re.I),
        "solar_panel": re.compile(r"solar.?panel|panel", re.I),
        "attitude_sensor_candidate": re.compile(r"SNSS|sun.?sensor|magnetometer|gyro|IMU", re.I),
        "antenna": re.compile(r"antenna|GPS", re.I),
        "structure": re.compile(r"frame|rail|spacer|bracket|holder", re.I),
    }
    category_counts = {name: 0 for name in categories}
    em_labels, b3_labels = set(), set()
    for node in nodes:
        label = str(node.get("source_label_name", node.get("source_node_name", "")))
        labels[label] = labels.get(label, 0) + 1
        upper = label.upper()
        is_em = "B4_EM" in upper or re.search(r"(^|[_\s])EM([_\s]|$)", upper) is not None
        is_b3 = "B3_" in upper or "B3 " in upper
        is_fm = "B4_FM" in upper or "BIRDS4_FM" in upper
        if is_em:
            counts["em_named"] += 1
            em_labels.add(label)
        elif is_b3:
            counts["b3_named"] += 1
            b3_labels.add(label)
        elif is_fm:
            counts["fm_named"] += 1
        else:
            counts["other"] += 1
        for category, pattern in categories.items():
            if pattern.search(label):
                category_counts[category] += 1

    duplicate_labels = {
        name: count for name, count in sorted(labels.items()) if count > 1
    }
    return {
        "schema_version": "birds4-reference-assembly-review.v1",
        "source_model": "BIRDS4_FM_Final v7.step",
        "source_node_count": len(nodes),
        "unique_source_labels": len(labels),
        "duplicate_label_occurrences": duplicate_labels,
        "source_name_classification": counts,
        "em_named_labels": sorted(em_labels),
        "b3_named_labels": sorted(b3_labels),
        "candidate_category_node_counts": category_counts,
        "identity_status": "flight_unit_not_identified_from_cad_tree",
        "coordinate_status": "datum_and_axis_mapping_not_independently_reviewed",
        "installation_status": "as_built_installation_not_verified",
        "render_eligibility": False,
        "generated_equipment_instances": [],
        "required_follow_up": [
            "Compare EM/B3-named subassemblies with the BIRDS-4 FM assembly procedure and approved FM drawings.",
            "Identify the exact flight-unit variant before using any unit-specific panels or antenna boards.",
            "Review the STEP root datum, handedness, up/forward axes and the source-to-body frame transform.",
            "Promote only individually reviewed hardware occurrences to verified equipment instances.",
        ],
    }


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


def convert_one(record: dict[str, Any], source_cache: Path, output_root: Path, source_commit: str, cad_directory: str, source_unit: str, scale_to_m: float) -> dict[str, Any]:
    import cadquery as cq

    source_path, source_integrity = _download_pinned(record, source_cache, source_commit, cad_directory)
    assembly = _import_step_assembly(source_path)
    node_rows: list[dict[str, Any]] = []
    _walk_assembly(assembly, None, None, node_rows, scale_to_m)

    # Measure the source model before applying the unit conversion. GLB
    # scaling is applied to the exported root node, preserving all STEP placements.
    bounds = assembly.toCompound().BoundingBox()
    envelope = {
        "x_m": float(bounds.xlen) * scale_to_m,
        "y_m": float(bounds.ylen) * scale_to_m,
        "z_m": float(bounds.zlen) * scale_to_m,
    }
    if not all(math.isfinite(v) and v > 0 for v in envelope.values()):
        raise ValueError(f"invalid model envelope: {envelope}")

    output_root.mkdir(parents=True, exist_ok=True)
    stem = Path(record["path"]).stem.replace(" ", "_").replace("+", "plus").replace("-", "minus")
    glb_path = output_root / f"{stem}.glb"
    assembly.export(str(glb_path), tolerance=0.0005, angularTolerance=0.1)
    apply_glb_root_scale(glb_path, scale_to_m)
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
            "source_unit_detection": "inferred from BIRDS-4 1U FM assembly envelope after STEP unit-aware import",
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
    if record["path"] == "BIRDS4_FM_Final v7.step":
        assembly_review = audit_reference_assembly(node_rows)
        (output_root / "BIRDS4_FM_Final_v7.assembly-review.json").write_text(
            json.dumps(assembly_review, indent=2), encoding="utf-8"
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

    # Import the full FM assembly first. CadQuery honours explicit STEP
    # units and uses MM only as a fallback. The 1U envelope then independently
    # distinguishes millimetres from metres before any GLB scaling is applied.
    main_record = records["BIRDS4_FM_Final v7.step"]
    main_path, _ = _download_pinned(
        main_record, args.source_cache,
        manifest["source_commit"], manifest["cad_directory"]
    )
    import cadquery as cq
    main_assembly = _import_step_assembly(main_path)
    source_unit, scale_to_m, raw_envelope = detect_source_unit(main_assembly)
    print(
        f"[CAD] Unit evidence: source={source_unit}, scale={scale_to_m}, "
        f"raw_envelope={raw_envelope}",
        flush=True,
    )

    results = []
    for name in SELECTED:
        print(f"[CAD] Importing {name}", flush=True)
        results.append(convert_one(
            records[name], args.source_cache, args.output,
            manifest["source_commit"], manifest["cad_directory"],
            source_unit, scale_to_m
        ))
    review_path = args.output / "BIRDS4_FM_Final_v7.assembly-review.json"
    summary = {
        "schema_version": "birds4-cad-conversion-report.v1",
        "assembly_review": json.loads(review_path.read_text(encoding="utf-8")),
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
