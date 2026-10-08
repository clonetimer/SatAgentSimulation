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


def test_workbench_exposes_graphical_composer_and_codegen_controls(tmp_path: Path):
    with _client(tmp_path) as client:
        index = client.get("/").text
        css = client.get("/assets/styles.css").text
        js = client.get("/assets/app.js").text
    assert 'data-tab="graph"' in index
    assert 'id="graphCanvas"' in index
    assert 'id="graphCapabilityPalette"' in index
    assert 'id="graphGenerateCodeBtn"' in index
    assert 'id="graphAutoWireBtn"' in index
    assert 'id="graphUndoBtn"' in index
    assert 'id="graphRedoBtn"' in index
    assert 'id="graphSaveDraftBtn"' in index
    assert 'id="graphLoadDraftBtn"' in index
    assert 'id="graphExportProjectBtn"' in index
    assert 'id="graphImportProjectInput"' in index
    assert 'id="graphAssemblyModeBtn"' in index
    assert 'id="assemblyCanvas"' in index
    assert 'id="assemblyCapabilitySelect"' in index
    assert 'id="assemblyGenerateCodeBtn"' in index
    assert "图形组装" in index
    assert "多模块装配 · V15" in index
    assert 'id="assemblyModuleLibrary"' in index
    assert 'id="assemblyLibrarySummary"' in index
    assert ".graph-canvas" in css
    assert ".graph-port-button" in css
    assert ".graph-edge-hit" in css
    assert ".assembly-canvas" in css
    assert ".assembly-edge-feedback" in css
    assert ".assembly-module-library" in css
    assert ".assembly-node.drop-compatible" in css
    assert "graphHandleDrop" in js
    assert "graphCompile" in js
    assert "graphCreateEdge" in js
    assert "graphAutoWire" in js
    assert "graphHistoryCommit" in js
    assert "graphUndo" in js
    assert "graphRedo" in js
    assert "graphBeginRewireEdge" in js
    assert "graphProjectBundle" in js
    assert "graphLoadProjectBundle" in js
    assert "sat-sim.visual-composer.project.v1" in js
    assert "/tasks/compile-graph" in js
    assert "/tasks/export-script" in js
    assert "assemblyLoadContract" in js
    assert "assemblyCompile" in js
    assert "assemblyGenerateCodeFromTaskSpec" in js
    assert "/visual-composer/assemblies" in js
    assert "/visual-composer/module-library" in js
    assert "simulation_ports" in js
    assert "port_contract_source" in js
    assert "/tasks/compile-assembly" in js
    assert "/tasks/export-assembly-script" in js
    assert "/visual-composer/module-replacements/" not in js  # contract is bundled with assembly payload
    assert "assemblyReplaceModule" in js
    assert "assemblyRenderModuleLibrary" in js
    assert "assembly-replacement-select" in js
    assert "parent-managed internal slot" in index


def test_task_export_script_endpoint_returns_executable_deterministic_python(tmp_path: Path):
    with _client(tmp_path) as client:
        schema = client.get("/forms/capabilities/component.battery.v1").json()["form_schema"]
        parsed = client.post(
            "/tasks/parse",
            json={"input_kind": "form", "form_data": schema["default_form"], "compile_if_valid": True},
        )
        assert parsed.status_code == 200
        assert parsed.json()["ok"] is True
        task_spec = parsed.json()["result"]["task_spec"]
        exported = client.post("/tasks/export-script", json={"task_spec": task_spec})
    assert exported.status_code == 200
    payload = exported.json()
    assert payload["ok"] is True
    assert payload["kind"] == "capability-python"
    assert payload["filename"].endswith(".py")
    assert payload["provenance"]["generator"] == "sat_sim.script_exporter"
    assert payload["provenance"]["arbitrary_python_accepted"] is False
    assert "CAPABILITY_ID = 'component.battery.v1'" in payload["code"]
    assert "execute_compiled_task" in payload["code"]
    compile(payload["code"], payload["filename"], "exec")


