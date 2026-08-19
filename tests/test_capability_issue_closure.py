from __future__ import annotations

import io
import urllib.error
from pathlib import Path

from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.form_schema import capability_form_schema
from sat_sim.model_providers import ModelProviderProfile, ModelProviderRegistry
from sat_sim.run_bundle import _plot_manifest_payload


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=False,
        auth_mode="disabled",
    ))


def test_output_catalog_uses_chinese_titles_and_excludes_metadata_curves() -> None:
    schema = capability_form_schema("subsystem.adcs_fidelity.v1")
    outputs = schema["outputs"]
    assert outputs["time_axis"]["name"] == "time_s"
    assert set(outputs["metadata_fields"]) == {"task_id", "case_id", "sample_index", "utc"}
    names = {item["name"] for item in outputs["trace_options"]}
    assert not names.intersection({"time_s", "t_s", "times", "sample_index", "task_id", "case_id", "utc"})
    for item in outputs["trace_options"]:
        assert item["label"]
        assert item["label"] != item["name"]
        assert "_" not in item["label"] and "." not in item["label"]


def test_plot_manifest_strictly_obeys_requested_vertical_series() -> None:
    rows = [
        {"time_s": 0.0, "sample_index": 0, "task_id": "t1", "case_id": "case_000", "a": 1.0, "b": 2.0},
        {"time_s": 1.0, "sample_index": 1, "task_id": "t1", "case_id": "case_000", "a": 1.5, "b": 2.5},
    ]
    manifest = _plot_manifest_payload({"outputs": {"plots": ["time_s", "a"]}}, rows)
    assert manifest["selection_policy"] == "strict_requested_plots"
    assert manifest["time_axis"] == {"field": "time_s", "label": "仿真时间", "unit": "s"}
    assert [item["field"] for item in manifest["series"]] == ["a"]
    assert manifest["series"][0]["scope_label"] == "分系统"
    assert _plot_manifest_payload({"outputs": {"plots": []}}, rows)["series"] == []


def test_plot_manifest_expands_requested_wildcards_but_not_metadata() -> None:
    rows = [{"time_s": 0.0, "gyro_true_x": 0.1, "gyro_measured_x": 0.11, "sample_index": 0}]
    manifest = _plot_manifest_payload({"outputs": {"plots": ["gyro_*"]}}, rows)
    assert [item["field"] for item in manifest["series"]] == ["gyro_true_x", "gyro_measured_x"]


def test_workbench_uses_light_sections_and_manifest_only_chart_controls(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        index = client.get("/").text
        js = client.get("/assets/app.js").text
        css = client.get("/assets/styles.css").text
    assert "task_id 标识可保存、可复用的任务配置" in index
    assert "横轴固定为仿真时间（time_s）" in index
    assert 'html[data-theme="light"] .schema-section' in css
    assert "rgba(252, 254, 255, .98)" in css
    assert "strict_requested_plots" not in js  # policy comes from the manifest, not a UI fallback
    assert "(state.plotManifest?.series || []).map" in js
    assert "output-hierarchy-toolbar" in js
    assert "输出层级" in index
    assert "[...preferred, ...available]" not in js
    assert "sample_index', 'task_id', 'case_id'" in js


def test_lmstudio_probe_retries_reasoning_only_response_with_expanded_budget(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_http_json(url: str, **kwargs):
        body = dict(kwargs.get("body") or {})
        calls.append(body)
        if len(calls) == 1:
            assert body["response_format"]["type"] == "json_schema"
            raise urllib.error.HTTPError(
                url, 400, "structured output unsupported", {},
                io.BytesIO(b'{"error":"unsupported response_format"}'),
            )
        if len(calls) == 2:
            return 200, {
                "choices": [{
                    "message": {"content": "", "reasoning_content": "Thinking Process..."},
                    "finish_reason": "length",
                }],
                "usage": {"completion_tokens": 512, "completion_tokens_details": {"reasoning_tokens": 512}},
            }, 10.0
        assert body["max_tokens"] == 1536
        return 200, {
            "choices": [{"message": {"content": '{"status":"ok","purpose":"sat-sim-provider-probe"}'}, "finish_reason": "stop"}],
            "usage": {"completion_tokens": 16},
        }, 11.0

    monkeypatch.setattr("sat_sim.model_providers._http_json", fake_http_json)
    registry = ModelProviderRegistry(profiles=[ModelProviderProfile(
        provider_id="local-lmstudio",
        label="本地 LM Studio",
        backend="openai_compatible",
        location="local",
        tier="L2",
        model="qwen/qwen3.5-9b",
        base_url="http://127.0.0.1:1234/v1",
        structured_output="json_schema",
    )])
    result = registry.probe_generation("local-lmstudio", timeout_s=20.0)
    assert result["ok"] is True
    assert "expanded_budget" in result["structured_output_transport"]
    assert len(calls) == 3
    assert "response_format" not in calls[1]


def test_saved_lmstudio_profile_uses_json_schema_and_context_safe_output_budget(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        saved = client.post("/models/local-services", json={
            "service_type": "lmstudio",
            "model": "qwen/qwen3.5-9b",
            "base_url": "http://127.0.0.1:1234/v1",
        })
        assert saved.status_code == 200
        providers = client.get("/models/providers").json()["catalog"]["providers"]
    profile = next(item for item in providers if item["provider_id"] == "local-lmstudio")
    assert profile["structured_output"] == "json_schema"
    assert profile["max_output_tokens"] == 2048
