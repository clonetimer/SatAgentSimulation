from __future__ import annotations

import hashlib
import json
import struct
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from sat_sim import triposr_g1


def _write_glb(path: Path, points=None) -> None:
    if points is None:
        # Two non-coplanar triangles give the structural fixture non-zero XYZ
        # extents while retaining a minimal, non-indexed triangle-list mesh.
        points = [
            (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
            (0.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0),
        ]
    binary = struct.pack(f"<{len(points) * 3}f", *(value for point in points for value in point))
    mins = [min(point[axis] for point in points) for axis in range(3)]
    maxs = [max(point[axis] for point in points) for axis in range(3)]
    document = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"name": "synthetic-candidate", "mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(binary)}],
        "accessors": [{
            "bufferView": 0,
            "componentType": 5126,
            "count": len(points),
            "type": "VEC3",
            "min": mins,
            "max": maxs,
        }],
    }
    raw_json = json.dumps(document, separators=(",", ":")).encode("utf-8")
    raw_json += b" " * (-len(raw_json) % 4)
    binary += b"\0" * (-len(binary) % 4)
    total = 12 + 8 + len(raw_json) + 8 + len(binary)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<I4s", len(raw_json), b"JSON") + raw_json
        + struct.pack("<I4s", len(binary), b"BIN\0") + binary
    )


def test_audit_glb_rejects_zero_extent_geometry(tmp_path):
    planar_points = [
        (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0), (0.0, 1.0, 0.0), (1.0, 1.0, 0.0),
    ]
    path = tmp_path / "zero-depth.glb"
    _write_glb(path, points=planar_points)
    with pytest.raises(ValueError, match="P9M4I_GLB_ZERO_OR_INVALID_EXTENT"):
        triposr_g1.audit_glb(path)


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _fixture(tmp_path: Path, monkeypatch):
    model_root = tmp_path / "triposr"
    model_root.mkdir()
    (model_root / "LICENSE").write_text("MIT License\\n", encoding="utf-8")
    fake_run = """from pathlib import Path
import argparse
import json
import struct
parser = argparse.ArgumentParser()
parser.add_argument("image")
parser.add_argument("--output-dir", required=True)
args, _ = parser.parse_known_args()
points = [(0.0,0.0,0.0),(1.0,0.0,0.0),(0.0,1.0,0.0),
          (0.0,0.0,0.0),(0.0,1.0,0.0),(0.0,0.0,1.0)]
binary = struct.pack("<18f", *(v for p in points for v in p))
doc = {"asset":{"version":"2.0"},"scene":0,"scenes":[{"nodes":[0]}],
       "nodes":[{"mesh":0}],"meshes":[{"primitives":[{"attributes":{"POSITION":0}}]}],
       "buffers":[{"byteLength":len(binary)}],
       "bufferViews":[{"buffer":0,"byteOffset":0,"byteLength":len(binary)}],
       "accessors":[{"bufferView":0,"componentType":5126,"count":6,"type":"VEC3",
                     "min":[0.0,0.0,0.0],"max":[1.0,1.0,1.0]}]}
raw = json.dumps(doc,separators=(",",":")).encode("utf-8")
raw += b" " * (-len(raw) % 4)
binary += b"\\0" * (-len(binary) % 4)
total = 12 + 8 + len(raw) + 8 + len(binary)
out = Path(args.output_dir) / "0" / "mesh.glb"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_bytes(struct.pack("<4sII",b"glTF",2,total)
                + struct.pack("<I4s",len(raw),b"JSON") + raw
                + struct.pack("<I4s",len(binary),b"BIN\\0") + binary)
"""
    (model_root / "run.py").write_text(fake_run, encoding="utf-8")
    _git(model_root, "init")
    _git(model_root, "config", "user.email", "test@example.invalid")
    _git(model_root, "config", "user.name", "P9-M4-I Test")
    _git(model_root, "add", "LICENSE", "run.py")
    _git(model_root, "commit", "-m", "fixture")
    revision = _git(model_root, "rev-parse", "HEAD")
    license_blob = _git(model_root, "rev-parse", "HEAD:LICENSE")
    monkeypatch.setattr(triposr_g1, "TRIPOSR_REVISION", revision)
    monkeypatch.setattr(triposr_g1, "TRIPOSR_LICENSE_BLOB_SHA1", license_blob)

    weights = tmp_path / "weights"
    weights.mkdir()
    (weights / "config.json").write_text('{"model":"fixture"}', encoding="utf-8")
    (weights / "weights.bin").write_bytes(b"pinned-test-weights")
    image = tmp_path / "source.png"
    image.write_bytes(b"licensed-gray-background-image")
    manifest = {
        "schema_version": "p9m4i-image-to-3d-input.v1",
        "asset_id": "synthetic:star_tracker:g1:test",
        "engineering_object_id": "component.star_tracker",
        "source_image": {
            "path": "source.png",
            "source_uri": "https://example.invalid/approved-source-image",
            "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            "license": {
                "status": "approved",
                "permitted_uses": ["model_generation", "internal_preview"],
                "evidence_uri": "https://example.invalid/permission-letter",
                "reviewer": "test-reviewer",
                "review_date": "2026-11-19",
            },
            "preprocessing": {
                "background_removed": True,
                "method": "manual-gray-background",
                "reviewer": "test-reviewer",
            },
        },
        "model": {
            "repository": triposr_g1.TRIPOSR_REPOSITORY,
            "revision": revision,
            "license": "MIT",
            "license_blob_sha1": license_blob,
            "repository_path": "triposr",
            "weights_path": "weights",
            "weights_sha256": triposr_g1.sha256_path(weights),
        },
        "config": {
            "chunk_size": 4096,
            "mc_resolution": 256,
            "minimum_free_vram_gb": 6.0,
        },
    }
    manifest_path = tmp_path / "input.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path, manifest, model_root, weights


