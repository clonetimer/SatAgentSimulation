from __future__ import annotations

import io
import json
from pathlib import Path
import zipfile

from fastapi.testclient import TestClient

from sat_sim.api import API_VERSION, WEB_WORKBENCH_VERSION, create_app
from sat_sim.capability_registry import active_capability_ids
from sat_sim.workbench_catalog import workbench_presentation_catalog


def _client(tmp_path: Path) -> TestClient:
    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=False,
        auth_mode="disabled",
    )
    return TestClient(app)


def test_presentation_catalog_has_one_whole_six_subsystems_and_24_components():
    payload = workbench_presentation_catalog()
    assert payload["visible_counts"] == {
        "whole_spacecraft": 1,
        "subsystem": 6,
        "component": 24,
        "orbit_environment": 1,
    }
    objects = payload["objects"]
    assert len([item for item in objects if item["level"] == "whole_spacecraft"]) == 1
    assert len([item for item in objects if item["level"] == "subsystem"]) == 6
    components = [item for item in objects if item["level"] == "component"]
    assert len(components) == 24
    assert payload["component_execution_summary"] == {
        "total": 24,
        "independently_executable": 24,
        "agent_exposed_independent": 24,
        "internal_independent": 0,
        "integration_only": 0,
    }
    assert {item["name_zh"] for item in components} >= {"蓄电池", "反作用轮", "推力器", "星敏感器", "发射机"}
    whole = next(item for item in objects if item["level"] == "whole_spacecraft")
    assert len(whole["variants"]) == 6
    assert whole["primary_capability_id"] == "whole_spacecraft.unified_native.v1"


def test_workbench_assets_fix_light_input_colors_and_add_task_center(tmp_path: Path):
    with _client(tmp_path) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["api_version"] == API_VERSION
        assert health.json()["workbench_version"] == WEB_WORKBENCH_VERSION
        index = client.get("/").text
        css = client.get("/assets/styles.css").text
        js = client.get("/assets/app.js").text
    assert 'data-view="tasks"' in index
    assert 'id="taskCenterView"' in index
    assert 'id="modelSettingsModal"' in index
    assert "Ollama" in index and "LM Studio" in index and "vLLM" in index
    assert "--input-bg: #ffffff" in css
    assert "-webkit-text-fill-color: var(--input-text)" in css
    assert "background: var(--input-bg)" in css
    assert "counts.component || 24" in js
    assert "/task-center/tasks" in js
    assert "/models/local-services" in js


