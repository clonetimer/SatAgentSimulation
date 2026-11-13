from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from sat_sim.api import create_app


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=False,
        auth_mode="disabled",
    ))


def test_wizard_explains_basic_parameters_and_keeps_advanced_schema_fields_collapsed(tmp_path):
    with _client(tmp_path) as client:
        index = client.get("/").text
        js = client.get("/assets/app.js").text
    assert 'id="wizardParametersPanel"' in index
    assert 'id="wizardTaskWorkspace"' in index
    assert 'id="wizardCurveChoices"' in index
    assert "仿真总时长" in js
    assert "采样间隔" in js
    assert "wizardParameterPresetValues" in js


def test_wizard_curve_presets_are_schema_derived_and_task_center_is_dialog_routed(tmp_path):
    with _client(tmp_path) as client:
        index = client.get("/").text
        js = client.get("/assets/app.js").text
    assert 'data-wizard-shortcut="history"' in index
    assert "renderWizardCurveChoices" in js
    assert "normalizedOutputOption" in js
    assert "wizardToggleCurveGroup" in js
    assert "wizardTaskList" in js
    assert "openTaskFromCenter(task.task_id, { guided: true })" in js
    assert "runTaskFromCenter(task.task_id, { guided: true })" in js
    assert "保存为新版本" in js


def test_wizard_task_management_reuses_existing_task_center_api(tmp_path):
    with _client(tmp_path) as client:
        index = client.get("/").text
        js = client.get("/assets/app.js").text
    for action in ("任务详情", "克隆任务", "编辑配置", "运行任务", "删除任务", "查看结果", "保存当前配置", "保存为新版本"):
        assert action in js or action in index
    assert "/task-center/tasks" in js
    assert "openTaskDetails(task.task_id)" in js



def test_natural_language_and_taskspec_are_available_inside_wizard(tmp_path):
    with _client(tmp_path) as client:
        index = client.get("/").text
        js = client.get("/assets/app.js").text
    for marker in (
        'id="wizardAgentWorkspace"', 'id="wizardAgentHost"',
        'id="wizardTaskSpecWorkspace"', 'id="wizardTaskSpecHost"',
        'id="wizardGenerateFromTextBtn"', 'id="wizardValidateTaskSpecBtn"',
        'id="wizardApplyTaskSpecBtn"',
    ):
        assert marker in index
    for marker in ("natural_language", "taskspec", "parseNaturalLanguage()", "syncEditorToForm()", "wizardGenerateFromTextBtn"):
        assert marker in js
    assert "/assets/app.js?v=" in index


def test_specialist_tools_keep_dedicated_workspaces_and_are_not_forced_into_chat(tmp_path):
    with _client(tmp_path) as client:
        index = client.get("/").text
        js = client.get("/assets/app.js").text
    for goal in ("experiments", "goals", "graph", "diagnostics"):
        assert f'data-wizard-goal="{goal}"' in index
    assert "wizard-tool-option[data-wizard-goal]" in js
    assert "switchView('experiments')" in js
    assert "switchView('goals')" in js
    assert "switchTab('graph')" in js


def test_wizard_parameter_modes_preserve_registry_fields_and_expose_progressive_disclosure(tmp_path):
    with _client(tmp_path) as client:
        index = client.get("/").text
        js = client.get("/assets/app.js").text
        css = client.get("/assets/compact-workbench.css").text
        schema = client.get("/forms/capabilities/component.reaction_wheel.v1")
    assert schema.status_code == 200
    fields = schema.json()["form_schema"]["fields"]
    parameter_count = sum(str(field["path"]).startswith("parameters.values.") for field in fields)
    assert parameter_count > 0
    for mode in ("runtime", "recommended", "all"):
        assert f'data-wizard-parameter-mode="{mode}"' in index
        assert f"'{mode}'" in js
    assert 'id="wizardParameterSearch"' in index
    assert 'id="wizardParameterCount"' in index
    assert 'id="wizardParameterSearchEmpty"' in index
    assert "wizardParameterSearchEmpty" in js
    assert "function renderWizardParameterControls()" in js
    assert "function setWizardParameterMode(mode)" in js
    assert "state.formSectionOpen.clear()" in js
    assert "wizardParameterMode === 'all'" in js
    assert 'data-wizard-parameter-mode="runtime"' in css
    assert 'data-wizard-parameter-mode="recommended"' in css
    assert 'data-wizard-parameter-mode="all"' in css
    assert "已保留" in js


def test_wizard_separates_summary_metrics_from_time_series_and_uses_registry_options(tmp_path):
    with _client(tmp_path) as client:
        index = client.get("/").text
        js = client.get("/assets/app.js").text
        css = client.get("/assets/compact-workbench.css").text
        response = client.get("/forms/capabilities/whole_spacecraft.unified_native.v1")
    assert response.status_code == 200
    schema = response.json()["form_schema"]
    qoi_names = {item["name"] for item in schema["outputs"]["summary_options"]}
    trace_names = {item["name"] for item in schema["outputs"]["trace_options"]}
    assert qoi_names
    assert trace_names
    assert qoi_names.issubset(set(schema["outputs"]["summary_qoi"]))
    assert trace_names.issubset(set(schema["outputs"]["trace_fields"]))
    assert 'id="wizardQoiChoices"' in index
    assert 'id="wizardCurveChoices"' in index
    assert 'id="wizardOutputPurposes"' in index
    assert 'id="wizardOutputSearch"' in index
    assert 'id="wizardRestoreOutputDefaultsBtn"' in index
    assert "function wizardOutputPurpose(option)" in js
    assert "function wizardSetQoiSelection(nextSelection)" in js
    assert "function wizardRestoreOutputDefaults()" in js
    assert "state.schema.outputs?.summary_options" in js
    assert "state.schema.outputs?.trace_options" in js
    assert "state.schema.default_form?.outputs" in js
    assert "wizardOutputSearch" in js
    assert ".wizard-qoi-row" in css
    assert ".wizard-output-purpose[aria-pressed=\"true\"]" in css

def test_p7b_natural_language_candidate_preview_requires_explicit_apply(tmp_path):
    with _client(tmp_path) as client:
        index = client.get("/").text
        js = client.get("/assets/app.js").text
    assert 'id="wizardAgentCandidatePreview"' in index
    assert "生成候选预览" in index
    for marker in (
        "wizardApplyAgentCandidateBtn",
        "wizardApplyAgentCandidateGraphBtn",
        "wizardDiscardAgentCandidateBtn",
        "parseNaturalLanguage({ previewOnly: true })",
        "taskSpecFieldDiff",
        "state.agentCandidate = { taskSpec:",
        "applyAgentCandidate(openGraph = false)",
        "此处为预览，不代表服务端图校验已通过",
        "openCurrentConfigurationInGraph('已确认的 Agent 候选')",
    ):
        assert marker in js
    assert "api('/tasks/compile-graph'" in js
    assert "api('/tasks/parse'" in js


def test_p7b_candidate_preview_blocks_apply_after_context_changes(tmp_path):
    with _client(tmp_path) as client:
        js = client.get("/assets/app.js").text
        css = client.get("/assets/styles.css").text
    assert "function agentCandidateBaseSnapshot()" in js
    assert "candidate.baseSnapshot, currentSnapshot" in js
    assert "当前任务或图形画布在候选生成后已发生变化" in js
    assert "agent-candidate-flow" in js
    assert "agent-candidate-node" in js
    assert "agent-candidate-edge" in css
    assert "此处为预览，不代表服务端图校验已通过" in js
