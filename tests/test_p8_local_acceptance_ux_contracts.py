from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "sat_sim" / "web"


def test_wizard_has_prominent_professional_entry_and_retains_graph_tool():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert 'id="wizardAdvancedBtn"' in html
    assert "wizardAdvancedBtn" in app
    assert "if (goal === 'graph') { switchView('creator'); switchTab('graph'); return; }" in app
    assert 'id="graphAssemblyComposer"' in html


def test_wizard_live_mode_explains_policy_and_component_scope():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "没有注册独立实时会话后端" in app
    assert "CONTINUOUS_MODE_REQUIRES_WHOLE_UNIFIED_RUNTIME" in app
    assert "SUPERVISED_NATIVE_ONLY_WHOLE_CONTINUOUS" in app


def test_wizard_validation_surfaces_backend_field_errors():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "const validationDetail = el('formValidationSummary')?.textContent?.trim()" in app
    assert "配置未通过校验：" in app
