from pathlib import Path


def test_guided_and_realtime_views_use_single_column_conversation_shell():
    css = Path("src/sat_sim/web/compact-workbench.css").read_text(encoding="utf-8")
    for view in ("wizard", "interactive", "continuous"):
        assert f'body[data-active-view="{view}"] .app-frame' in css
        assert f'body[data-active-view="{view}"] .app-sidebar' in css
    assert "grid-template-columns: minmax(0, 1fr);" in css
    assert ".wizard-conversation" in css
    assert ".wizard-message.user" in css
    assert ".wizard-footer" in css


def test_conversation_shell_keeps_narrow_screen_layout_and_reduced_motion():
    css = Path("src/sat_sim/web/compact-workbench.css").read_text(encoding="utf-8")
    assert "@media (max-width: 760px)" in css
    assert ".wizard-realtime-workspace .continuous-grid { grid-template-columns: minmax(0, 1fr); }" in css
    assert "@media (prefers-reduced-motion: reduce)" in css


def test_wizard_progress_is_present_and_tracks_existing_state_machine():
    from pathlib import Path

    html = Path("src/sat_sim/web/index.html").read_text(encoding="utf-8")
    js = Path("src/sat_sim/web/app.js").read_text(encoding="utf-8")
    css = Path("src/sat_sim/web/compact-workbench.css").read_text(encoding="utf-8")
    assert 'id="wizardProgress"' in html
    assert "function renderWizardProgress()" in js
    assert "renderWizardProgress();" in js
    for label in ("任务目标", "仿真对象", "运行方式", "配置与结果"):
        assert label in js
    assert ".wizard-progress-step[data-state=\"current\"]" in css
    assert "@media (max-width: 540px)" in css


def test_realtime_conversation_mount_keeps_app_frame_full_width():
    css = Path("src/sat_sim/web/compact-workbench.css").read_text(encoding="utf-8")
    assert '.app-frame:has(.wizard-view[data-runtime-mode="interactive"])' in css
    assert '.app-frame:has(.wizard-view[data-runtime-mode="continuous"])' in css
    assert "grid-template-columns: minmax(0, 1fr) !important;" in css
    assert ".app-frame:has(.wizard-view[data-runtime-mode=\"continuous\"]) .app-content" in css


def test_graph_modeling_has_conversational_guide_over_native_drag_canvas():
    html = Path("src/sat_sim/web/index.html").read_text(encoding="utf-8")
    js = Path("src/sat_sim/web/app.js").read_text(encoding="utf-8")
    css = Path("src/sat_sim/web/compact-workbench.css").read_text(encoding="utf-8")

    assert 'id="graphConversationGuide"' in html
    assert 'id="graphGuideNextTitle"' in html
    assert 'id="graphGuideNextDetail"' in html
    assert 'id="graphCanvas"' in html
    assert 'id="graphCapabilityPalette"' in html
    assert 'id="graphInspector"' in html
    assert "function renderGraphGuide()" in js
    assert "renderGraphGuide();" in js
    assert "graphValidateStructure({ requireCode: true, requireRun: true })" in js
    assert "graphCompile({ requireCode: true, requireRun: true })" in js
    assert "graphInitialize(true)" in js
    assert "graphAutoWire()" in js
    assert ".graph-conversation-guide" in css
    assert "不会绕过端口类型、DAG、TaskSpec" in html


def test_guided_task_workspace_prioritizes_common_actions_and_discloses_secondary_actions():
    js = Path("src/sat_sim/web/app.js").read_text(encoding="utf-8")
    css = Path("src/sat_sim/web/compact-workbench.css").read_text(encoding="utf-8")
    for label in ("任务详情", "编辑配置", "运行任务", "更多操作", "克隆任务", "删除任务"):
        assert label in js
    assert "wizard-task-more" in js
    assert ".wizard-task-more-actions" in css
    assert "openTaskDetails(task.task_id)" in js
    assert "runTaskFromCenter(task.task_id, { guided: true })" in js


def test_graph_guide_distinguishes_frontend_structure_from_server_compile_success():
    js = Path("src/sat_sim/web/app.js").read_text(encoding="utf-8")
    assert "state.graphCompilationContext === visualCompilationSnapshot('flow')" in js
    assert "state.graphCompilationContext = requestedCompilation" in js
    assert "state.graphCompilationContext = ''" in js
    assert "正式校验已通过，可以生成 Python" in js
    assert "图、正式校验和生成代码保持一致" in js
    assert "renderGraphGuide();" in js


def test_taskspec_can_be_applied_and_opened_in_the_native_graph_editor():
    html = Path("src/sat_sim/web/index.html").read_text(encoding="utf-8")
    js = Path("src/sat_sim/web/app.js").read_text(encoding="utf-8")
    assert 'id="wizardOpenGraphBtn"' in html
    assert "await syncEditorToForm();" in js[js.index("wizardOpenGraphBtn").__str__():] if False else True
    start = js.index("el('wizardOpenGraphBtn').addEventListener")
    handler = js[start:js.index("el('previewBtn').addEventListener", start)]
    assert "await syncEditorToForm()" in handler
    assert "graphInitialize(true)" in handler
    assert "graphInitialize(false)" in handler
    assert "switchView('creator')" in handler
    assert "switchTab('graph')" in handler
    assert "graphSetStatus('TaskSpec 已投影到当前模型配置" in handler
