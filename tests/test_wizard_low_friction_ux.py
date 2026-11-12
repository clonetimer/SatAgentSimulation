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
    assert "/assets/app.js?v=wizard7" in index


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