def test_typed_graph_compile_endpoint_validates_topology_before_taskspec(tmp_path: Path):
    def edge(source: str, source_port: str, target: str, target_port: str) -> dict:
        return {
            "id": f"{source}.{source_port}__{target}.{target_port}",
            "source": source,
            "sourcePort": source_port,
            "target": target,
            "targetPort": target_port,
        }

    graph = {
        "schema_version": "sat-sim.visual-graph.v1",
        "nodes": [
            {"id": "graph-model", "type": "model", "capabilityId": "component.battery.v1"},
            {"id": "graph-config", "type": "config"},
            {"id": "graph-effects", "type": "effects"},
            {"id": "graph-outputs", "type": "outputs"},
            {"id": "graph-code", "type": "code"},
            {"id": "graph-run", "type": "run"},
        ],
        "edges": [
            edge("graph-model", "capability", "graph-config", "capability"),
            edge("graph-config", "task", "graph-effects", "task"),
            edge("graph-effects", "task", "graph-outputs", "task"),
            edge("graph-outputs", "spec", "graph-code", "spec"),
            edge("graph-outputs", "spec", "graph-run", "spec"),
            edge("graph-code", "script", "graph-run", "script"),
        ],
    }
    with _client(tmp_path) as client:
        form_data = client.get("/forms/capabilities/component.battery.v1").json()["form_schema"]["default_form"]
        compiled = client.post(
            "/tasks/compile-graph",
            json={"graph": graph, "form_data": form_data, "require_code": True, "require_run": True},
        )
        assert compiled.status_code == 200
        payload = compiled.json()
        assert payload["ok"] is True
        assert payload["graph_validation"]["ok"] is True
        assert payload["graph_validation"]["topological_order"][0] == "graph-model"
        assert payload["result"]["task_spec"]["model"]["capability_id"] == "component.battery.v1"

        bad_graph = json.loads(json.dumps(graph))
        bad_graph["edges"][4] = edge("graph-code", "script", "graph-run", "spec")
        rejected = client.post(
            "/tasks/compile-graph",
            json={"graph": bad_graph, "form_data": form_data, "require_code": True, "require_run": True},
        )
        assert rejected.status_code == 200
        rejected_payload = rejected.json()
        assert rejected_payload["ok"] is False
        assert rejected_payload["result"] is None
        codes = {item["code"] for item in rejected_payload["graph_validation"]["errors"]}
        assert "GRAPH_PORT_TYPE_MISMATCH" in codes
        assert "GRAPH_REQUIRED_INPUT_UNCONNECTED" in codes


def test_v9_workbench_exposes_rapid_graph_programming_controls(tmp_path: Path):
    with _client(tmp_path) as client:
        index = client.get("/").text
        css = client.get("/assets/styles.css").text
        js = client.get("/assets/app.js").text
    for control_id in (
        "graphExportProgramBtn",
        "assemblyExportProgramBtn",
        "assemblyQuickTemplates",
        "assemblyModuleSearch",
        "assemblyCompatibleOnly",
    ):
        assert f'id="{control_id}"' in index
    assert "像 Simulink 一样组装，直接形成可运行程序" in index
    assert "assembly-quick-template" in css
    assert "assemblyApplyQuickTemplate" in js
    assert "postDownloadEndpoint('/tasks/export-program'" in js
    assert "自动匹配到" in js


def test_v9_capability_program_bundle_contains_runner_manifest_and_launchers(tmp_path: Path):
    capability_id = "component.reaction_wheel.v1"
    with _client(tmp_path) as client:
        form = client.get(f"/forms/capabilities/{capability_id}").json()["form_schema"]["default_form"]
        parsed = client.post(
            "/tasks/parse",
            json={"input_kind": "form", "form_data": form, "compile_if_valid": True},
        )
        assert parsed.status_code == 200
        assert parsed.json()["ok"] is True
        task_spec = parsed.json()["result"]["task_spec"]
        response = client.post("/tasks/export-program", json={"task_spec": task_spec})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["x-sat-sim-program-kind"] == "capability-program"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        root = names[0].split("/", 1)[0]
        expected = {
            f"{root}/README.md",
            f"{root}/program_manifest.json",
            f"{root}/run.bat",
            f"{root}/run.sh",
            f"{root}/run_simulation.py",
            f"{root}/task_spec.json",
        }
        assert expected <= set(names)
        manifest = json.loads(archive.read(f"{root}/program_manifest.json"))
        assert manifest["schema_version"] == "sat-sim.visual-program.v1"
        assert manifest["kind"] == "capability-program"
        assert manifest["capability_id"] == capability_id
        assert manifest["arbitrary_python_accepted"] is False
        code = archive.read(f"{root}/run_simulation.py").decode("utf-8")
        readme = archive.read(f"{root}/README.md").decode("utf-8")
        assert "Generated capability-python simulation script" in code
        assert "execute_compiled_task" in code
        assert "bash run.sh" in readme


