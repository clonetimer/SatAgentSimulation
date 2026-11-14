from __future__ import annotations

from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.form_schema import capability_form_schema


def test_scene_configuration_catalog_is_real_and_read_only(tmp_path):
    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=False,
        auth_mode="disabled",
    )
    with TestClient(app) as client:
        before = client.get("/health").json()
        response = client.get("/workbench/scene/catalog")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["ok"] is True
        tree = payload["tree"]
        assert tree["schema_version"] == "workbench-object-tree.v2"
        assert tree["tree"]["object_id"] == "whole_spacecraft"
        adcs = next(row for row in tree["tree"]["children"] if row["object_id"] == "subsystem.adcs")
        wheel = next(row for row in adcs["children"] if row["object_id"] == "component.reaction_wheel")
        parameters = next(row for row in wheel["children"] if row["kind"] == "parameter_group")
        inertia = next(row for row in parameters["children"] if row["source_field"] == "wheel_inertia_kg_m2")
        assert inertia["unit"] == "kg·m²"
        assert inertia["minimum"] is not None
        schema = capability_form_schema("component.reaction_wheel.v1")
        source_field = next(row for row in schema["fields"] if row["path"].endswith(".wheel_inertia_kg_m2"))
        assert source_field["default"][inertia["array_index"]] == inertia["default"]
        assert source_field["unit"] == inertia["unit"]
        after = client.get("/health").json()
        assert before["features"] == after["features"]


def test_p9d_ui_uses_existing_task_spec_validation_and_no_fake_anchor():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "sat_sim" / "web"
    index = (root / "index.html").read_text(encoding="utf-8")
    app = (root / "app.js").read_text(encoding="utf-8")
    panel = (root / "object-config-panel.js").read_text(encoding="utf-8")
    assert 'id="p9dObjectTree"' in index
    assert 'id="p9dParameterHost"' in index
    assert "api('/workbench/scene/catalog')" in app
    assert "previewForm()" in app
    assert "api('/tasks/parse'" in app
    assert "state.formData" in app
    assert "hasSpatialAnchor" in app
    assert "spatial_anchor" in panel or "spatial_anchor" in app
    assert "window.P9DObjectConfigPanel" in app
