"""P9-M4 unified geometry/engineering/physics mapping contract.

This contract keeps as-built geometry, reference CAD and simulation-configuration
geometry distinct. It validates declarations; it never infers missing evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from typing import Any, Mapping

from sat_sim.engineering_mapping_policy import validate_mapping_policy

SCHEMA_VERSION = "engineering-asset-mapping.v1"
MAPPING_KINDS = {
    "as_built_verified",
    "simulation_configuration_geometry",
    "reference_cad",
    "unlocated",
}
REQUIRED_AS_BUILT_EVIDENCE = {
    "flight_unit_identity",
    "hardware_identity",
    "assembly_relation",
    "coordinate_datum",
    "installation_transform",
}


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _validate_matrix(matrix: Any, mapping_id: str) -> None:
    if not isinstance(matrix, list) or len(matrix) != 4:
        raise ValueError(f"P9M4_INVALID_TRANSFORM:{mapping_id}")
    rows = []
    for row in matrix:
        if not isinstance(row, list) or len(row) != 4:
            raise ValueError(f"P9M4_INVALID_TRANSFORM:{mapping_id}")
        converted = []
        for value in row:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"P9M4_NONFINITE_TRANSFORM:{mapping_id}")
            converted.append(float(value))
        rows.append(converted)
    if any(abs(rows[3][i] - expected) > 1e-6 for i, expected in enumerate((0, 0, 0, 1))):
        raise ValueError(f"P9M4_INVALID_HOMOGENEOUS_ROW:{mapping_id}")
    rotation = [[rows[i][j] for j in range(3)] for i in range(3)]
    gram = [[sum(rotation[k][i] * rotation[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    error = max(abs(gram[i][j] - (1.0 if i == j else 0.0)) for i in range(3) for j in range(3))
    determinant = (
        rotation[0][0] * (rotation[1][1] * rotation[2][2] - rotation[1][2] * rotation[2][1])
        - rotation[0][1] * (rotation[1][0] * rotation[2][2] - rotation[1][2] * rotation[2][0])
        + rotation[0][2] * (rotation[1][0] * rotation[2][1] - rotation[1][1] * rotation[2][0])
    )
    if error > 1e-6 or abs(determinant - 1.0) > 1e-6:
        raise ValueError(f"P9M4_TRANSFORM_NOT_PROPER_RIGID:{mapping_id}")


def validate_mapping_record(record: Mapping[str, Any]) -> dict[str, Any]:
    validate_mapping_policy(record)
    mapping_id = record.get("mapping_id")
    object_id = record.get("object_id")
    kind = record.get("mapping_kind")
    if not isinstance(mapping_id, str) or not mapping_id.strip():
        raise ValueError("P9M4_MAPPING_ID_REQUIRED")
    if not isinstance(object_id, str) or not object_id.strip():
        raise ValueError(f"P9M4_OBJECT_ID_REQUIRED:{mapping_id}")
    if kind not in MAPPING_KINDS:
        raise ValueError(f"P9M4_UNKNOWN_MAPPING_KIND:{mapping_id}:{kind}")

    asset = record.get("geometry_asset")
    frame = record.get("geometry_frame")
    instance_id = record.get("asset_instance_id")
    spatial_anchor = record.get("spatial_anchor")
    telemetry = record.get("telemetry_binding")
    render = record.get("render", {})
    evidence = record.get("evidence", [])
    if not isinstance(render, Mapping):
        raise ValueError(f"P9M4_RENDER_POLICY_REQUIRED:{mapping_id}")
    if evidence is None:
        evidence = []
    if not isinstance(evidence, list):
        raise ValueError(f"P9M4_EVIDENCE_LIST_REQUIRED:{mapping_id}")
    if telemetry is not None and not isinstance(telemetry, Mapping):
        raise ValueError(f"P9M4_TELEMETRY_BINDING_INVALID:{mapping_id}")

    if kind == "as_built_verified":
        if not isinstance(asset, Mapping) or not asset.get("sha256") or not asset.get("source_node_path"):
            raise ValueError(f"P9M4_AS_BUILT_ASSET_PROVENANCE_REQUIRED:{mapping_id}")
        if not isinstance(instance_id, str) or not instance_id.strip():
            raise ValueError(f"P9M4_AS_BUILT_INSTANCE_ID_REQUIRED:{mapping_id}")
        if not isinstance(spatial_anchor, Mapping) or not spatial_anchor.get("parent_instance_id"):
            raise ValueError(f"P9M4_AS_BUILT_PARENT_ANCHOR_REQUIRED:{mapping_id}")
        if (
            not isinstance(frame, Mapping)
            or frame.get("identity_status") != "approved"
            or frame.get("handedness") != "right"
            or not frame.get("frame_id")
            or not frame.get("parent_frame_id")
        ):
            raise ValueError(f"P9M4_AS_BUILT_FRAME_APPROVAL_REQUIRED:{mapping_id}")
        _validate_matrix(frame.get("installation_transform_parent_from_child"), mapping_id)
        applicability = record.get("applicability", {})
        if not isinstance(applicability, Mapping) or not all(applicability.get(key) for key in ("spacecraft_model", "flight_unit", "hardware_part_number", "hardware_revision")):
            raise ValueError(f"P9M4_AS_BUILT_APPLICABILITY_REQUIRED:{mapping_id}")
        approved = {
            item.get("claim_type") for item in evidence
            if isinstance(item, Mapping)
            and item.get("decision") == "approved"
            and item.get("reviewer")
            and item.get("source_sha256")
            and item.get("source_locator")
        }
        if not REQUIRED_AS_BUILT_EVIDENCE.issubset(approved):
            missing = sorted(REQUIRED_AS_BUILT_EVIDENCE - approved)
            raise ValueError(f"P9M4_AS_BUILT_EVIDENCE_INCOMPLETE:{mapping_id}:{','.join(missing)}")
        if telemetry is not None and telemetry.get("binding_status") != "approved":
            raise ValueError(f"P9M4_AS_BUILT_TELEMETRY_BINDING_NOT_APPROVED:{mapping_id}")
        if render.get("as_built_eligible") is not True:
            raise ValueError(f"P9M4_AS_BUILT_RENDER_GATE_REQUIRED:{mapping_id}")
        if "as_built" not in render.get("views", []):
            raise ValueError(f"P9M4_AS_BUILT_VIEW_REQUIRED:{mapping_id}")

    elif kind == "simulation_configuration_geometry":
        if instance_id is not None or spatial_anchor is not None:
            raise ValueError(f"P9M4_SIMULATION_GEOMETRY_MUST_NOT_CLAIM_AS_BUILT_INSTANCE:{mapping_id}")
        if not isinstance(frame, Mapping) or frame.get("identity_status") != "task_spec_configuration":
            raise ValueError(f"P9M4_SIMULATION_CONFIGURATION_FRAME_REQUIRED:{mapping_id}")
        simulation_binding = record.get("simulation_binding")
        if (
            not isinstance(simulation_binding, Mapping)
            or not simulation_binding.get("run_id")
            or not simulation_binding.get("taskspec_sha256")
        ):
            raise ValueError(f"P9M4_SIMULATION_RUN_BINDING_REQUIRED:{mapping_id}")
        if render.get("as_built_eligible") is not False:
            raise ValueError(f"P9M4_SIMULATION_GEOMETRY_AS_BUILT_FORBIDDEN:{mapping_id}")
        if "simulation_configuration" not in render.get("views", []):
            raise ValueError(f"P9M4_SIMULATION_CONFIGURATION_VIEW_REQUIRED:{mapping_id}")
        if telemetry is not None and telemetry.get("binding_status") not in ("run_scoped", "approved"):
            raise ValueError(f"P9M4_SIMULATION_TELEMETRY_BINDING_INVALID:{mapping_id}")

    elif kind == "reference_cad":
        if instance_id is not None or spatial_anchor is not None or telemetry is not None:
            raise ValueError(f"P9M4_REFERENCE_CAD_CANNOT_CLAIM_INSTANCE_OR_TELEMETRY:{mapping_id}")
        if render.get("as_built_eligible") is not False:
            raise ValueError(f"P9M4_REFERENCE_CAD_AS_BUILT_FORBIDDEN:{mapping_id}")
        if "reference_cad" not in render.get("views", []):
            raise ValueError(f"P9M4_REFERENCE_CAD_VIEW_REQUIRED:{mapping_id}")
        if not isinstance(asset, Mapping) or not asset.get("sha256") or not asset.get("source_node_path"):
            raise ValueError(f"P9M4_REFERENCE_CAD_PROVENANCE_REQUIRED:{mapping_id}")

    elif kind == "unlocated":
        if asset is not None or frame is not None or spatial_anchor is not None or instance_id is not None:
            raise ValueError(f"P9M4_UNLOCATED_OBJECT_HAS_GEOMETRY:{mapping_id}")
        if render.get("as_built_eligible") is not False or render.get("renderable") is not False:
            raise ValueError(f"P9M4_UNLOCATED_OBJECT_RENDER_FORBIDDEN:{mapping_id}")

    return {
        "mapping_id": mapping_id,
        "object_id": object_id,
        "mapping_kind": kind,
        "validation_status": "valid",
        "validation_scope": "declaration_consistency_only",
        "approval_authenticity_verified": False,
        "asset_instance_id": instance_id,
        "as_built_eligible": bool(render.get("as_built_eligible")),
        "render_views": list(render.get("views", [])),
        "record_sha256": _digest(record),
    }


def validate_mapping_bundle(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("P9M4_UNSUPPORTED_SCHEMA_VERSION")
    records = payload.get("mappings")
    if not isinstance(records, list):
        raise ValueError("P9M4_MAPPING_LIST_REQUIRED")
    validated = []
    seen_mapping_ids = set()
    seen_instance_ids = set()
    for record in records:
        result = validate_mapping_record(record)
        if result["mapping_id"] in seen_mapping_ids:
            raise ValueError(f"P9M4_DUPLICATE_MAPPING_ID:{result['mapping_id']}")
        seen_mapping_ids.add(result["mapping_id"])
        instance_id = result["asset_instance_id"]
        if instance_id:
            if instance_id in seen_instance_ids:
                raise ValueError(f"P9M4_DUPLICATE_ASSET_INSTANCE_ID:{instance_id}")
            seen_instance_ids.add(instance_id)
        validated.append(result)

    counts = Counter(row["mapping_kind"] for row in validated)
    return {
        "schema_version": "engineering-asset-mapping-validation.v1",
        "mapping_count": len(validated),
        "counts_by_kind": dict(sorted(counts.items())),
        "as_built_verified_count": counts["as_built_verified"],
        "simulation_configuration_geometry_count": counts["simulation_configuration_geometry"],
        "reference_cad_count": counts["reference_cad"],
        "unlocated_count": counts["unlocated"],
        "render_eligible_as_built_count": sum(1 for row in validated if row["as_built_eligible"]),
        "mappings": validated,
        "semantics": {
            "mapping_kinds_are_mutually_distinct": True,
            "reference_cad_is_not_as_built": True,
            "simulation_configuration_is_not_as_built": True,
            "unlocated_objects_do_not_get_fake_geometry": True,
            "missing_evidence_fails_closed": True,
        },
    }