def test_v9_assembly_program_bundle_preserves_validated_graph_and_module_selection(tmp_path: Path):
    parent = "subsystem.adcs_fidelity.v1"
    with _client(tmp_path) as client:
        contract = client.get(f"/visual-composer/assemblies/{parent}").json()["assembly"]
        graph = json.loads(json.dumps(contract["default_graph"]))
        wheel = next(node for node in graph["nodes"] if node["moduleAlias"] == "reaction_wheel")
        wheel["capabilityId"] = "component.reaction_wheel.v1"
        form = client.get(f"/forms/capabilities/{parent}").json()["form_schema"]["default_form"]
        compiled = client.post(
            "/tasks/compile-assembly",
            json={"assembly_graph": graph, "form_data": form, "require_all_bindings": True},
        )
        assert compiled.status_code == 200
        assert compiled.json()["ok"] is True
        task_spec = compiled.json()["result"]["task_spec"]
        response = client.post(
            "/tasks/export-program",
            json={"task_spec": task_spec, "assembly_graph": graph, "require_all_bindings": True},
        )
    assert response.status_code == 200
    assert response.headers["x-sat-sim-program-kind"] == "assembly-program"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        root = names[0].split("/", 1)[0]
        assert f"{root}/assembly_graph.json" in names
        manifest = json.loads(archive.read(f"{root}/program_manifest.json"))
        assert manifest["kind"] == "assembly-program"
        assert manifest["replacement_count"] == 1
        assert manifest["selected_modules"]["reaction_wheel"] == "component.reaction_wheel.v1"
        assert len(manifest["port_contract_fingerprint"]) == 64
        assert len(manifest["module_contract_fingerprint"]) == 64
        code = archive.read(f"{root}/run_simulation.py").decode("utf-8")
        assert "validate_assembly_task_spec_binding" in code


def test_v13_assembly_program_bundle_preserves_scope_as_project_observer_only(tmp_path: Path):
    parent = "subsystem.adcs_fidelity.v1"
    with _client(tmp_path) as client:
        contract = client.get(f"/visual-composer/assemblies/{parent}").json()["assembly"]
        graph = json.loads(json.dumps(contract["default_graph"]))
        graph["scopes"] = [{
            "id": "scope-rw",
            "name": "RW speed",
            "x": 860,
            "y": 70,
            "probes": [{
                "id": "probe-rw",
                "sourceNode": "module-reaction_wheel",
                "sourcePort": "reaction_wheel.out.wheel_speed_rad_s",
            }],
        }]
        form = client.get(f"/forms/capabilities/{parent}").json()["form_schema"]["default_form"]
        compiled = client.post(
            "/tasks/compile-assembly",
            json={"assembly_graph": graph, "form_data": form, "require_all_bindings": True},
        ).json()
        assert compiled["ok"] is True
        response = client.post(
            "/tasks/export-program",
            json={"task_spec": compiled["result"]["task_spec"], "assembly_graph": graph, "require_all_bindings": True},
        )
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        root = archive.namelist()[0].split("/", 1)[0]
        saved_graph = json.loads(archive.read(f"{root}/assembly_graph.json"))
        manifest = json.loads(archive.read(f"{root}/program_manifest.json"))
        runner = archive.read(f"{root}/run_simulation.py").decode("utf-8")
        readme = archive.read(f"{root}/README.md").decode("utf-8")
    assert saved_graph["scopes"][0]["id"] == "scope-rw"
    assert manifest["observer_scope_count"] == 1
    assert manifest["observer_probe_count"] == 1
    assert manifest["observer_runtime_effect"] == "none"
    assert "scope-rw" not in runner
    assert "Scope / Display / To Workspace 等 observer 不会进入生成的物理 runner" in readme



