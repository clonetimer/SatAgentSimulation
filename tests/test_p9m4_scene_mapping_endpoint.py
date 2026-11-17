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
        "mapping_kind": "unlocated",
        "as_built_eligible": False,
        "render_views": [],
    }]
    assert "evidence" not in json.dumps(payload)
    assert "geometry_asset" not in json.dumps(payload)


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
    assert 'id="p9m4MappingStatus"' in html
    assert 'id="p9m4MappingDetail"' in html
    assert "api('/workbench/engineering-mapping')" in app
    assert "renderP9M4MappingPanel" in app
    assert "glb_node_binding" not in app
    assert ".p9m4-mapping-section" in css
