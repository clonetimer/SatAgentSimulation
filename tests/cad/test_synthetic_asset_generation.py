from __future__ import annotations

import json
import struct
from pathlib import Path

from jsonschema import Draft202012Validator

import pytest

from sat_sim.synthetic_asset_generation import audit_glb, validate_synthetic_asset


def _write_glb_document(path, document, binary):
    raw = json.dumps(document, separators=(",", ":")).encode("utf-8")
    raw += b" " * (-len(raw) % 4)
    binary += b"\0" * (-len(binary) % 4)
    total_length = 12 + 8 + len(raw) + 8 + len(binary)
    path.write_bytes(
        struct.pack("<4sII", b"glTF", 2, total_length)
        + struct.pack("<I4s", len(raw), b"JSON") + raw
        + struct.pack("<I4s", len(binary), b"BIN\0") + binary
    )


def _read_glb_document(path):
    data = path.read_bytes()
    json_length, chunk_type = struct.unpack_from("<I4s", data, 12)
    assert chunk_type == b"JSON"
    document = json.loads(data[20:20 + json_length].decode("utf-8").rstrip(" \0"))
    binary_offset = 20 + json_length
    binary_length, binary_type = struct.unpack_from("<I4s", data, binary_offset)
    assert binary_type == b"BIN\0"
    return document, data[binary_offset + 8:binary_offset + 8 + binary_length]


def _write_glb(path, *, nodes=None, bounds=None):
    minimum, maximum = bounds if bounds else ([-0.03, -0.02, -0.01], [0.03, 0.02, 0.01])
    points = [
        minimum,
        maximum,
        [minimum[0], maximum[1], minimum[2]],
        [maximum[0], minimum[1], maximum[2]],
        [minimum[0], minimum[1], maximum[2]],
        [maximum[0], maximum[1], minimum[2]],
    ]
    binary = struct.pack("<18f", *(coordinate for point in points for coordinate in point))
    document = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": nodes or [{"name": "battery", "mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(binary)}],
        "accessors": [{
            "bufferView": 0, "byteOffset": 0,
            "componentType": 5126, "count": 6, "type": "VEC3",
            "min": minimum, "max": maximum,
        }],
        "materials": [{"name": "housing"}],
    }
    _write_glb_document(path, document, binary)


def _record(path):
    from sat_sim.synthetic_asset_generation import _sha256
    return {
        "schema_version": "synthetic-reference-geometry.v1",
        "asset_id": "synthetic:battery:g1",
        "engineering_object_id": "component.battery",
        "mapping_kind": "synthetic_reference_geometry",
        "generation_method": "image_to_3d",
        "quality_grade": "G1",
        "geometry": {"file": "battery.glb", "sha256": _sha256(path.read_bytes()), "unit": "m"},
        "provenance": {
            "generator": "TRELLIS.2",
            "model_version": "4B",
            "model_revision": "pinned-test-revision",
            "source_inputs": [{"source_uri": "https://example.invalid/battery-reference.png", "sha256": "a" * 64}],
        },
        "review": {"visual_review": "approved", "engineering_review": "not_applicable", "reviewer": "test-reviewer"},
        "render_view": "synthetic_preview",
        "as_built_eligible": False,
        "asset_instance_id": None,
        "spatial_anchor": None,
        "telemetry_binding": None,
    }


def test_glb_audit_reports_structure_digest_and_bounds(tmp_path):
    path = tmp_path / "battery.glb"
    _write_glb(path)
    report = audit_glb(path)
    assert report["status"] == "structurally_valid"
    assert len(report["sha256"]) == 64
    assert report["node_count"] == 1
    assert report["mesh_count"] == 1
    assert report["triangle_count_estimate"] == 2
    assert report["bounds"]["dimensions"] == pytest.approx([0.06, 0.04, 0.02])


def test_glb_audit_rejects_position_bounds_that_do_not_match_binary_payload(tmp_path):
    path = tmp_path / "mismatched-bounds.glb"
    _write_glb(path)
    document, binary = _read_glb_document(path)
    payload = bytearray(binary)
    struct.pack_into("<f", payload, 0, -0.5)
    _write_glb_document(path, document, bytes(payload))
    with pytest.raises(ValueError, match="POSITION_BOUNDS_DATA_MISMATCH"):
        audit_glb(path)