def test_v14_observer_toolbox_preserves_physical_task_and_runner(tmp_path: Path):
    parent = "subsystem.adcs_fidelity.v1"
    with _client(tmp_path) as client:
        contract = client.get(f"/visual-composer/assemblies/{parent}").json()["assembly"]
        base_graph = json.loads(json.dumps(contract["default_graph"]))
        observed_graph = json.loads(json.dumps(base_graph))
        observed_graph["scopes"] = [
            {
                "kind": "scope",
                "id": "scope-rw",
                "name": "RW scope",
                "x": 850,
                "y": 40,
                "probes": [{"id": "p1", "sourceNode": "module-reaction_wheel", "sourcePort": "reaction_wheel.out.wheel_speed_rad_s"}],
            },
            {
                "kind": "display",
                "id": "display-rw",
                "name": "RW display",
                "x": 850,
                "y": 180,
                "probes": [{"id": "p2", "sourceNode": "module-reaction_wheel", "sourcePort": "reaction_wheel.out.wheel_speed_rad_s"}],
            },
            {
                "kind": "workspace",
                "id": "workspace-rw",
                "name": "RW export",
                "x": 850,
                "y": 320,
                "probes": [{"id": "p3", "sourceNode": "module-reaction_wheel", "sourcePort": "reaction_wheel.out.wheel_speed_rad_s"}],
            },
        ]
        form = client.get(f"/forms/capabilities/{parent}").json()["form_schema"]["default_form"]
        base_compiled = client.post(
            "/tasks/compile-assembly",
            json={"assembly_graph": base_graph, "form_data": form, "require_all_bindings": True},
        ).json()
        observed_compiled = client.post(
            "/tasks/compile-assembly",
            json={"assembly_graph": observed_graph, "form_data": form, "require_all_bindings": True},
        ).json()
        assert base_compiled["ok"] is True
        assert observed_compiled["ok"] is True
        assert observed_compiled["result"]["task_spec"] == base_compiled["result"]["task_spec"]

        base_export = client.post(
            "/tasks/export-assembly-script",
            json={"assembly_graph": base_graph, "task_spec": base_compiled["result"]["task_spec"], "require_all_bindings": True},
        ).json()
        observed_export = client.post(
            "/tasks/export-assembly-script",
            json={"assembly_graph": observed_graph, "task_spec": observed_compiled["result"]["task_spec"], "require_all_bindings": True},
        ).json()
        assert base_export["ok"] is True
        assert observed_export["ok"] is True
        assert observed_export["code"] == base_export["code"]

        program = client.post(
            "/tasks/export-program",
            json={"task_spec": observed_compiled["result"]["task_spec"], "assembly_graph": observed_graph, "require_all_bindings": True},
        )
    assert program.status_code == 200
    with zipfile.ZipFile(io.BytesIO(program.content)) as archive:
        root = archive.namelist()[0].split("/", 1)[0]
        manifest = json.loads(archive.read(f"{root}/program_manifest.json"))
        saved_graph = json.loads(archive.read(f"{root}/assembly_graph.json"))
        runner = archive.read(f"{root}/run_simulation.py").decode("utf-8")
    assert manifest["observer_count"] == 3
    assert manifest["observer_scope_count"] == 1
    assert manifest["observer_display_count"] == 1
    assert manifest["observer_workspace_count"] == 1
    assert manifest["observer_probe_count"] == 3
    assert manifest["observer_runtime_effect"] == "none"
    assert {item.get("kind", "scope") for item in saved_graph["scopes"]} == {"scope", "display", "workspace"}
    assert "scope-rw" not in runner
    assert "display-rw" not in runner
    assert "workspace-rw" not in runner


def test_v14_workbench_exposes_explicit_observer_sink_toolbox(tmp_path: Path):
    with _client(tmp_path) as client:
        index = client.get("/").text
        css = client.get("/assets/styles.css").text
        js = client.get("/assets/app.js").text
    assert 'id="assemblyAddScopeBtn"' in index
    assert 'id="assemblyAddDisplayBtn"' in index
    assert 'id="assemblyAddWorkspaceBtn"' in index
    assert "Scope" in index
    assert "Display" in index
    assert "To Workspace" in index
    assert "assemblyAddObserver" in js
    assert "assemblyAddDisplay" in js
    assert "assemblyAddWorkspace" in js
    assert "assemblyExportWorkspace" in js
    assert "observer_overlays" in js
    assert ".assembly-display-value" in css
    assert ".assembly-observer-workspace" in css
    assert "0.15.0-graph-v15" in index


