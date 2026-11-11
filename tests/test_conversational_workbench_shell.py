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
