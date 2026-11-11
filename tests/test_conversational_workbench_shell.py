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
