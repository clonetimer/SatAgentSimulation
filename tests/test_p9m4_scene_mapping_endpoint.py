from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from sat_sim.api import create_app


def _client(tmp_path):
    return TestClient(create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=False,
        auth_mode="disabled",
    ))


def test_engineering_mapping_endpoint_is_explicitly_unconfigured_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("SAT_SIM_ENGINEERING_MAPPING_BUNDLE", raising=False)
    monkeypatch.delenv("SAT_SIM_ENGINEERING_MAPPING_BUNDLE_SHA256", raising=False)
    with _client(tmp_path) as client:
        response = client.get("/workbench/engineering-mapping")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["configured"] is False
    assert payload["status"] == "not_configured"
    assert payload["mappings"] == []
    assert payload["summary"] is None


def test_engineering_mapping_endpoint_rejects_invalid_bundle_without_leaking_path(tmp_path, monkeypatch):
    path = tmp_path / "bad-registry.json"
    path.write_text(json.dumps({"schema_version": "wrong", "mappings": []}), encoding="utf-8")
    monkeypatch.setenv("SAT_SIM_ENGINEERING_MAPPING_BUNDLE", str(path))
    monkeypatch.setenv("SAT_SIM_ENGINEERING_MAPPING_BUNDLE_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    with _client(tmp_path / "app") as client:
        payload = client.get("/workbench/engineering-mapping").json()
    assert payload["ok"] is False
    assert payload["status"] == "invalid"
    assert str(path) not in json.dumps(payload)
    assert payload["mappings"] == []


def test_engineering_mapping_endpoint_returns_only_validated_public_projection(tmp_path, monkeypatch):
    path = tmp_path / "registry.json"
    bundle = {
        "schema_version": "engineering-asset-mapping.v1",
        "mappings": [{
            "mapping_id": "unlocated:component.imu",
            "object_id": "component.imu",
            "mapping_kind": "unlocated",
            "asset_instance_id": None,
            "geometry_asset": None,
            "geometry_frame": None,
            "spatial_anchor": None,
            "telemetry_binding": None,
            "evidence": [],
            "render": {"as_built_eligible": False, "renderable": False, "views": []},
        }],
        "intake": {"scope": "reference_registration_only_not_as_built_approval"},
        "summary": {"as_built_verified_count": 0, "telemetry_binding_count": 0, "node_renderable_count": 0},
    }
    path.write_text(json.dumps(bundle), encoding="utf-8")
    monkeypatch.setenv("SAT_SIM_ENGINEERING_MAPPING_BUNDLE", str(path))
    monkeypatch.setenv("SAT_SIM_ENGINEERING_MAPPING_BUNDLE_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    with _client(tmp_path / "app") as client:
        payload = client.get("/workbench/engineering-mapping").json()
    assert payload["ok"] is True
    assert payload["status"] == "validated"
    assert payload["summary"]["counts_by_kind"] == {"unlocated": 1}
    assert payload["summary"]["as_built_verified_count"] == 0
    assert payload["mappings"] == [{
        "object_id": "component.imu",
        "name_zh": "component.imu",
        "mapping_kind": "unlocated",
        "as_built_eligible": False,
        "render_views": [],
    }]
    assert "evidence" not in json.dumps(payload)
    assert "geometry_asset" not in json.dumps(payload)


def _reference_bundle(candidate=None):
    candidate = candidate or {
        "status": "unique_exact_name_parent_match_group_candidate",
        "candidate_glb_node_index": 545,
        "candidate_glb_node_name": "battery",
        "candidate_glb_mesh_index": None,
        "candidate_geometry_node_count": 2,
        "candidate_descendant_mesh_nodes": [
            {"node_index": 546, "name": "battery-case", "mesh_index": 475},
            {"node_index": 547, "name": "battery-cover", "mesh_index": 476},
        ],
        "selector_promoted": False,
        "requires_visual_review": True,
    }
    return {
        "schema_version": "engineering-asset-mapping.v1",
        "mappings": [{
            "mapping_id": "reference:battery",
            "object_id": "component.battery",
            "name_zh": "电池组",
            "mapping_kind": "reference_cad",
            "asset_instance_id": None,
            "geometry_asset": {
                "asset_id": "glb:" + "a" * 64,
                "sha256": "a" * 64,
                "source_node_path": "root/battery",
                "glb_node_binding": None,
                "glb_node_binding_candidate": candidate,
            },
            "geometry_frame": {"identity_status": "unverified"},
            "spatial_anchor": None,
            "telemetry_binding": None,
            "evidence": [],
            "render": {"as_built_eligible": False, "renderable": False, "views": ["reference_cad"]},
        }],
        "intake": {
            "scope": "reference_registration_only_not_as_built_approval",
            "glb_sha256": "a" * 64,
        },
        "glb_node_inventory": {"node_count": 852, "mesh_count": 477, "scene_count": 1},
        "summary": {
            "as_built_verified_count": 0,
            "telemetry_binding_count": 0,
            "node_renderable_count": 0,
        },
    }


def test_engineering_mapping_endpoint_projects_candidate_only_review_data(tmp_path, monkeypatch):
    path = tmp_path / "reference-registry.json"
    path.write_text(json.dumps(_reference_bundle()), encoding="utf-8")
    monkeypatch.setenv("SAT_SIM_ENGINEERING_MAPPING_BUNDLE", str(path))
    monkeypatch.setenv("SAT_SIM_ENGINEERING_MAPPING_BUNDLE_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    with _client(tmp_path / "app") as client:
        payload = client.get("/workbench/engineering-mapping").json()
    assert payload["ok"] is True
    assert payload["intake"]["glb_sha256"] == "a" * 64
    assert payload["glb_node_inventory"] == {"node_count": 852, "mesh_count": 477}
    candidate = payload["mappings"][0]["review_candidate"]
    assert candidate == {
        "status": "unique_exact_name_parent_match_group_candidate",
        "node_index": 545,
        "node_name": "battery",
        "mesh_index": None,
        "geometry_node_count": 2,
        "descendant_mesh_nodes": [
            {"node_index": 546, "name": "battery-case", "mesh_index": 475},
            {"node_index": 547, "name": "battery-cover", "mesh_index": 476},
        ],
        "selector_promoted": False,
        "requires_visual_review": True,
    }
    serialized = json.dumps(payload)
    assert "geometry_asset" not in serialized
    assert "glb_node_binding" not in serialized
    assert "installation_transform" not in serialized
    assert payload["summary"]["visual_review_candidate_count"] == 1


def test_engineering_mapping_endpoint_rejects_promoted_review_candidate(tmp_path, monkeypatch):
    path = tmp_path / "promoted-registry.json"
    candidate = _reference_bundle()["mappings"][0]["geometry_asset"]["glb_node_binding_candidate"]
    candidate["selector_promoted"] = True
    path.write_text(json.dumps(_reference_bundle(candidate)), encoding="utf-8")
    monkeypatch.setenv("SAT_SIM_ENGINEERING_MAPPING_BUNDLE", str(path))
    monkeypatch.setenv("SAT_SIM_ENGINEERING_MAPPING_BUNDLE_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    with _client(tmp_path / "app") as client:
        payload = client.get("/workbench/engineering-mapping").json()
    assert payload["ok"] is False
    assert payload["status"] == "invalid"
    assert payload["mappings"] == []


def test_engineering_mapping_endpoint_rejects_descendant_mesh_outside_glb_inventory(tmp_path, monkeypatch):
    path = tmp_path / "out-of-range-mesh-registry.json"
    bundle = _reference_bundle()
    bundle["mappings"][0]["geometry_asset"]["glb_node_binding_candidate"]["candidate_descendant_mesh_nodes"][0]["mesh_index"] = 477
    path.write_text(json.dumps(bundle), encoding="utf-8")
    monkeypatch.setenv("SAT_SIM_ENGINEERING_MAPPING_BUNDLE", str(path))
    monkeypatch.setenv("SAT_SIM_ENGINEERING_MAPPING_BUNDLE_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    with _client(tmp_path / "app") as client:
        payload = client.get("/workbench/engineering-mapping").json()
    assert payload["ok"] is False
    assert payload["status"] == "invalid"
    assert payload["mappings"] == []


def test_engineering_mapping_endpoint_rejects_reference_row_render_promotion(tmp_path, monkeypatch):
    path = tmp_path / "render-promoted-registry.json"
    bundle = _reference_bundle()
    bundle["mappings"][0]["render"]["renderable"] = True
    path.write_text(json.dumps(bundle), encoding="utf-8")
    monkeypatch.setenv("SAT_SIM_ENGINEERING_MAPPING_BUNDLE", str(path))
    monkeypatch.setenv("SAT_SIM_ENGINEERING_MAPPING_BUNDLE_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    with _client(tmp_path / "app") as client:
        payload = client.get("/workbench/engineering-mapping").json()
    assert payload["ok"] is False
    assert payload["status"] == "invalid"
    assert payload["mappings"] == []


def test_engineering_mapping_endpoint_rejects_registry_digest_mismatch(tmp_path, monkeypatch):
    path = tmp_path / "registry.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("SAT_SIM_ENGINEERING_MAPPING_BUNDLE", str(path))
    monkeypatch.setenv("SAT_SIM_ENGINEERING_MAPPING_BUNDLE_SHA256", "0" * 64)
    with _client(tmp_path / "app") as client:
        payload = client.get("/workbench/engineering-mapping").json()
    assert payload["ok"] is False
    assert payload["status"] == "invalid"
    assert payload["mappings"] == []


def test_satellite_scene_exposes_evidence_classes_without_fake_geometry():
    root = Path(__file__).resolve().parents[1] / "src" / "sat_sim" / "web"
    html = (root / "index.html").read_text(encoding="utf-8")
    app = (root / "app.js").read_text(encoding="utf-8")
    css = (root / "satellite-scene.css").read_text(encoding="utf-8")
    cad_viewer = (root / "birds-cad-viewer.js").read_text(encoding="utf-8")
    assert 'id="p9m4MappingStatus"' in html
    assert 'id="birdsCadReviewPanel"' in html
    assert "sha256Hex" in cad_viewer
    assert "loadedGlbSha256!==expected" in cad_viewer
    assert "selectByIndex" in cad_viewer
    assert "gltf.parser?.associations" in cad_viewer
    assert "item.userData.gltfNodeIndex===index" in cad_viewer
    assert "item.userData.cadTreeIndex===index" not in cad_viewer
    assert ".birds-cad-review-panel" in css
    assert ".birds-cad-review-checklist" in css
    assert "待工程人员目视核对" in cad_viewer
    assert "未批准；本审查不建立实装或遥测绑定" in cad_viewer
    assert 'id="p9m4MappingDetail"' in html
    assert "api('/workbench/engineering-mapping')" in app
    assert "renderP9M4MappingPanel" in app
    assert "glb_node_binding" not in app
    assert ".p9m4-mapping-section" in css