def test_v15_workbench_exposes_fast_modeling_layout_tools(tmp_path: Path):
    with _client(tmp_path) as client:
        index = client.get("/").text
        css = client.get("/assets/styles.css").text
        js = client.get("/assets/app.js").text
    for control_id in (
        "graphAutoLayoutBtn",
        "assemblyAutoLayoutBtn",
        "assemblyAlignLeftBtn",
        "assemblyAlignTopBtn",
        "assemblyDistributeHBtn",
        "assemblyDistributeVBtn",
        "assemblyCopyObserversBtn",
        "assemblyPasteObserversBtn",
        "assemblySelectionBox",
        "assemblySelectionSummary",
    ):
        assert f'id="{control_id}"' in index
    assert "多模块装配 · V15" in index
    assert "assemblyBeginMarquee" in js
    assert "assemblyStartItemDrag" in js
    assert "assemblyAutoLayout" in js
    assert "assemblyAlignSelection" in js
    assert "assemblyDistributeSelection" in js
    assert "assemblyCopySelectedObservers" in js
    assert "assemblyPasteObservers" in js
    assert "assemblyStronglyConnectedLevels" in js
    assert "graphAutoLayout" in js
    assert ".assembly-selection-box" in css
    assert ".assembly-node.layout-selected" in css
    assert "0.15.0-graph-v15" in index


def test_v15_layout_only_snapshot_ignores_coordinates_and_observers(tmp_path: Path):
    with _client(tmp_path) as client:
        js = client.get("/assets/app.js").text
    start = js.index("function assemblySnapshot()")
    end = js.index("function assemblyCodeIsFresh()", start)
    snapshot_impl = js[start:end]
    assert "node.x" not in snapshot_impl
    assert "node.y" not in snapshot_impl
    assert "scopes" not in snapshot_impl
    drag_start = js.index("function assemblyStartItemDrag")
    drag_end = js.index("function assemblyBeginMarquee", drag_start)
    assert "assemblyGeneratedSnapshot = ''" not in js[drag_start:drag_end]

def test_v9_visual_flow_program_bundle_preserves_graph_source(tmp_path: Path):
    capability_id = "component.reaction_wheel.v1"
    graph = {
        "schema_version": "sat-sim.visual-graph.v1",
        "nodes": [
            {"id": "graph-model", "type": "model", "capabilityId": capability_id},
            {"id": "graph-config", "type": "config"},
            {"id": "graph-effects", "type": "effects"},
            {"id": "graph-outputs", "type": "outputs"},
            {"id": "graph-code", "type": "code"},
            {"id": "graph-run", "type": "run"},
        ],
        "edges": [
            {"id": "e1", "source": "graph-model", "sourcePort": "capability", "target": "graph-config", "targetPort": "capability"},
            {"id": "e2", "source": "graph-config", "sourcePort": "task", "target": "graph-effects", "targetPort": "task"},
            {"id": "e3", "source": "graph-effects", "sourcePort": "task", "target": "graph-outputs", "targetPort": "task"},
            {"id": "e4", "source": "graph-outputs", "sourcePort": "spec", "target": "graph-code", "targetPort": "spec"},
            {"id": "e5", "source": "graph-outputs", "sourcePort": "spec", "target": "graph-run", "targetPort": "spec"},
            {"id": "e6", "source": "graph-code", "sourcePort": "script", "target": "graph-run", "targetPort": "script"},
        ],
    }
    with _client(tmp_path) as client:
        form = client.get(f"/forms/capabilities/{capability_id}").json()["form_schema"]["default_form"]
        parsed = client.post(
            "/tasks/parse",
            json={"input_kind": "form", "form_data": form, "compile_if_valid": True},
        ).json()
        response = client.post(
            "/tasks/export-program",
            json={"task_spec": parsed["result"]["task_spec"], "visual_graph": graph},
        )
    assert response.status_code == 200
    assert response.headers["x-sat-sim-program-kind"] == "visual-flow-program"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        root = names[0].split("/", 1)[0]
        assert f"{root}/visual_graph.json" in names
        saved_graph = json.loads(archive.read(f"{root}/visual_graph.json"))
        assert saved_graph["schema_version"] == "sat-sim.visual-graph.v1"
        manifest = json.loads(archive.read(f"{root}/program_manifest.json"))
        assert manifest["visual_graph"] == "visual_graph.json"
        assert manifest["visual_graph_node_count"] == 6
        assert manifest["visual_graph_edge_count"] == 6


