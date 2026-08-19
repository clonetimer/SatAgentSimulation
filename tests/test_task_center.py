from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from fastapi.testclient import TestClient

from sat_sim.api import API_VERSION, WEB_WORKBENCH_VERSION, create_app
from sat_sim.task_center import TASK_CENTER_SCHEMA_VERSION, TaskCenterStore


class _ModelHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def _json(self, payload: dict, status: int = 200):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/v1/models":
            self._json({"data": [{"id": "qwen-test"}, {"id": "qwen-small"}]})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        request_body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/v1/chat/completions":
            messages = request_body.get("messages") or []
            system_text = " ".join(str(item.get("content") or "") for item in messages if isinstance(item, dict) and item.get("role") == "system")
            content = json.dumps(_sample_spec(), ensure_ascii=False) if "TaskSpec" in system_text else '{"status":"ok","purpose":"sat-sim-provider-probe"}'
            self._json({
                "id": "probe-1",
                "choices": [{"message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 8},
            })
        else:
            self._json({"error": "not found"}, 404)


def _model_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ModelHandler)
    server.daemon_threads = True
    server.block_on_close = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}/v1"


def _sample_spec(name: str = "版本任务", duration: float = 10.0):
    return {
        "schema_version": "1.0",
        "task": {"id": "task_versioned", "name": name},
        "simulation": {"duration_s": duration, "sample_s": 1.0, "level": "component"},
        "model": {"capability_id": "component.reaction_wheel.v1", "target": {"mode": "nominal"}},
        "parameters": {"profile": "demo", "values": {}},
        "events": {"faults": [], "degradations": []},
        "outputs": {"qoi": ["qoi.adcs.reaction_wheel.final_speed_rad_s_0"], "plots": ["adcs.reaction_wheel.speed_rad_s_0"], "output_root": "datasets/task_versioned"},
        "assurance": {"parameter_profile": "demo"},
    }


def test_taskcenter_local_model_discovery_and_structured_probe(tmp_path: Path):
    server, base_url = _model_server()
    try:
        app = create_app(runs_root=tmp_path / "runs", artifacts_root=tmp_path / "artifacts", embedded_worker=False, auth_mode="disabled")
        with TestClient(app) as client:
            saved = client.post("/models/local-services", json={"service_type": "vllm", "model": "qwen-test", "base_url": base_url})
            assert saved.status_code == 200
            provider_id = saved.json()["provider_id"]
            discovery = client.get(f"/models/providers/{provider_id}/models").json()["discovery"]
            assert discovery["ok"] is True
            assert discovery["models"] == ["qwen-small", "qwen-test"]
            assert discovery["configured_model_found"] is True
            probe = client.post(f"/models/providers/{provider_id}/probe", json={"include_taskspec_probe": True}).json()
            assert probe["ok"] is True
            assert probe["inference_probe"]["json_output"] is True
            assert probe["inference_probe"]["taskspec_draft_capable"] is True
            assert probe["taskspec_probe"]["ok"] is True
            assert probe["taskspec_probe"]["fallback_detected"] is False
            assert probe["secret_values_exposed"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_taskcenter_task_center_versions_clone_baseline_and_run_history(tmp_path: Path):
    store = TaskCenterStore(tmp_path / "tasks.sqlite3")
    first = store.save(_sample_spec(), task_id="task_versioned", status="READY")
    assert first.current_version == 1
    second = store.save(_sample_spec(duration=20.0), task_id="task_versioned", status="READY", change_summary="延长时长")
    assert second.current_version == 2
    assert second.version_count == 2
    same = store.save(_sample_spec(duration=20.0), task_id="task_versioned", status="READY")
    assert same.current_version == 2
    baseline = store.set_baseline("task_versioned", 1)
    assert baseline and baseline.baseline_version == 1
    restored = store.restore_version("task_versioned", 1)
    assert restored and restored.current_version == 3
    assert restored.task_spec["simulation"]["duration_s"] == 10.0
    cloned = store.clone("task_versioned")
    assert cloned and cloned.task_id != "task_versioned" and cloned.current_version == 1
    store.mark_run("task_versioned", run_id="run-1", run_status="QUEUED")
    store.mark_run_by_run_id("run-1", run_status="SUCCEEDED", validation_result="PASS")
    runs = store.list_runs("task_versioned")
    assert len(runs) == 1
    assert runs[0].task_version == 3
    assert runs[0].status == "SUCCEEDED"
    assert store.health()["schema_version"] == TASK_CENTER_SCHEMA_VERSION


def test_taskcenter_task_center_version_api_and_assets(tmp_path: Path):
    app = create_app(runs_root=tmp_path / "runs", artifacts_root=tmp_path / "artifacts", embedded_worker=False, auth_mode="disabled")
    with TestClient(app) as client:
        assert client.get("/health").json()["api_version"] == API_VERSION
        saved = client.post("/task-center/tasks", json={"task_spec": _sample_spec(), "task_id": "task_versioned", "status": "READY"})
        assert saved.status_code == 200
        updated = client.put("/task-center/tasks/task_versioned", json={"task_spec": _sample_spec(duration=30.0), "status": "READY", "change_summary": "30秒"})
        assert updated.json()["task"]["current_version"] == 2
        versions = client.get("/task-center/tasks/task_versioned/versions").json()
        assert versions["count"] == 2
        baseline = client.post("/task-center/tasks/task_versioned/versions/1/baseline")
        assert baseline.status_code == 200
        restored = client.post("/task-center/tasks/task_versioned/versions/1/restore", json={"change_summary": "回退"})
        assert restored.json()["task"]["current_version"] == 3
        clone = client.post("/task-center/tasks/task_versioned/clone", json={"name": "克隆任务"})
        assert clone.status_code == 200
        html = client.get("/").text
        js = client.get("/assets/app.js").text
    assert WEB_WORKBENCH_VERSION == "sidebar-observability-workbench.v1"
    assert "自动发现模型" in html
    assert "任务版本与运行历史" in html
    assert "/models/local-services/discover" in js
    assert "/versions" in js and "/clone" in js
