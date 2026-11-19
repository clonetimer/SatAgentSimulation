"""Strict declaration boundaries shared by the M4 contract and evidence intake.

These checks establish consistency and digest syntax, not reviewer authenticity.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

_VIEWS = {
    "as_built_verified": ["as_built"],
    "simulation_configuration_geometry": ["simulation_configuration"],
    "reference_cad": ["reference_cad"],
    "unlocated": [],
}


def require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"P9M4_TEXT_REQUIRED:{field}")
    return value


def require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"P9M4_SHA256_REQUIRED:{field}")
    return value


def validate_mapping_policy(record: Any) -> None:
    if not isinstance(record, Mapping):
        raise ValueError("P9M4_RECORD_REQUIRED")
    kind = record.get("mapping_kind")
    if not isinstance(kind, str) or kind not in _VIEWS:
        raise ValueError(f"P9M4_UNKNOWN_MAPPING_KIND:{record.get('mapping_id')}:{kind}")
    render = record.get("render")
    if not isinstance(render, Mapping):
        raise ValueError("P9M4_RENDER_POLICY_REQUIRED")
    views = render.get("views")
    if (not isinstance(views, list) or any(not isinstance(v, str) for v in views)
            or len(views) != len(set(views))):
        raise ValueError("P9M4_VIEW_LIST_REQUIRED")
    if views != _VIEWS[kind]:
        raise ValueError(f"P9M4_VIEW_ISOLATION:{kind}:{views}")
    asset = record.get("geometry_asset")
    if isinstance(asset, Mapping):
        require_sha256(asset.get("sha256"), "geometry_asset.sha256")
        require_text(asset.get("asset_id"), "geometry_asset.asset_id")
        require_text(asset.get("source_node_path"), "geometry_asset.source_node_path")
    if kind == "as_built_verified":
        evidence = record.get("evidence")
        if isinstance(evidence, list):
            for item in evidence:
                if isinstance(item, Mapping) and item.get("decision") == "approved":
                    require_sha256(item.get("source_sha256"), "evidence.source_sha256")
                    require_text(item.get("reviewer"), "evidence.reviewer")
                    require_text(item.get("source_locator"), "evidence.source_locator")
    elif kind == "reference_cad":
        if record.get("simulation_binding") is not None:
            raise ValueError("P9M4_REFERENCE_CAD_SIMULATION_BINDING_FORBIDDEN")
        frame = record.get("geometry_frame")
        if frame is not None and (
            not isinstance(frame, Mapping)
            or frame.get("identity_status") != "unverified"
            or frame.get("installation_transform_parent_from_child") is not None
        ):
            raise ValueError("P9M4_REFERENCE_CAD_FRAME_MUST_REMAIN_UNVERIFIED")
    elif kind == "simulation_configuration_geometry":
        binding = record.get("simulation_binding")
        if isinstance(binding, Mapping) and binding.get("run_id") and binding.get("taskspec_sha256"):
            require_text(binding["run_id"], "simulation_binding.run_id")
            require_sha256(binding["taskspec_sha256"], "simulation_binding.taskspec_sha256")
            telemetry = record.get("telemetry_binding")
            if isinstance(telemetry, Mapping) and any(
                telemetry.get(key) != binding[key] for key in ("run_id", "taskspec_sha256")
            ):
                raise ValueError("P9M4_TELEMETRY_SCOPE_MISMATCH")
