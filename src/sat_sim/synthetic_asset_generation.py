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
    binary_chunk = None
    chunk_number = 0
    while offset + 8 <= len(data):
        chunk_length, chunk_type = struct.unpack_from("<I4s", data, offset)
        offset += 8
        end = offset + chunk_length
        if end > len(data):
            raise ValueError("P9M4I_GLB_CHUNK_OUT_OF_BOUNDS")
        if chunk_number == 0 and chunk_type != b"JSON":
            raise ValueError("P9M4I_GLB_JSON_CHUNK_ORDER_INVALID")
        if chunk_type == b"JSON":
            if document is not None:
                raise ValueError("P9M4I_GLB_DUPLICATE_JSON_CHUNK")
            try:
                document = json.loads(data[offset:end].decode("utf-8").rstrip(" \0"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("P9M4I_GLB_JSON_INVALID") from exc
        elif chunk_type == b"BIN\0":
            if binary_chunk is not None:
                raise ValueError("P9M4I_GLB_DUPLICATE_BIN_CHUNK")
            binary_chunk = data[offset:end]
        offset = end
        chunk_number += 1
    if offset != len(data) or not isinstance(document, dict):
        raise ValueError("P9M4I_GLB_CHUNK_LAYOUT_INVALID")
    if document.get("asset", {}).get("version") != "2.0":
        raise ValueError("P9M4I_GLTF_VERSION_INVALID")

    nodes = document.get("nodes", [])
    meshes = document.get("meshes", [])
    accessors = document.get("accessors", [])
    buffer_views = document.get("bufferViews", [])
    buffers = document.get("buffers", [])
    materials = document.get("materials", [])
    scenes = document.get("scenes", [])
    if not all(isinstance(value, list) for value in (nodes, meshes, accessors, buffer_views, buffers)):
        raise ValueError("P9M4I_GLTF_ARRAY_INVALID")
    if len(buffers) != 1 or not isinstance(buffers[0], dict) or "uri" in buffers[0] or binary_chunk is None:
        raise ValueError("P9M4I_GLB_EMBEDDED_BINARY_BUFFER_REQUIRED")
    declared_buffer_length = buffers[0].get("byteLength")
    if type(declared_buffer_length) is not int or declared_buffer_length <= 0:
        raise ValueError("P9M4I_GLB_BUFFER_LENGTH_INVALID")
    if declared_buffer_length > len(binary_chunk) or len(binary_chunk) - declared_buffer_length > 3:
        raise ValueError("P9M4I_GLB_BUFFER_LENGTH_MISMATCH")
    for view_index, view in enumerate(buffer_views):
        if not isinstance(view, dict) or view.get("buffer") != 0:
            raise ValueError(f"P9M4I_BUFFER_VIEW_REFERENCE_INVALID:{view_index}")
        view_offset = view.get("byteOffset", 0)
        view_length = view.get("byteLength")
        if type(view_offset) is not int or type(view_length) is not int or view_offset < 0 or view_length <= 0:
            raise ValueError(f"P9M4I_BUFFER_VIEW_RANGE_INVALID:{view_index}")
        if view_offset + view_length > declared_buffer_length:
            raise ValueError(f"P9M4I_BUFFER_VIEW_OUT_OF_BOUNDS:{view_index}")

    def _accessor_layout(accessor: Mapping[str, Any], *, component_size: int, element_size: int, label: str):
        view_index = accessor.get("bufferView")
        if type(view_index) is not int or not 0 <= view_index < len(buffer_views):
            raise ValueError(f"P9M4I_{label}_BUFFER_VIEW_MISSING")
        view = buffer_views[view_index]
        accessor_offset = accessor.get("byteOffset", 0)
        view_offset = view.get("byteOffset", 0)
        stride = view.get("byteStride", element_size)
        if "sparse" in accessor:
            raise ValueError(f"P9M4I_{label}_SPARSE_ACCESSOR_UNSUPPORTED")
        if type(accessor_offset) is not int or accessor_offset < 0:
            raise ValueError(f"P9M4I_{label}_ACCESSOR_OFFSET_INVALID")
        if type(view_offset) is not int or view_offset < 0 or (view_offset + accessor_offset) % component_size:
            raise ValueError(f"P9M4I_{label}_ACCESSOR_ALIGNMENT_INVALID")
        if type(stride) is not int or stride < element_size or stride % component_size or stride > 252:
            raise ValueError(f"P9M4I_{label}_BYTE_STRIDE_INVALID")
        count = accessor.get("count")
        if type(count) is not int or count <= 0:
            raise ValueError(f"P9M4I_{label}_ACCESSOR_EMPTY")
        required_end = accessor_offset + (count - 1) * stride + element_size
        if required_end > view["byteLength"]:
            raise ValueError(f"P9M4I_{label}_ACCESSOR_OUT_OF_BOUNDS")
        return view["byteOffset"] + accessor_offset, stride
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
            position_offset, position_stride = _accessor_layout(
                accessor, component_size=4, element_size=12, label=f"POSITION_{position_index}"
            )
            bounds_min, bounds_max = accessor.get("min"), accessor.get("max")
            if not isinstance(bounds_min, list) or not isinstance(bounds_max, list) or len(bounds_min) != 3 or len(bounds_max) != 3:
                raise ValueError(f"P9M4I_POSITION_BOUNDS_MISSING:{position_index}")
            if any(not isinstance(v, (int, float)) or not math.isfinite(float(v)) for v in bounds_min + bounds_max):
                raise ValueError(f"P9M4I_POSITION_BOUNDS_NONFINITE:{position_index}")
            if any(float(bounds_min[i]) > float(bounds_max[i]) for i in range(3)):
                raise ValueError(f"P9M4I_POSITION_BOUNDS_INVERTED:{position_index}")
            actual_min = [math.inf, math.inf, math.inf]
            actual_max = [-math.inf, -math.inf, -math.inf]
            for vertex_index in range(accessor["count"]):
                vertex = struct.unpack_from("<3f", binary_chunk, position_offset + vertex_index * position_stride)
                if any(not math.isfinite(value) for value in vertex):
                    raise ValueError(f"P9M4I_POSITION_DATA_NONFINITE:{position_index}")
                for axis, value in enumerate(vertex):
                    actual_min[axis] = min(actual_min[axis], value)
                    actual_max[axis] = max(actual_max[axis], value)
            if any(
                abs(actual_min[i] - float(bounds_min[i])) > 1e-5
                or abs(actual_max[i] - float(bounds_max[i])) > 1e-5
                for i in range(3)
            ):
                raise ValueError(f"P9M4I_POSITION_BOUNDS_DATA_MISMATCH:{position_index}")
            mode = primitive.get("mode", 4)
            if type(mode) is not int or mode not in range(7):
                raise ValueError(f"P9M4I_PRIMITIVE_MODE_INVALID:{mesh_index}")
            material_index = primitive.get("material")
            if material_index is not None and (
                type(material_index) is not int or not 0 <= material_index < len(materials)
            ):
                raise ValueError(f"P9M4I_MATERIAL_REFERENCE_INVALID:{mesh_index}")
            index_accessor = primitive.get("indices")
            if index_accessor is not None:
                if type(index_accessor) is not int or not 0 <= index_accessor < len(accessors):
                    raise ValueError(f"P9M4I_INDEX_ACCESSOR_INVALID:{mesh_index}")
                index_data = accessors[index_accessor]
                index_component = index_data.get("componentType")
                index_format = {5121: ("<B", 1), 5123: ("<H", 2), 5125: ("<I", 4)}.get(index_component)
                if index_data.get("type") != "SCALAR" or index_format is None:
                    raise ValueError(f"P9M4I_INDEX_ACCESSOR_TYPE_INVALID:{mesh_index}")
                if "sparse" in index_data:
                    raise ValueError(f"P9M4I_INDEX_SPARSE_ACCESSOR_UNSUPPORTED:{mesh_index}")
                index_view_index = index_data.get("bufferView")
                if type(index_view_index) is not int or not 0 <= index_view_index < len(buffer_views):
                    raise ValueError(f"P9M4I_INDEX_BUFFER_VIEW_MISSING:{mesh_index}")
                if "byteStride" in buffer_views[index_view_index]:
                    raise ValueError(f"P9M4I_INDEX_BUFFER_VIEW_STRIDE_FORBIDDEN:{mesh_index}")
                index_count = index_data.get("count", 0)
                index_offset, index_stride = _accessor_layout(
                    index_data, component_size=index_format[1], element_size=index_format[1], label=f"INDEX_{mesh_index}"
                )
                for item_index in range(index_count):
                    value = struct.unpack_from(index_format[0], binary_chunk, index_offset + item_index * index_stride)[0]
                    if value >= accessor["count"]:
                        raise ValueError(f"P9M4I_INDEX_VALUE_OUT_OF_RANGE:{mesh_index}")
                if mode == 4:
                    if index_count % 3:
                        raise ValueError(f"P9M4I_TRIANGLE_INDEX_COUNT_INVALID:{mesh_index}")
                    triangle_count += index_count // 3
            elif mode == 4:
                if accessor["count"] % 3:
                    raise ValueError(f"P9M4I_TRIANGLE_VERTEX_COUNT_INVALID:{mesh_index}")
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
