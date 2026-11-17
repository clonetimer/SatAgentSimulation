"""P9-M3 evidence review packet builder for reference-CAD installation candidates.

CAD transforms are preserved as source geometry facts, never promoted to as-built
installation transforms without independent identity, assembly and datum evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "cad-installation-evidence-review.v1"
REQUIRED_EVIDENCE = {
    "flight_unit_identity": ("approved_flight_unit_configuration", "approved_bom_or_configuration_list"),
    "hardware_identity": ("approved_part_drawing", "part_number_and_revision"),
    "assembly_relation": ("fm_assembly_procedure_step", "parent_child_assembly_drawing"),
    "coordinate_datum": ("coordinate_system_definition", "datum_and_axis_drawing"),
    "installation_transform": ("approved_installation_transform", "transform_chain_and_frame_definition"),
}


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _matrix4(value: Any, field: str) -> list[list[float]]:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError(f"P9M3_INVALID_MATRIX:{field}")
    matrix: list[list[float]] = []
    for row in value:
        if not isinstance(row, list) or len(row) != 4:
            raise ValueError(f"P9M3_INVALID_MATRIX:{field}")
        converted = []
        for item in row:
            if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)):
                raise ValueError(f"P9M3_NONFINITE_MATRIX_VALUE:{field}")
            converted.append(float(item))
        matrix.append(converted)
    return matrix


def _matmul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _det3(r: list[list[float]]) -> float:
    return (
        r[0][0] * (r[1][1] * r[2][2] - r[1][2] * r[2][1])
        - r[0][1] * (r[1][0] * r[2][2] - r[1][2] * r[2][0])
        + r[0][2] * (r[1][0] * r[2][1] - r[1][1] * r[2][0])
    )


def validate_rigid_transform(value: Any, field: str, tolerance: float = 1e-6) -> dict[str, Any]:
    """Check a homogeneous proper rigid transform; this is not datum validation."""
    matrix = _matrix4(value, field)
    if any(abs(matrix[3][i] - expected) > tolerance for i, expected in enumerate((0, 0, 0, 1))):
        raise ValueError(f"P9M3_INVALID_HOMOGENEOUS_LAST_ROW:{field}")
    rotation = [[matrix[i][j] for j in range(3)] for i in range(3)]
    gram = [[sum(rotation[k][i] * rotation[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    orthogonality_error = max(abs(gram[i][j] - (1.0 if i == j else 0.0)) for i in range(3) for j in range(3))
    determinant = _det3(rotation)
    if orthogonality_error > tolerance:
        raise ValueError(f"P9M3_NON_ORTHOGONAL_ROTATION:{field}:{orthogonality_error}")
    if abs(determinant - 1.0) > tolerance:
        raise ValueError(f"P9M3_NOT_PROPER_ROTATION:{field}:{determinant}")
    return {
        "valid_rigid_transform": True,
        "orthogonality_error": orthogonality_error,
        "rotation_determinant": determinant,
        "translation_m": [matrix[i][3] for i in range(3)],
        "interpretation": "CAD source transform only; no installation or datum claim",
    }


def _load_candidate_rows(candidate_payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    if candidate_payload.get("schema_version") != "cad-engineering-candidate-mapping.v1":
        raise ValueError("P9M3_UNSUPPORTED_CANDIDATE_SCHEMA")
    rows = candidate_payload.get("candidates")
    if not isinstance(rows, list):
        raise ValueError("P9M3_CANDIDATES_MISSING")
    return rows


def _load_nodes(assembly_payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    rows = assembly_payload.get("nodes")
    if not isinstance(rows, list):
        raise ValueError("P9M3_ASSEMBLY_NODES_MISSING")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("P9M3_INVALID_ASSEMBLY_NODE")
        path = row.get("source_node_path")
        if not isinstance(path, str) or not path:
            raise ValueError("P9M3_ASSEMBLY_NODE_PATH_MISSING")
        if path in result:
            raise ValueError(f"P9M3_DUPLICATE_ASSEMBLY_NODE_PATH:{path}")
        result[path] = dict(row)
    return result


def _evidence_requirements(manifest: Mapping[str, Any], procedure_extraction: Mapping[str, Any] | None = None) -> dict[str, Any]:
    procedure = manifest.get("assembly_procedure", {})
    coordinate = manifest.get("coordinate_reference", {})
    extraction_source = procedure_extraction.get("source", {}) if isinstance(procedure_extraction, Mapping) else {}
    extraction = procedure_extraction.get("extraction", {}) if isinstance(procedure_extraction, Mapping) else {}
    return {
        "assembly_procedure_source": {
            "repository": procedure.get("repository"),
            "path": procedure.get("path"),
            "source_commit": procedure.get("source_commit"),
            "git_blob_sha1": procedure.get("git_blob_sha1"),
            "size_bytes": procedure.get("size_bytes"),
            "extracted_source_sha256": extraction_source.get("sha256"),
            "extracted_paragraph_count": extraction.get("paragraph_count"),
            "extracted_table_count": extraction.get("table_count"),
            "source_status": (
                "retrieved_and_hash_verified_raw_text_not_human_reviewed"
                if extraction_source.get("verification_status") == "source_bytes_verified"
                else "pinned_in_manifest_not_retrieved_or_independently_reviewed"
            ),
        },
        "coordinate_reference": {
            "title": coordinate.get("title"),
            "url": coordinate.get("url"),
            "page": coordinate.get("page"),
            "role": coordinate.get("role"),
            "source_status": "orientation_reference_only_not_complete_transform_evidence",
        },
        "required_evidence_groups": {
            group: list(types) for group, types in REQUIRED_EVIDENCE.items()
        },
    }


def build_installation_review_packets(
    candidate_payload: Mapping[str, Any],
    assembly_payload: Mapping[str, Any],
    source_manifest: Mapping[str, Any],
    procedure_extraction: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create review packets. Missing evidence remains missing; no auto-approval."""
    candidates = _load_candidate_rows(candidate_payload)
    nodes = _load_nodes(assembly_payload)
    source_commit = source_manifest.get("source_commit")
    source_tree_sha = source_manifest.get("source_tree_sha")
    if not source_commit or not source_tree_sha:
        raise ValueError("P9M3_CAD_SOURCE_PIN_MISSING")
    if procedure_extraction is not None:
        extracted_source = procedure_extraction.get("source", {})
        expected_procedure = source_manifest.get("assembly_procedure", {})
        if extracted_source.get("verification_status") != "source_bytes_verified":
            raise ValueError("P9M3_PROCEDURE_SOURCE_NOT_VERIFIED")
        if extracted_source.get("source_commit") != expected_procedure.get("source_commit"):
            raise ValueError("P9M3_PROCEDURE_COMMIT_MISMATCH")
        if extracted_source.get("git_blob_sha1") != expected_procedure.get("git_blob_sha1"):
            raise ValueError("P9M3_PROCEDURE_GIT_BLOB_MISMATCH")
        if extracted_source.get("size_bytes") != expected_procedure.get("size_bytes"):
            raise ValueError("P9M3_PROCEDURE_SIZE_MISMATCH")

    keyword_index = (
        procedure_extraction.get("keyword_index", {})
        if isinstance(procedure_extraction, Mapping) else {}
    )
    general_procedure_terms = ["structural testing model", "axis definition"]
    packets = []
    for candidate in candidates:
        path = candidate.get("cad_node_path")
        node = nodes.get(path)
        if node is None:
            raise ValueError(f"P9M3_CANDIDATE_NODE_NOT_FOUND:{path}")
        parent_path = node.get("parent_node_path")
        parent = nodes.get(parent_path) if parent_path else None
        local = validate_rigid_transform(node.get("local_transform_m"), f"{path}.local")
        world = validate_rigid_transform(node.get("world_transform_m"), f"{path}.world")
        parent_consistency = "root"
        if parent is not None:
            validate_rigid_transform(parent.get("world_transform_m"), f"{parent_path}.world")
            composed = _matmul(_matrix4(parent.get("world_transform_m"), f"{parent_path}.world"),
                               _matrix4(node.get("local_transform_m"), f"{path}.local"))
            declared_world = _matrix4(node.get("world_transform_m"), f"{path}.world")
            error = max(abs(composed[i][j] - declared_world[i][j]) for i in range(4) for j in range(4))
            parent_consistency = "pass" if error <= 1e-6 else "fail"
            if parent_consistency == "fail":
                raise ValueError(f"P9M3_PARENT_TRANSFORM_COMPOSITION_MISMATCH:{path}:{error}")
        evidence_hash = _sha256_json({
            "candidate_id": candidate.get("candidate_id"),
            "source_commit": source_commit,
            "source_tree_sha": source_tree_sha,
            "node": node,
        })
        candidate_terms = {
            "component.battery": ["battery"],
            "component.reaction_wheel": ["reaction wheel"],
            "component.payload": ["perovskite solar cells", "-y panel"],
        }.get(str(candidate.get("engineering_object_id")), [])
        procedure_locators = {
            term: keyword_index.get(term, [])
            for term in [*general_procedure_terms, *candidate_terms]
        }
        packets.append({
            "review_packet_id": hashlib.sha256(
                f"{candidate.get('candidate_id')}\0{source_commit}\0{source_tree_sha}".encode("utf-8")
            ).hexdigest()[:24],
            "candidate_id": candidate.get("candidate_id"),
            "engineering_object_id": candidate.get("engineering_object_id"),
            "engineering_object_name": candidate.get("engineering_object_name"),
            "cad_source": {
                "model": candidate.get("cad_source_model"),
                "revision": candidate.get("cad_source_revision"),
                "upstream_repository": source_manifest.get("source_repository"),
                "upstream_commit": source_commit,
                "source_tree_sha": source_tree_sha,
                "source_node_path": path,
                "source_node_name": node.get("source_node_name"),
                "source_label_name": node.get("source_label_name"),
                "parent_node_path": parent_path,
                "duplicate_occurrence": node.get("duplicate_occurrence"),
                "direct_shape_count": node.get("direct_shape_count"),
                "child_count": node.get("child_count"),
                "evidence_sha256": evidence_hash,
            },
            "cad_geometry_transform": {
                "local_transform_m": node.get("local_transform_m"),
                "world_transform_m": node.get("world_transform_m"),
                "local_transform_check": local,
                "world_transform_check": world,
                "parent_composition_check": parent_consistency,
                "coordinate_frame_identity": "unverified",
                "datum_origin": "unverified",
                "axis_convention": "orientation_reference_only",
                "handedness": "unverified",
                "units": "metre_after_source_scale_conversion",
                "transform_semantics": "source_CAD_geometry_transform_not_as_built_installation_transform",
            },
            "flight_unit_identity": {
                "candidate_variant_tags": candidate.get("variant_tags", []),
                "source_contains_mixed_em_or_b3_nodes": candidate.get("source_model_identity_review_required", False),
                "target_flight_unit": None,
                "target_flight_unit_status": "unresolved",
                "fm_em_revision_applicability": "unresolved",
            },
            "procedure_text_evidence_locators": {
                "status": "raw_text_source_locators_only_not_claim_approval",
                "references": procedure_locators,
            },
            "installation_evidence": {
                "assembly_procedure": "pinned_and_extracted_but_not_human_reviewed",
                "approved_drawing": "missing",
                "bom_or_configuration_list": "missing",
                "datum_and_axis_drawing": "missing",
                "signed_installation_transform": None,
                "reviewer_decision": "pending",
            },
            "required_reviews": {
                "flight_unit_identity": "blocked",
                "hardware_identity": "blocked",
                "assembly_relation": "blocked",
                "coordinate_datum": "blocked",
                "installation_transform": "blocked",
            },
            "promotion": {
                "status": "blocked_evidence_incomplete",
                "instance_id": None,
                "installation_transform": None,
                "spatial_anchor": None,
                "render_eligible": False,
                "allowed_next_stage": "P9-M3 evidence collection and independent review",
            },
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "cad_model": candidate_payload.get("source", {}).get("cad_model"),
            "cad_revision": candidate_payload.get("source", {}).get("cad_revision"),
            "source_commit": source_commit,
            "source_tree_sha": source_tree_sha,
            "cad_node_count": len(nodes),
            "candidate_count": len(packets),
        },
        "semantics": {
            "cad_transform_is_not_installation_transform": True,
            "coordinate_reference_does_not_define_numeric_origin": True,
            "no_as_built_truth_inferred": True,
            "no_instance_or_spatial_anchor_created": True,
            "manual_evidence_review_required": True,
            "unreviewed_evidence_fails_closed": True,
        },
        "evidence_catalog": _evidence_requirements(source_manifest, procedure_extraction),
        "summary": {
            "review_packet_count": len(packets),
            "cad_local_transform_valid_count": sum(1 for p in packets if p["cad_geometry_transform"]["local_transform_check"]["valid_rigid_transform"]),
            "cad_world_transform_valid_count": sum(1 for p in packets if p["cad_geometry_transform"]["world_transform_check"]["valid_rigid_transform"]),
            "parent_composition_pass_count": sum(1 for p in packets if p["cad_geometry_transform"]["parent_composition_check"] in ("pass", "root")),
            "identity_review_required_count": sum(1 for p in packets if p["flight_unit_identity"]["source_contains_mixed_em_or_b3_nodes"]),
            "blocked_evidence_count": len(packets),
            "promoted_instance_count": 0,
            "render_eligible_count": 0,
        },
        "review_packets": packets,
    }
