"""Evidence-gated spatial assets and as-built equipment instances.

This module validates metadata only. It never invents geometry, downloads assets,
or turns capability categories into installed equipment instances.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

SCHEMA_VERSION = "spatial-asset-registry.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ASSET_STATES = {"draft", "reviewed", "verified", "rejected"}
_INSTANCE_STATES = {"draft", "reviewed", "verified", "rejected"}


def _error(errors: list[dict[str, str]], code: str, subject: str, detail: str = "") -> None:
    row = {"code": code, "subject": subject}
    if detail:
        row["detail"] = detail
    errors.append(row)


def _finite_vector(value: Any, length: int) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) == length
        and all(
            isinstance(item, (int, float))
            and not isinstance(item, bool)
            and math.isfinite(float(item))
            for item in value
        )
    )


def _safe_asset_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        return False
    if "://" in value or value.startswith(("/", "./")):
        return False
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).drive:
        return False
    return all(part not in {"", ".", ".."} for part in value.split("/"))


def _object_rows(object_tree: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    if not isinstance(object_tree, Mapping):
        return {}
    rows: dict[str, Mapping[str, Any]] = {}
    stack = list(object_tree.get("roots", []))
    tree = object_tree.get("tree")
    if isinstance(tree, Mapping):
        stack.append(tree)
    while stack:
        row = stack.pop()
        if not isinstance(row, Mapping):
            continue
        object_id = row.get("object_id")
        if isinstance(object_id, str):
            rows[object_id] = row
        children = row.get("children")
        if isinstance(children, list):
            stack.extend(children)
    return rows


def validate_spatial_asset_registry(
    registry: Mapping[str, Any],
    *,
    object_tree: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate registry metadata and return only verified, evidence-backed instances.

    An empty asset/instance list is valid. That is the expected current-main
    state until reviewed CAD/as-built evidence is supplied.
    """
    errors: list[dict[str, str]] = []
    if not isinstance(registry, Mapping):
        return {"ok": False, "schema_version": SCHEMA_VERSION,
                "errors": [{"code": "REGISTRY_NOT_OBJECT", "subject": "registry"}],
                "renderable_instances": []}
    if registry.get("schema_version") != SCHEMA_VERSION:
        _error(errors, "SCHEMA_VERSION_INVALID", "registry.schema_version")
    assets = registry.get("assets", [])
    instances = registry.get("instances", [])
    if not isinstance(assets, list):
        _error(errors, "ASSETS_NOT_LIST", "assets")
        assets = []
    if not isinstance(instances, list):
        _error(errors, "INSTANCES_NOT_LIST", "instances")
        instances = []

    asset_index: dict[tuple[str, str], Mapping[str, Any]] = {}
    for index, asset in enumerate(assets):
        subject = f"assets[{index}]"
        if not isinstance(asset, Mapping):
            _error(errors, "ASSET_NOT_OBJECT", subject)
            continue
        asset_id, version = asset.get("asset_id"), asset.get("asset_version")
        if not isinstance(asset_id, str) or not _ID.fullmatch(asset_id):
            _error(errors, "ASSET_ID_INVALID", subject)
        if not isinstance(version, str) or not _ID.fullmatch(version):
            _error(errors, "ASSET_VERSION_INVALID", subject)
        key = (asset_id, version) if isinstance(asset_id, str) and isinstance(version, str) else None
        if key is not None and key in asset_index:
            _error(errors, "ASSET_VERSION_DUPLICATE", subject, f"{asset_id}@{version}")
        elif key is not None:
            asset_index[key] = asset
        if asset.get("asset_format") not in {"glb", "gltf"}:
            _error(errors, "ASSET_FORMAT_UNSUPPORTED", subject)
        if not _safe_asset_path(asset.get("path")):
            _error(errors, "ASSET_PATH_UNSAFE", subject)
        digest = asset.get("sha256")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            _error(errors, "ASSET_SHA256_INVALID", subject)
        size = asset.get("size_bytes")
        if type(size) is not int or size <= 0:
            _error(errors, "ASSET_SIZE_INVALID", subject)
        if asset.get("length_unit") != "m":
            _error(errors, "ASSET_LENGTH_UNIT_INVALID", subject)
        if asset.get("up_axis") not in {"X", "Y", "Z"} or asset.get("forward_axis") not in {"X", "Y", "Z"}:
            _error(errors, "ASSET_AXIS_INVALID", subject)
        if asset.get("up_axis") == asset.get("forward_axis"):
            _error(errors, "ASSET_AXIS_COLLISION", subject)
        if not isinstance(asset.get("origin_semantics"), str) or not asset["origin_semantics"].strip():
            _error(errors, "ASSET_ORIGIN_SEMANTICS_MISSING", subject)
        if asset.get("review_status") not in _ASSET_STATES:
            _error(errors, "ASSET_REVIEW_STATUS_INVALID", subject)
        evidence = asset.get("source_evidence")
        if not isinstance(evidence, Mapping) or not all(
            isinstance(evidence.get(key), str) and evidence[key].strip()
            for key in ("document_id", "revision", "locator")
        ):
            _error(errors, "ASSET_SOURCE_EVIDENCE_MISSING", subject)

    object_index = _object_rows(object_tree)
    instance_index: dict[str, Mapping[str, Any]] = {}
    parent_links: dict[str, str] = {}
    verified_candidates: list[Mapping[str, Any]] = []
    for index, instance in enumerate(instances):
        subject = f"instances[{index}]"
        if not isinstance(instance, Mapping):
            _error(errors, "INSTANCE_NOT_OBJECT", subject)
            continue
        instance_id = instance.get("instance_id")
        if not isinstance(instance_id, str) or not _ID.fullmatch(instance_id):
            _error(errors, "INSTANCE_ID_INVALID", subject)
            continue
        if instance_id in instance_index:
            _error(errors, "INSTANCE_ID_DUPLICATE", subject, instance_id)
            continue
        instance_index[instance_id] = instance
        object_id = instance.get("object_id")
        if not isinstance(object_id, str) or not object_id.startswith("component."):
            _error(errors, "INSTANCE_OBJECT_ID_INVALID", subject)
        if object_index:
            object_row = object_index.get(object_id)
            if object_row is None or object_row.get("kind") != "equipment":
                _error(errors, "INSTANCE_OBJECT_NOT_EQUIPMENT", subject, str(object_id))
        asset_ref = instance.get("asset_ref")
        asset = None
        if not isinstance(asset_ref, Mapping):
            _error(errors, "INSTANCE_ASSET_REF_MISSING", subject)
        else:
            key = (asset_ref.get("asset_id"), asset_ref.get("asset_version"))
            asset = asset_index.get(key) if all(isinstance(part, str) for part in key) else None
            if asset is None:
                _error(errors, "INSTANCE_ASSET_REF_UNRESOLVED", subject)
            elif asset_ref.get("sha256") != asset.get("sha256"):
                _error(errors, "INSTANCE_ASSET_HASH_MISMATCH", subject)
        parent = instance.get("parent_instance_id")
        if parent is not None:
            if not isinstance(parent, str) or not _ID.fullmatch(parent):
                _error(errors, "INSTANCE_PARENT_ID_INVALID", subject)
            else:
                parent_links[instance_id] = parent
        mount_frame = instance.get("mount_frame_id")
        if not isinstance(mount_frame, str) or not mount_frame.strip():
            _error(errors, "INSTANCE_MOUNT_FRAME_MISSING", subject)
        if not _finite_vector(instance.get("translation_m"), 3):
            _error(errors, "INSTANCE_TRANSLATION_INVALID", subject)
        quaternion = instance.get("rotation_q_wxyz")
        if not _finite_vector(quaternion, 4):
            _error(errors, "INSTANCE_QUATERNION_INVALID", subject)
        elif abs(math.hypot(*(float(v) for v in quaternion)) - 1.0) > 1e-6:
            _error(errors, "INSTANCE_QUATERNION_NOT_UNIT", subject)
        evidence = instance.get("source_evidence")
        if not isinstance(evidence, Mapping) or not all(
            isinstance(evidence.get(key), str) and evidence[key].strip()
            for key in ("document_id", "revision", "locator")
        ):
            _error(errors, "INSTANCE_SOURCE_EVIDENCE_MISSING", subject)
        if instance.get("verification_status") not in _INSTANCE_STATES:
            _error(errors, "INSTANCE_VERIFICATION_STATUS_INVALID", subject)
        if instance.get("verification_status") == "verified":
            verified_candidates.append(instance)
            if asset is not None and asset.get("review_status") != "verified":
                _error(errors, "VERIFIED_INSTANCE_ASSET_NOT_VERIFIED", subject)

    for instance_id, parent_id in parent_links.items():
        if parent_id not in instance_index:
            _error(errors, "INSTANCE_PARENT_UNRESOLVED", instance_id, parent_id)
    for instance_id in instance_index:
        visited: set[str] = set()
        current = instance_id
        while current in parent_links:
            if current in visited:
                _error(errors, "INSTANCE_PARENT_CYCLE", instance_id)
                break
            visited.add(current)
            current = parent_links[current]

    # Fail closed globally: no geometry is renderable if any registry contract
    # error exists. This prevents a partially corrupt assembly from appearing valid.
    renderable = []
    if not errors:
        for instance in verified_candidates:
            renderable.append({
                "instance_id": instance["instance_id"],
                "object_id": instance["object_id"],
                "asset_ref": dict(instance["asset_ref"]),
                "parent_instance_id": instance.get("parent_instance_id"),
                "mount_frame_id": instance["mount_frame_id"],
                "translation_m": list(instance["translation_m"]),
                "rotation_q_wxyz": list(instance["rotation_q_wxyz"]),
            })
    return {
        "ok": not errors,
        "schema_version": SCHEMA_VERSION,
        "asset_count": len(asset_index),
        "instance_count": len(instance_index),
        "renderable_instances": renderable,
        "errors": errors,
        "semantics": {
            "capability_categories_are_inventory": False,
            "empty_registry_is_valid": True,
            "unverified_instances_are_renderable": False,
        },
    }
