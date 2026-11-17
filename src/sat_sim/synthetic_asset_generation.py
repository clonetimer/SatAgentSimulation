"""P9-M4-I synthetic engineering geometry intake and dependency-free GLB audit.

AI-generated meshes are synthetic reference assets. They are not CAD authority,
physical installation evidence, telemetry identities or as-built instances.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "synthetic-reference-geometry.v1"
ALLOWED_METHODS = {"parametric_cad", "image_to_3d", "text_to_3d", "multiview_reconstruction"}
ALLOWED_GRADES = {"G1", "G2"}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def audit_glb(path: str | Path) -> dict[str, Any]:
    """Audit core GLB structure and POSITION accessor bounds without loading a renderer."""
    source = Path(path)
    data = source.read_bytes()
    if len(data) < 20:
        raise ValueError("P9M4I_GLB_TOO_SHORT")
    magic, version, declared_length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2 or declared_length != len(data):
        raise ValueError("P9M4I_GLB_HEADER_INVALID")
    offset = 12
    document = None
    while offset + 8 <= len(data):
        chunk_length, chunk_type = struct.unpack_from("<I4s", data, offset)
        offset += 8
        end = offset + chunk_length
        if end > len(data):
            raise ValueError("P9M4I_GLB_CHUNK_OUT_OF_BOUNDS")
        if chunk_type == b"JSON":
            if document is not None:
                raise ValueError("P9M4I_GLB_DUPLICATE_JSON_CHUNK")
            try:
                document = json.loads(data[offset:end].decode("utf-8").rstrip(" \0"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("P9M4I_GLB_JSON_INVALID") from exc
        offset = end
    if offset != len(data) or not isinstance(document, dict):
        raise ValueError("P9M4I_GLB_CHUNK_LAYOUT_INVALID")
    if document.get("asset", {}).get("version") != "2.0":
        raise ValueError("P9M4I_GLTF_VERSION_INVALID")

    nodes = document.get("nodes", [])
    meshes = document.get("meshes", [])
    accessors = document.get("accessors", [])
    materials = document.get("materials", [])
    scenes = document.get("scenes", [])
    if not isinstance(nodes, list) or not isinstance(meshes, list) or not isinstance(accessors, list):
        raise ValueError("P9M4I_GLTF_ARRAY_INVALID")
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise ValueError(f"P9M4I_NODE_INVALID:{index}")
        mesh_index = node.get("mesh")
        if mesh_index is not None and (type(mesh_index) is not int or not 0 <= mesh_index < len(meshes)):
            raise ValueError(f"P9M4I_NODE_MESH_REFERENCE_INVALID:{index}")
        for child in node.get("children", []):
            if type(child) is not int or not 0 <= child < len(nodes) or child == index:
                raise ValueError(f"P9M4I_NODE_CHILD_REFERENCE_INVALID:{index}")
    triangle_count = 0
    position_accessor_indices = set()
    mesh_primitive_count = 0
    for mesh_index, mesh in enumerate(meshes):
        for primitive in mesh.get("primitives", []):
            mesh_primitive_count += 1
            attributes = primitive.get("attributes", {})
            position_index = attributes.get("POSITION")
            if type(position_index) is not int or not 0 <= position_index < len(accessors):
                raise ValueError(f"P9M4I_POSITION_ACCESSOR_MISSING:{mesh_index}")
            position_accessor_indices.add(position_index)
            accessor = accessors[position_index]
            if accessor.get("type") != "VEC3" or accessor.get("componentType") not in (5126,):
                raise ValueError(f"P9M4I_POSITION_ACCESSOR_TYPE_INVALID:{position_index}")
            if type(accessor.get("count")) is not int or accessor["count"] <= 0:
                raise ValueError(f"P9M4I_POSITION_ACCESSOR_EMPTY:{position_index}")
            bounds_min, bounds_max = accessor.get("min"), accessor.get("max")
            if not isinstance(bounds_min, list) or not isinstance(bounds_max, list) or len(bounds_min) != 3 or len(bounds_max) != 3:
                raise ValueError(f"P9M4I_POSITION_BOUNDS_MISSING:{position_index}")
            if any(not isinstance(v, (int, float)) or not math.isfinite(float(v)) for v in bounds_min + bounds_max):
                raise ValueError(f"P9M4I_POSITION_BOUNDS_NONFINITE:{position_index}")
            if any(float(bounds_min[i]) > float(bounds_max[i]) for i in range(3)):
                raise ValueError(f"P9M4I_POSITION_BOUNDS_INVERTED:{position_index}")
            index_accessor = primitive.get("indices")
            if index_accessor is not None:
                if type(index_accessor) is not int or not 0 <= index_accessor < len(accessors):
                    raise ValueError(f"P9M4I_INDEX_ACCESSOR_INVALID:{mesh_index}")
                index_count = accessors[index_accessor].get("count", 0)
                if type(index_count) is not int or index_count <= 0:
                    raise ValueError(f"P9M4I_INDEX_ACCESSOR_EMPTY:{mesh_index}")
                mode = primitive.get("mode", 4)
                if mode == 4:
                    triangle_count += index_count // 3
            elif primitive.get("mode", 4) == 4:
                triangle_count += accessor["count"] // 3
    if not nodes or not meshes or not position_accessor_indices:
        raise ValueError("P9M4I_GLB_NO_RENDERABLE_GEOMETRY")

    mins = [min(float(accessors[i]["min"][axis]) for i in position_accessor_indices) for axis in range(3)]
    maxs = [max(float(accessors[i]["max"][axis]) for i in position_accessor_indices) for axis in range(3)]
    dimensions = [maxs[i] - mins[i] for i in range(3)]
    if any(not math.isfinite(v) or v <= 0 for v in dimensions):
        raise ValueError("P9M4I_GLB_ZERO_OR_INVALID_EXTENT")
    return {
        "status": "structurally_valid",
        "sha256": _sha256(data),
        "size_bytes": len(data),
        "node_count": len(nodes),
        "mesh_count": len(meshes),
        "primitive_count": mesh_primitive_count,
        "position_accessor_count": len(position_accessor_indices),
        "material_count": len(materials),
        "scene_count": len(scenes),
        "triangle_count_estimate": triangle_count,
        "bounds": {"min": mins, "max": maxs, "dimensions": dimensions, "unit": "declared_by_asset_manifest"},
        "limitations": [
            "GLB structural validity does not prove geometric accuracy or engineering dimensions",
            "accessor bounds do not independently prove scene-world bounds",
            "visual inspection and source-backed dimensions are required for G1/G2 acceptance",
        ],
    }


def validate_synthetic_asset(record: Mapping[str, Any], glb_path: str | Path) -> dict[str, Any]:
    if record.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("P9M4I_UNSUPPORTED_SYNTHETIC_ASSET_SCHEMA")
    asset_id = record.get("asset_id")
    if not isinstance(asset_id, str) or not asset_id.strip():
        raise ValueError("P9M4I_ASSET_ID_REQUIRED")
    if record.get("generation_method") not in ALLOWED_METHODS:
        raise ValueError("P9M4I_GENERATION_METHOD_INVALID")
    grade = record.get("quality_grade")
    if grade not in ALLOWED_GRADES:
        raise ValueError("P9M4I_QUALITY_GRADE_INVALID")
    if record.get("mapping_kind") != "synthetic_reference_geometry":
        raise ValueError("P9M4I_SYNTHETIC_MAPPING_KIND_REQUIRED")
    if record.get("as_built_eligible") is not False:
        raise ValueError("P9M4I_AS_BUILT_PROMOTION_FORBIDDEN")
    if record.get("telemetry_binding") is not None or record.get("asset_instance_id") is not None:
        raise ValueError("P9M4I_INSTANCE_OR_TELEMETRY_BINDING_FORBIDDEN")
    if record.get("spatial_anchor") is not None:
        raise ValueError("P9M4I_SPATIAL_ANCHOR_FORBIDDEN")
    if record.get("render_view") != "synthetic_preview":
        raise ValueError("P9M4I_SYNTHETIC_VIEW_REQUIRED")
    provenance = record.get("provenance")
    if not isinstance(provenance, Mapping) or not provenance.get("source_inputs"):
        raise ValueError("P9M4I_SOURCE_PROVENANCE_REQUIRED")
    for source in provenance["source_inputs"]:
        if not isinstance(source, Mapping) or not source.get("source_uri") or not source.get("sha256"):
            raise ValueError("P9M4I_SOURCE_INPUT_UNTRACEABLE")
    audit = audit_glb(glb_path)
    expected = record.get("geometry", {}).get("sha256")
    if expected != audit["sha256"]:
        raise ValueError("P9M4I_GENERATED_GLB_DIGEST_MISMATCH")
    units = record.get("geometry", {}).get("unit")
    if units not in ("m", "mm"):
        raise ValueError("P9M4I_GEOMETRY_UNIT_REQUIRED")
    review = record.get("review", {})
    if review.get("visual_review") != "approved":
        raise ValueError("P9M4I_VISUAL_REVIEW_REQUIRED")
    if grade == "G2":
        if review.get("engineering_review") != "approved":
            raise ValueError("P9M4I_G2_ENGINEERING_REVIEW_REQUIRED")
        if not record.get("dimension_evidence"):
            raise ValueError("P9M4I_G2_DIMENSION_EVIDENCE_REQUIRED")
        if not record.get("interface_evidence"):
            raise ValueError("P9M4I_G2_INTERFACE_EVIDENCE_REQUIRED")
    return {
        "asset_id": asset_id,
        "quality_grade": grade,
        "mapping_kind": "synthetic_reference_geometry",
        "validation_status": "valid_for_synthetic_preview",
        "as_built_eligible": False,
        "telemetry_binding": None,
        "asset_instance_id": None,
        "glb_audit": audit,
    }
