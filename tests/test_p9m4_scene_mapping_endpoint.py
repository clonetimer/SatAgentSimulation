from __future__ import annotations

import json

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
    with _client(tmp_path / "app") as client:
        payload = client.get("/workbench/engineering-mapping").json()
    assert payload["ok"] is False
    assert payload["status"] == "invalid"
    assert str(path) not in json.dumps(payload)
    assert payload["mappings"] == []


def test_engineering_mapping_endpoint_returns_only_validated_public_projection(tmp_path, monkeypatch):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({
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
    }), encoding="utf-8")
    monkeypatch.setenv("SAT_SIM_ENGINEERING_MAPPING_BUNDLE", str(path))
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