def test_v10_workbench_exposes_debug_compare_and_code_change_feedback(tmp_path: Path):
    with _client(tmp_path) as client:
        index = client.get("/").text
        css = client.get("/assets/styles.css").text
        js = client.get("/assets/app.js").text
    assert 'id="assemblyCompareBtn"' in index
    assert 'id="assemblyComparePanel"' in index
    assert 'id="assemblyCompareTable"' in index
    assert "与基线对比" in index
    assert "/visual-composer/diagnose" in js
    assert "assemblyCompareBaseline" in js
    assert "changedSnapshotPaths" in js
    assert "codeDiffSummary" in js
    assert "ASSEMBLY_TASK_MODULE_SELECTION_MISMATCH" not in index
    assert ".graph-node.diagnostic-error" in css
    assert ".assembly-compare-panel" in css
    assert ".code-change-card" in css


def test_v10_run_comparison_preserves_requested_metric_priority(tmp_path: Path):
    for run_id, values in (
        ("baseline", {"adapter.metric": 1.0, "qoi.primary": 2.0, "qoi.secondary": 3.0}),
        ("current", {"adapter.metric": 1.5, "qoi.primary": 2.5, "qoi.secondary": 3.5}),
    ):
        result_root = tmp_path / "runs" / run_id / "results"
        result_root.mkdir(parents=True)
        (result_root / "metrics.json").write_text(json.dumps({"metrics": values}), encoding="utf-8")
        (tmp_path / "runs" / run_id / "run_record.json").write_text(
            json.dumps({"status": "SUCCEEDED", "validation_result": "PASS"}), encoding="utf-8"
        )
    with _client(tmp_path) as client:
        response = client.post(
            "/runs/compare",
            json={"run_ids": ["baseline", "current"], "metrics": ["qoi.primary", "qoi.secondary", "adapter.metric"]},
        )
    assert response.status_code == 200
    assert response.json()["comparison"]["metrics"] == ["qoi.primary", "qoi.secondary", "adapter.metric"]


def test_v13_workbench_keeps_model_nodes_clean_and_uses_explicit_scope(tmp_path: Path):
    with _client(tmp_path) as client:
        index = client.get("/").text
        css = client.get("/assets/styles.css").text
        js = client.get("/assets/app.js").text
    assert 'id="graphResultOverlayBtn"' in index
    assert 'id="assemblyResultOverlayBtn"' in index
    assert 'id="assemblyAddScopeBtn"' in index
    assert "查看运行结果" in index
    assert "Scope" in index
    assert "/visual-composer/run-overlay" in js
    assert "loadVisualRunOverlay" in js
    assert "openVisualRunResults" in js
    assert "assemblyScopeElement" in js
    assert "assemblyCompleteScopeProbe" in js
    assert "drawVisualMiniSeries" in js
    assert "appendVisualNodeResults" not in js
    assert "appendVisualNodeBadge" not in js
    assert "visual-edge-value" not in js
    assert ".assembly-scope-node" in css
    assert ".assembly-scope-probe" in css
    assert ".assembly-scope-chart" in css
    assert ".visual-node-result-badge" not in css
    assert ".visual-edge-value" not in css
    assert "0.15.0-graph-v15" in index


def test_v12_workbench_exposes_constrained_quick_tuning_loop(tmp_path: Path):
    with _client(tmp_path) as client:
        index = client.get("/").text
        css = client.get("/assets/styles.css").text
        js = client.get("/assets/app.js").text
    assert 'id="graphTuneBtn"' in index
    assert 'id="assemblyTuneBtn"' in index
    assert 'id="visualTuningPanel"' in index
    assert 'id="visualTuningObjective"' in index
    assert 'id="visualTuningApplyBestBtn"' in index
    assert "快速调参 · V12" in index
    assert "/visual-composer/tuning-options" in js
    assert "/visual-composer/tuning-plan" in js
    assert "/visual-composer/tuning-rank" in js
    assert "startVisualTuning" in js
    assert "applyVisualTuningBest" in js
    assert ".visual-tuning-panel" in css
    assert ".visual-tuning-result-row.best" in css