def test_synthetic_g1_asset_requires_visual_review_and_stays_out_of_as_built(tmp_path):
    path = tmp_path / "battery.glb"
    _write_glb(path)
    result = validate_synthetic_asset(_record(path), path)
    assert result["validation_status"] == "valid_for_synthetic_preview"
    assert result["mapping_kind"] == "synthetic_reference_geometry"
    assert result["as_built_eligible"] is False
    assert result["asset_instance_id"] is None
    assert result["telemetry_binding"] is None


def test_synthetic_asset_rejects_digest_mismatch(tmp_path):
    path = tmp_path / "battery.glb"
    _write_glb(path)
    record = _record(path)
    record["geometry"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="GENERATED_GLB_DIGEST_MISMATCH"):
        validate_synthetic_asset(record, path)


def test_synthetic_asset_rejects_attempted_as_built_promotion(tmp_path):
    path = tmp_path / "battery.glb"
    _write_glb(path)
    record = _record(path)
    record["as_built_eligible"] = True
    with pytest.raises(ValueError, match="AS_BUILT_PROMOTION_FORBIDDEN"):
        validate_synthetic_asset(record, path)


def test_synthetic_asset_rejects_missing_source_provenance(tmp_path):
    path = tmp_path / "battery.glb"
    _write_glb(path)
    record = _record(path)
    record["provenance"]["source_inputs"] = []
    with pytest.raises(ValueError, match="SOURCE_PROVENANCE_REQUIRED"):
        validate_synthetic_asset(record, path)


def test_g2_requires_engineering_review_and_dimension_interface_evidence(tmp_path):
    path = tmp_path / "battery.glb"
    _write_glb(path)
    record = _record(path)
    record["quality_grade"] = "G2"
    record["review"]["engineering_review"] = "pending"
    with pytest.raises(ValueError, match="G2_ENGINEERING_REVIEW_REQUIRED"):
        validate_synthetic_asset(record, path)


def test_glb_rejects_invalid_header(tmp_path):
    path = tmp_path / "bad.glb"
    path.write_bytes(b"not a glb")
    with pytest.raises(ValueError, match="GLB_TOO_SHORT"):
        audit_glb(path)


def test_glb_rejects_missing_position_accessor(tmp_path):
    path = tmp_path / "bad.glb"
    _write_glb(path)
    doc, binary = _read_glb_document(path)
    doc["meshes"][0]["primitives"][0]["attributes"] = {}
    _write_glb_document(path, doc, binary)
    with pytest.raises(ValueError, match="POSITION_ACCESSOR_MISSING"):
        audit_glb(path)
from pathlib import Path


def test_freecad_parametric_macro_requires_source_backed_dimensions_and_positions():
    macro = (Path(__file__).resolve().parents[2] / "scripts" / "cad" / "build_parametric_synthetic_asset.FCMacro").read_text(encoding="utf-8")
    assert 'UNSOURCED_FEATURE_DIMENSION' in macro
    assert 'POSITION_SOURCE' in macro
    assert 'Part.makeBox' in macro
    assert 'Part.makeCylinder' in macro
    assert 'doc.saveAs' in macro
    assert 'Import.export' in macro
    assert 'telemetry' not in macro.lower()


def test_synthetic_asset_record_conforms_to_published_json_schema(tmp_path):
    path = tmp_path / "battery.glb"
    _write_glb(path)
    schema_path = Path(__file__).resolve().parents[2] / "src" / "sat_sim" / "schemas" / "synthetic-reference-geometry.v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(_record(path))


def test_published_schema_rejects_telemetry_and_as_built_claims(tmp_path):
    path = tmp_path / "battery.glb"
    _write_glb(path)
    record = _record(path)
    record["telemetry_binding"] = {"binding_status": "approved"}
    schema_path = Path(__file__).resolve().parents[2] / "src" / "sat_sim" / "schemas" / "synthetic-reference-geometry.v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(record)


def test_glb_rejects_invalid_material_reference(tmp_path):
    path = tmp_path / "bad-material.glb"
    _write_glb(path)
    doc, binary = _read_glb_document(path)
    doc["meshes"][0]["primitives"][0]["material"] = 99
    _write_glb_document(path, doc, binary)
    with pytest.raises(ValueError, match="MATERIAL_REFERENCE_INVALID"):
        audit_glb(path)


def test_glb_rejects_triangle_vertex_count_not_divisible_by_three(tmp_path):
    path = tmp_path / "bad-triangle-count.glb"
    _write_glb(path)
    doc, binary = _read_glb_document(path)
    doc["accessors"][0]["count"] = 5
    _write_glb_document(path, doc, binary)
    with pytest.raises(ValueError, match="TRIANGLE_VERTEX_COUNT_INVALID"):
        audit_glb(path)