def test_run_dataset_can_be_inspected_and_exported_as_zip(tmp_path: Path):
    dataset_root = tmp_path / "runs" / "run-dataset" / "results" / "dataset"
    labels_root = dataset_root / "labels"
    labels_root.mkdir(parents=True)
    manifest = {
        "manifest_version": "sat.dataset.v1",
        "dataset_id": "task-dataset",
        "status": "complete",
        "quality": {"trace_rows": 2},
    }
    (dataset_root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (dataset_root / "trace.csv").write_text("time_s,value\n0,1\n1,2\n", encoding="utf-8")
    (labels_root / "run_labels.json").write_text('{"status":"complete"}', encoding="utf-8")
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("must-not-export", encoding="utf-8")
    (dataset_root / "outside-link.txt").symlink_to(outside)

    with _client(tmp_path) as client:
        description = client.get("/runs/run-dataset/dataset")
        assert description.status_code == 200
        dataset = description.json()["dataset"]
        assert dataset["available"] is True
        assert dataset["file_count"] == 3
        assert dataset["manifest"] == manifest
        assert dataset["formats"] == ["csv", "json"]
        assert dataset["download_url"] == "/runs/run-dataset/dataset/download"

        download = client.get("/runs/run-dataset/dataset/download")
        assert download.status_code == 200
        assert download.headers["content-type"] == "application/zip"
        assert "run-dataset_dataset.zip" in download.headers["content-disposition"]
        with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
            assert archive.namelist() == [
                "dataset/labels/run_labels.json",
                "dataset/manifest.json",
                "dataset/trace.csv",
            ]
            assert archive.read("dataset/trace.csv").decode("utf-8").startswith("time_s,value")
            assert "must-not-export" not in b"".join(archive.read(name) for name in archive.namelist()).decode("utf-8")


def test_run_dataset_export_reports_missing_dataset(tmp_path: Path):
    (tmp_path / "runs" / "run-without-dataset").mkdir(parents=True)
    with _client(tmp_path) as client:
        response = client.get("/runs/run-without-dataset/dataset/download")
    assert response.status_code == 404
    assert response.json()["detail"]["reason_code"] == "DATASET_NOT_FOUND"


def test_workbench_exposes_dataset_export_control(tmp_path: Path):
    with _client(tmp_path) as client:
        index = client.get("/").text
        js = client.get("/assets/app.js").text
    assert 'id="exportDatasetBtn"' in index
    assert "导出数据集" in index
    assert "/dataset/download" in js
    assert "exportActiveDataset" in js


def test_workbench_exposes_font_scaling_and_run_result_focus_modes(tmp_path: Path):
    with _client(tmp_path) as client:
        index = client.get("/").text
        css = client.get("/assets/styles.css").text
        js = client.get("/assets/app.js").text
    for control_id in ("fontScaleSelect", "creatorRunPanel", "expandRunPanelBtn", "fullscreenRunPanelBtn"):
        assert f'id="{control_id}"' in index
    assert "特大（130%）" in index
    assert "--ui-scale" in css
    assert ".workspace.run-results-expanded" in css
    assert ".run-panel:fullscreen" in css
    assert "sat-sim-font-scale" in js
    assert "toggleRunResultsExpanded" in js
    assert "toggleRunPanelFullscreen" in js
    assert "fullscreenchange" in js


def test_forms_endpoint_preserves_registry_catalog_and_adds_presentation(tmp_path: Path):
    with _client(tmp_path) as client:
        response = client.get("/forms/capabilities")
    assert response.status_code == 200
    catalog = response.json()["catalog"]
    expected_active_count = len(active_capability_ids())
    assert catalog["count"] == expected_active_count
    assert len(catalog["capabilities"]) == expected_active_count
    assert catalog["presentation"]["visible_counts"]["component"] == 24
    assert catalog["presentation"]["active_agent_capability_contracts"] == expected_active_count


def test_local_ollama_lmstudio_vllm_configuration(tmp_path: Path):
    with _client(tmp_path) as client:
        before = client.get("/models/providers").json()["catalog"]["providers"]
        ids = {item["provider_id"] for item in before}
        assert {"local-ollama", "local-lmstudio", "local-vllm"} <= ids
        saved = client.post(
            "/models/local-services",
            json={
                "service_type": "ollama",
                "model": "qwen3:8b",
                "base_url": "http://127.0.0.1:11434/v1",
            },
        )
        assert saved.status_code == 200
        assert saved.json()["provider_id"] == "local-ollama"
        assert saved.json()["api_key_persisted"] is False
        catalog = client.get("/models/providers").json()["catalog"]["providers"]
        ollama = next(item for item in catalog if item["provider_id"] == "local-ollama")
        assert ollama["model"] == "qwen3:8b"
        assert ollama["base_url"] == "http://127.0.0.1:11434/v1"
        assert ollama["readiness"]["ready"] is True


def test_task_center_persists_created_simulations(tmp_path: Path):
    with _client(tmp_path) as client:
        schema = client.get(
            "/forms/capabilities/whole_spacecraft.composite_digital_twin.v1"
        ).json()["form_schema"]
        parsed = client.post(
            "/tasks/parse",
            json={"input_kind": "form", "form_data": schema["default_form"], "compile_if_valid": True},
        )
        assert parsed.status_code == 200
        assert parsed.json()["ok"] is True
        task_spec = parsed.json()["result"]["task_spec"]
        saved = client.post(
            "/task-center/tasks",
            json={"task_spec": task_spec, "status": "READY", "source": "test"},
        )
        assert saved.status_code == 200
        task_id = saved.json()["task"]["task_id"]
        listed = client.get("/task-center/tasks").json()
        assert listed["total"] == 1
        assert listed["tasks"][0]["task_id"] == task_id
        opened = client.get(f"/task-center/tasks/{task_id}")
        assert opened.status_code == 200
        assert opened.json()["task"]["task_spec"]["model"]["capability_id"] == "whole_spacecraft.composite_digital_twin.v1"
        deleted = client.delete(f"/task-center/tasks/{task_id}")
        assert deleted.status_code == 200
        assert client.get("/task-center/tasks").json()["total"] == 0
