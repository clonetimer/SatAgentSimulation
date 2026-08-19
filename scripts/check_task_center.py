#!/usr/bin/env python3
"""
用途：执行任务中心、本地模型诊断和任务生命周期审计。
参数：--output。
输出：生成任务中心审计 JSON。
"""
from __future__ import annotations

import argparse
import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from fastapi.testclient import TestClient

from sat_sim.api import create_app

SCHEMA_VERSION = "task-center-audit.v1"


def sample_spec(duration: float = 10.0) -> dict:
    return {
        "schema_version": "1.0",
        "task": {"id": "taskcenter_audit_task", "name": "任务中心审计任务"},
        "simulation": {"duration_s": duration, "sample_s": 1.0, "level": "component"},
        "model": {"capability_id": "component.reaction_wheel.v1", "target": {"mode": "nominal"}},
        "parameters": {"profile": "demo", "values": {}},
        "events": {"faults": [], "degradations": []},
        "outputs": {
            "qoi": ["qoi.adcs.reaction_wheel.final_speed_rad_s_0"],
            "plots": ["adcs.reaction_wheel.speed_rad_s_0"],
            "output_root": "datasets/taskcenter_audit_task",
        },
        "assurance": {"parameter_profile": "demo"},
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def send_json(self, payload: dict, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/v1/models":
            self.send_json({"data": [{"id": "qwen-audit"}, {"id": "qwen-small"}]})
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path != "/v1/chat/completions":
            self.send_json({"error": "not found"}, 404)
            return
        messages = body.get("messages") or []
        system = " ".join(str(item.get("content") or "") for item in messages if isinstance(item, dict) and item.get("role") == "system")
        content = json.dumps(sample_spec(), ensure_ascii=False) if "TaskSpec" in system else '{"status":"ok","purpose":"sat-sim-provider-probe"}'
        self.send_json({"choices": [{"message": {"role": "assistant", "content": content}}], "usage": {"prompt_tokens": 8, "completion_tokens": 8}})


def add(checks: list[dict], check_id: str, passed: bool, evidence) -> None:
    checks.append({"check_id": check_id, "status": "PASS" if passed else "FAIL", "evidence": evidence})


def run(output_path: str | Path) -> dict:
    checks: list[dict] = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}/v1"
    try:
        with tempfile.TemporaryDirectory(prefix="sat-sim-task-center-audit-") as tmp:
            root = Path(tmp)
            app = create_app(runs_root=root / "runs", artifacts_root=root / "artifacts", embedded_worker=False, auth_mode="disabled")
            with TestClient(app) as client:
                discovery = client.post("/models/local-services/discover", json={"service_type": "vllm", "base_url": base_url}).json()
                add(checks, "MODEL_DISCOVERY_OK", discovery.get("ok") is True, discovery)
                models = discovery.get("discovery", {}).get("models", [])
                add(checks, "MODEL_LIST_EXACT", models == ["qwen-audit", "qwen-small"], models)
                saved = client.post("/models/local-services", json={"service_type": "vllm", "model": "qwen-audit", "base_url": base_url}).json()
                add(checks, "LOCAL_PROVIDER_SAVED", saved.get("ok") is True, saved)
                add(checks, "API_KEY_NOT_PERSISTED", saved.get("api_key_persisted") is False, saved)
                probe = client.post("/models/providers/local-vllm/probe", json={"include_taskspec_probe": True, "timeout_s": 20}).json()
                add(checks, "STRUCTURED_PROBE_OK", probe.get("inference_probe", {}).get("json_output") is True, probe.get("inference_probe"))
                add(checks, "TASKSPEC_PROBE_OK", probe.get("taskspec_probe", {}).get("ok") is True, probe.get("taskspec_probe"))
                add(checks, "NO_FALLBACK_MASQUERADE", probe.get("taskspec_probe", {}).get("fallback_detected") is False, probe.get("taskspec_probe"))
                add(checks, "SECRETS_NOT_EXPOSED", probe.get("secret_values_exposed") is False, probe)

                first = client.post("/task-center/tasks", json={"task_spec": sample_spec(), "task_id": "taskcenter_audit_task", "status": "READY"}).json()["task"]
                add(checks, "TASK_CREATED_V1", first.get("current_version") == 1, first)
                second = client.put("/task-center/tasks/taskcenter_audit_task", json={"task_spec": sample_spec(20.0), "status": "READY", "change_summary": "延长到20秒"}).json()["task"]
                add(checks, "TASK_VERSION_INCREMENTED", second.get("current_version") == 2 and second.get("version_count") == 2, second)
                same = client.put("/task-center/tasks/taskcenter_audit_task", json={"task_spec": sample_spec(20.0), "status": "READY"}).json()["task"]
                add(checks, "IDENTICAL_SAVE_NO_DUPLICATE", same.get("current_version") == 2, same)
                versions = client.get("/task-center/tasks/taskcenter_audit_task/versions").json()
                add(checks, "VERSION_HISTORY_COUNT", versions.get("count") == 2, versions)
                baseline = client.post("/task-center/tasks/taskcenter_audit_task/versions/1/baseline").json()["task"]
                add(checks, "BASELINE_SET", baseline.get("baseline_version") == 1, baseline)
                restored = client.post("/task-center/tasks/taskcenter_audit_task/versions/1/restore", json={"change_summary": "恢复基线"}).json()["task"]
                add(checks, "RESTORE_CREATES_NEW_VERSION", restored.get("current_version") == 3, restored)
                add(checks, "RESTORE_CONTENT_MATCHES", restored.get("task_spec", {}).get("simulation", {}).get("duration_s") == 10.0, restored)
                clone = client.post("/task-center/tasks/taskcenter_audit_task/clone", json={"name": "审计副本"}).json()["task"]
                add(checks, "TASK_CLONE_NEW_ID", clone.get("task_id") != "taskcenter_audit_task" and clone.get("current_version") == 1, clone)
                health = client.get("/task-center/health").json()["task_center"]
                add(checks, "TASK_CENTER_V2_HEALTH", health.get("schema_version") == "task-center.v2", health)

                index = client.get("/").text
                js = client.get("/assets/app.js").text
                add(checks, "WORKBENCH_DISCOVERY_UI", "自动发现模型" in index and "保存并完整测试" in index, None)
                add(checks, "WORKBENCH_TASK_DETAILS_UI", "任务版本与运行历史" in index and "/versions" in js and "/clone" in js, None)
                add(checks, "API_VERSION_MATCH", client.get("/health").json().get("api_version") == "workbench-api.v1", client.get("/health").json())
    finally:
        server.shutdown()
        server.server_close()
    passed = sum(item["status"] == "PASS" for item in checks)
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS" if passed == len(checks) else "FAIL",
        "summary": {"total": len(checks), "passed": passed, "failed": len(checks) - passed},
        "checks": checks,
        "claim_boundary": "Local model probes validate connectivity, JSON output and one constrained TaskSpec only; they do not certify all prompts or model quality.",
    }
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="reports/taskcenter_local_model_task_lifecycle_audit.json")
    args = parser.parse_args()
    report = run(args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
