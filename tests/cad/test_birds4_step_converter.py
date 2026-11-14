from __future__ import annotations

import importlib.util
import json
import struct
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/cad/convert_birds4_step.py"
SPEC = importlib.util.spec_from_file_location("birds4_step_converter", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _minimal_glb(path: Path):
    document = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"name": "root", "mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {}}]}],
    }
    json_chunk = json.dumps(document, separators=(",", ":")).encode("utf-8")
    json_chunk += b" " * ((4 - len(json_chunk) % 4) % 4)
    binary_chunk = b"\0\0\0\0"
    total = 12 + 8 + len(json_chunk) + 8 + len(binary_chunk)
    path.write_bytes(
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<I4s", len(json_chunk), b"JSON") + json_chunk
        + struct.pack("<I4s", len(binary_chunk), b"BIN\0") + binary_chunk
    )


def test_git_blob_sha1_uses_git_object_header():
    data = b"test STEP payload"
    import hashlib
    expected = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
    assert MODULE.git_blob_sha1(data) == expected


def test_source_pin_checks_exact_size_git_blob_sha_and_sha256():
    data = b"pinned source bytes"
    import hashlib
    record = MODULE.verify_source_bytes(
        data, len(data), MODULE.git_blob_sha1(data)
    )
    assert record["size_bytes"] == len(data)
    assert record["sha256"] == hashlib.sha256(data).hexdigest()


def test_source_pin_rejects_changed_or_truncated_bytes():
    data = b"pinned source bytes"
    with pytest.raises(ValueError, match="size mismatch"):
        MODULE.verify_source_bytes(data[:-1], len(data), MODULE.git_blob_sha1(data))
    with pytest.raises(ValueError, match="Git blob SHA-1 mismatch"):
        MODULE.verify_source_bytes(data + b"!", len(data) + 1, MODULE.git_blob_sha1(data))


def test_glb_parser_checks_glb2_header_and_geometry(tmp_path):
    path = tmp_path / "model.glb"
    _minimal_glb(path)
    result = MODULE.parse_glb(path)
    assert result["node_count"] == 1
    assert result["mesh_count"] == 1
    assert result["scene_count"] == 1
    assert result["root_node_indices"] == [0]


@pytest.mark.parametrize("payload", [b"", b"not a glb", b"glTF\x01\x00\x00\x00"])
def test_glb_parser_rejects_invalid_headers(tmp_path, payload):
    path = tmp_path / "bad.glb"
    path.write_bytes(payload)
    with pytest.raises(ValueError):
        MODULE.parse_glb(path)


def test_selected_conversion_targets_exist_in_pinned_manifest():
    manifest_path = Path("assets/cad-sources/birds4/birds4-cad-source-manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = {row["path"]: row for row in manifest["cad_files"]}
    assert len(records) == 46
    assert len(MODULE.SELECTED) == 6
    assert all(name in records for name in MODULE.SELECTED)
    assert records["BIRDS4_FM_Final v7.step"]["size_bytes"] == 22_964_753


class _Bounds:
    def __init__(self, x, y, z):
        self.xlen, self.ylen, self.zlen = x, y, z


class _Compound:
    def __init__(self, bounds):
        self._bounds = bounds

    def BoundingBox(self):
        return self._bounds


class _Assembly:
    def __init__(self, bounds):
        self._compound = _Compound(bounds)

    def toCompound(self):
        return self._compound


def test_birds4_unit_scale_is_inferred_from_nominal_1u_assembly_envelope():
    unit, scale, envelope = MODULE.detect_source_unit(_Assembly(_Bounds(100.2, 99.8, 103.1)))
    assert unit == "MM"
    assert scale == pytest.approx(0.001)
    assert envelope["z"] == pytest.approx(103.1)

    unit, scale, _ = MODULE.detect_source_unit(_Assembly(_Bounds(0.1002, 0.0998, 0.1031)))
    assert unit == "M"
    assert scale == pytest.approx(1.0)


def test_birds4_unit_scale_refuses_implausible_envelope():
    with pytest.raises(ValueError, match="cannot safely infer STEP units"):
        MODULE.detect_source_unit(_Assembly(_Bounds(10.0, 10.0, 10.0)))


def test_duplicate_cad_component_names_get_stable_unique_occurrence_ids():
    existing = {"root", "B4_DepSW v3", "B4_DepSW v3__instance_002"}
    name, occurrence = MODULE._unique_instance_name("B4_DepSW v3", existing)
    assert name == "B4_DepSW v3__instance_003"
    assert occurrence == 3
    name, occurrence = MODULE._unique_instance_name("new-part", existing)
    assert name == "new-part"
    assert occurrence == 1