def test_image_to_3d_manifest_requires_explicit_source_permission(tmp_path, monkeypatch):
    manifest_path, manifest, _, _ = _fixture(tmp_path, monkeypatch)
    manifest["source_image"]["license"]["status"] = "unknown"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="SOURCE_LICENSE_NOT_APPROVED"):
        triposr_g1.validate_image_to_3d_manifest(manifest_path)


def test_image_to_3d_manifest_requires_model_generation_and_preview_scope(tmp_path, monkeypatch):
    manifest_path, manifest, _, _ = _fixture(tmp_path, monkeypatch)
    manifest["source_image"]["license"]["permitted_uses"] = ["model_generation"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="SOURCE_LICENSE_SCOPE_INSUFFICIENT"):
        triposr_g1.validate_image_to_3d_manifest(manifest_path)


def test_image_to_3d_manifest_checks_original_image_digest(tmp_path, monkeypatch):
    manifest_path, manifest, _, _ = _fixture(tmp_path, monkeypatch)
    manifest["source_image"]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="SOURCE_IMAGE_DIGEST_MISMATCH"):
        triposr_g1.validate_image_to_3d_manifest(manifest_path)


def test_image_to_3d_manifest_pins_model_revision_and_license(tmp_path, monkeypatch):
    manifest_path, manifest, _, _ = _fixture(tmp_path, monkeypatch)
    manifest["model"]["revision"] = "f" * 40
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="MODEL_REVISION_NOT_PINNED"):
        triposr_g1.validate_image_to_3d_manifest(manifest_path)


def test_triposr_worker_emits_unregistered_g1_candidate_and_audited_glb(tmp_path, monkeypatch):
    manifest_path, _, _, _ = _fixture(tmp_path, monkeypatch)
    output_dir = tmp_path / "output"
    receipt = triposr_g1.run_triposr_g1(
        manifest_path,
        output_dir,
        gpu_probe=lambda minimum: {"device": "test GPU", "total_vram_gb": 8.0, "free_vram_gb": 7.0},
    )
    assert receipt["quality_grade"] == "G1"
    assert receipt["glb_audit"]["status"] == "structurally_valid"
    assert receipt["scale_status"] == "unknown_from_single_image"
    assert receipt["visual_review"] == "pending"
    assert receipt["registration_status"] == "not_registered"
    assert receipt["render_eligible"] is False
    assert receipt["as_built_eligible"] is False
    assert receipt["asset_instance_id"] is None
    assert receipt["spatial_anchor"] is None
    assert receipt["telemetry_binding"] is None
    assert (output_dir / "0" / "mesh.glb").is_file()
    assert (output_dir / "triposr_g1_receipt.json").is_file()


def test_image_to_3d_manifest_schema_requires_permission_for_generation(tmp_path, monkeypatch):
    # Keep the production model pins before _fixture patches them to its local
    # temporary Git repository; this test validates the committed schema pins.
    pinned_revision = triposr_g1.TRIPOSR_REVISION
    pinned_license_blob_sha1 = triposr_g1.TRIPOSR_LICENSE_BLOB_SHA1
    manifest_path, manifest, _, _ = _fixture(tmp_path, monkeypatch)
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "src" / "sat_sim" / "schemas" / "p9m4i-image-to-3d-input.v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert schema["properties"]["model"]["properties"]["revision"]["const"] == pinned_revision
    assert schema["properties"]["model"]["properties"]["license_blob_sha1"]["const"] == pinned_license_blob_sha1
    manifest["model"]["revision"] = pinned_revision
    manifest["model"]["license_blob_sha1"] = pinned_license_blob_sha1
    Draft202012Validator(schema).validate(manifest)
    manifest["source_image"]["license"]["permitted_uses"] = ["model_generation"]
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(manifest)
