"""Fail-closed local TripoSR worker for G1 synthetic preview candidates.

The worker requires an explicitly licensed source image and a pinned local
TripoSR checkout/weights. It writes a raw GLB plus a provenance receipt only;
it never registers geometry, assigns physical scale, or creates as-built data.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from sat_sim.synthetic_asset_generation import audit_glb

TRIPOSR_REPOSITORY = "https://github.com/VAST-AI-Research/TripoSR"
TRIPOSR_REVISION = "107cefdc244c39106fa830359024f6a2f1c78871"
TRIPOSR_LICENSE_BLOB_SHA1 = "6c440ca1ef32cedcc2257b99953add129199ed26"
INPUT_SCHEMA_VERSION = "p9m4i-image-to-3d-input.v1"


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_path(path: str | Path) -> str:
    """Hash a file or a directory tree deterministically."""
    source = Path(path)
    if source.is_file():
        return sha256_file(source)
    if not source.is_dir():
        raise ValueError("P9M4I_WEIGHTS_PATH_MISSING")
    digest = hashlib.sha256()
    files = sorted(item for item in source.rglob("*") if item.is_file())
    if not files:
        raise ValueError("P9M4I_WEIGHTS_DIRECTORY_EMPTY")
    for item in files:
        relative = item.relative_to(source).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\\0")
        digest.update(bytes.fromhex(sha256_file(item)))
    return digest.hexdigest()


def _git_output(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def validate_image_to_3d_manifest(path: str | Path) -> dict[str, Any]:
    """Validate source permission, bytes, pinned model checkout and local weights."""
    manifest_path = Path(path).resolve()
    record = json.loads(manifest_path.read_text(encoding="utf-8"))
    if record.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise ValueError("P9M4I_IMAGE_INPUT_SCHEMA_INVALID")

    asset_id = record.get("asset_id")
    object_id = record.get("engineering_object_id")
    if not isinstance(asset_id, str) or not asset_id.strip():
        raise ValueError("P9M4I_ASSET_ID_REQUIRED")
    if object_id not in {
        "component.battery",
        "component.reaction_wheel",
        "component.star_tracker",
    }:
        raise ValueError("P9M4I_ENGINEERING_OBJECT_ID_INVALID")

    image = record.get("source_image")
    if not isinstance(image, Mapping):
        raise ValueError("P9M4I_SOURCE_IMAGE_REQUIRED")
    image_path = (manifest_path.parent / str(image.get("path") or "")).resolve()
    if not image_path.is_file():
        raise ValueError("P9M4I_SOURCE_IMAGE_MISSING")
    if not image.get("source_uri"):
        raise ValueError("P9M4I_SOURCE_URI_REQUIRED")
    if sha256_file(image_path) != image.get("sha256"):
        raise ValueError("P9M4I_SOURCE_IMAGE_DIGEST_MISMATCH")

    license_record = image.get("license")
    if not isinstance(license_record, Mapping) or license_record.get("status") != "approved":
        raise ValueError("P9M4I_SOURCE_LICENSE_NOT_APPROVED")
    permitted = set(license_record.get("permitted_uses") or [])
    if not {"model_generation", "internal_preview"}.issubset(permitted):
        raise ValueError("P9M4I_SOURCE_LICENSE_SCOPE_INSUFFICIENT")
    if not all(license_record.get(key) for key in ("evidence_uri", "reviewer", "review_date")):
        raise ValueError("P9M4I_SOURCE_LICENSE_EVIDENCE_REQUIRED")
    preprocessing = image.get("preprocessing")
    if not isinstance(preprocessing, Mapping) or preprocessing.get("background_removed") is not True:
        raise ValueError("P9M4I_BACKGROUND_PREPROCESSING_REQUIRED")
    if not preprocessing.get("method") or not preprocessing.get("reviewer"):
        raise ValueError("P9M4I_BACKGROUND_PREPROCESSING_PROVENANCE_REQUIRED")

    model = record.get("model")
    if not isinstance(model, Mapping):
        raise ValueError("P9M4I_MODEL_MANIFEST_REQUIRED")
    if model.get("repository") != TRIPOSR_REPOSITORY:
        raise ValueError("P9M4I_MODEL_REPOSITORY_UNEXPECTED")
    if model.get("revision") != TRIPOSR_REVISION:
        raise ValueError("P9M4I_MODEL_REVISION_NOT_PINNED")
    if model.get("license") != "MIT" or model.get("license_blob_sha1") != TRIPOSR_LICENSE_BLOB_SHA1:
        raise ValueError("P9M4I_MODEL_LICENSE_NOT_PINNED")
    repository = (manifest_path.parent / str(model.get("repository_path") or "")).resolve()
    if not (repository / "run.py").is_file() or not (repository / "LICENSE").is_file():
        raise ValueError("P9M4I_MODEL_CHECKOUT_INCOMPLETE")
    if _git_output(repository, "rev-parse", "HEAD") != TRIPOSR_REVISION:
        raise ValueError("P9M4I_MODEL_CHECKOUT_REVISION_MISMATCH")
    if _git_output(repository, "rev-parse", "HEAD:LICENSE") != TRIPOSR_LICENSE_BLOB_SHA1:
        raise ValueError("P9M4I_MODEL_LICENSE_BLOB_MISMATCH")

    weights_path = (manifest_path.parent / str(model.get("weights_path") or "")).resolve()
    if sha256_path(weights_path) != model.get("weights_sha256"):
        raise ValueError("P9M4I_MODEL_WEIGHTS_DIGEST_MISMATCH")

    config = record.get("config") or {}
    chunk_size = config.get("chunk_size", 4096)
    mc_resolution = config.get("mc_resolution", 256)
    minimum_free_vram_gb = config.get("minimum_free_vram_gb", 6.0)
    if type(chunk_size) is not int or chunk_size < 256:
        raise ValueError("P9M4I_TRIPOSR_CHUNK_SIZE_INVALID")
    if type(mc_resolution) is not int or not 128 <= mc_resolution <= 512:
        raise ValueError("P9M4I_TRIPOSR_MC_RESOLUTION_INVALID")
    if (
        type(minimum_free_vram_gb) not in (int, float)
        or not math.isfinite(minimum_free_vram_gb)
        or minimum_free_vram_gb < 1
    ):
        raise ValueError("P9M4I_TRIPOSR_MINIMUM_VRAM_INVALID")

    return {
        "record": record,
        "manifest_path": manifest_path,
        "image_path": image_path,
        "repository_path": repository,
        "weights_path": weights_path,
        "chunk_size": chunk_size,
        "mc_resolution": mc_resolution,
        "minimum_free_vram_gb": float(minimum_free_vram_gb),
    }


def probe_cuda(minimum_free_vram_gb: float = 6.0) -> dict[str, Any]:
    """Check actual CUDA device and free VRAM without installing dependencies."""
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("P9M4I_TORCH_REQUIRED_IN_WORKER_ENVIRONMENT") from exc
    if not torch.cuda.is_available():
        raise RuntimeError("P9M4I_CUDA_DEVICE_REQUIRED")
    free_bytes, total_bytes = torch.cuda.mem_get_info(0)
    free_gb = free_bytes / (1024 ** 3)
    total_gb = total_bytes / (1024 ** 3)
    if free_gb < minimum_free_vram_gb:
        raise RuntimeError(
            f"P9M4I_INSUFFICIENT_FREE_VRAM:required={minimum_free_vram_gb:.2f}GB:free={free_gb:.2f}GB"
        )
    properties = torch.cuda.get_device_properties(0)
    return {
        "device": properties.name,
        "total_vram_gb": round(total_gb, 3),
        "free_vram_gb": round(free_gb, 3),
    }


def run_triposr_g1(
    manifest_path: str | Path,
    output_dir: str | Path,
    *,
    gpu_probe=probe_cuda,
) -> dict[str, Any]:
    """Run the pinned local TripoSR checkout and audit its GLB output."""
    validated = validate_image_to_3d_manifest(manifest_path)
    gpu = gpu_probe(validated["minimum_free_vram_gb"])
    destination = Path(output_dir).resolve()
    mesh_path = destination / "0" / "mesh.glb"
    receipt_path = destination / "triposr_g1_receipt.json"
    if mesh_path.exists() or receipt_path.exists():
        raise ValueError("P9M4I_OUTPUT_ALREADY_EXISTS")
    destination.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        str(validated["repository_path"] / "run.py"),
        str(validated["image_path"]),
        "--output-dir",
        str(destination),
        "--device",
        "cuda:0",
        "--chunk-size",
        str(validated["chunk_size"]),
        "--mc-resolution",
        str(validated["mc_resolution"]),
        "--model-save-format",
        "glb",
        "--pretrained-model-name-or-path",
        str(validated["weights_path"]),
        "--no-remove-bg",
    ]
    environment = os.environ.copy()
    environment["HF_HUB_OFFLINE"] = "1"
    environment["TRANSFORMERS_OFFLINE"] = "1"
    subprocess.run(command, cwd=validated["repository_path"], env=environment, check=True)
    if not mesh_path.is_file():
        raise ValueError("P9M4I_TRIPOSR_GLB_OUTPUT_MISSING")

    glb_audit = audit_glb(mesh_path)
    record = validated["record"]
    receipt = {
        "schema_version": "p9m4i-triposr-g1-receipt.v1",
        "asset_id": record["asset_id"],
        "engineering_object_id": record["engineering_object_id"],
        "generation_method": "image_to_3d",
        "quality_grade": "G1",
        "generator": "TripoSR",
        "model_repository": TRIPOSR_REPOSITORY,
        "model_revision": TRIPOSR_REVISION,
        "model_license": "MIT",
        "source_uri": record["source_image"]["source_uri"],
        "source_sha256": record["source_image"]["sha256"],
        "source_license": dict(record["source_image"]["license"]),
        "configuration": {
            "chunk_size": validated["chunk_size"],
            "mc_resolution": validated["mc_resolution"],
            "background_removed_input": True,
        },
        "gpu": gpu,
        "glb_audit": glb_audit,
        "scale_status": "unknown_from_single_image",
        "visual_review": "pending",
        "engineering_review": "not_applicable",
        "registration_status": "not_registered",
        "render_eligible": False,
        "as_built_eligible": False,
        "asset_instance_id": None,
        "spatial_anchor": None,
        "telemetry_binding": None,
        "limitations": [
            "single-image reconstruction does not establish hidden geometry",
            "physical scale, mounting interfaces and boresight remain unknown",
            "G1 output is an isolated preview candidate, not a reference CAD or as-built asset",
        ],
    }
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt
