from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_p7b_candidate_preview_is_explicit_and_non_destructive():
    index = (ROOT / "src/sat_sim/web/index.html").read_text(encoding="utf-8")
    app = (ROOT / "src/sat_sim/web/app.js").read_text(encoding="utf-8")
    assert 'id="wizardAgentCandidatePreview"' in index
    assert "生成候选预览" in index
    assert "wizardApplyAgentCandidateBtn" in app
    assert "wizardApplyAgentCandidateGraphBtn" in app
    assert "wizardDiscardAgentCandidateBtn" in app
    assert "parseNaturalLanguage({ previewOnly: true })" in app
    assert "projectTaskSpecToForm(generatedTaskSpec" in app
    assert "state.agentCandidate = { taskSpec:" in app
    assert "taskSpecFieldDiff" in app
    assert "GRAPH_FLOW_ORDER.map" in app
    assert "此处为预览，不代表服务端图校验已通过" in app
    assert "openCurrentConfigurationInGraph('已确认的 Agent 候选')" in app


def test_p7b_preview_does_not_replace_graph_compile_or_run_contracts():
    app = (ROOT / "src/sat_sim/web/app.js").read_text(encoding="utf-8")
    assert "api('/tasks/compile-graph'" in app
    assert "api('/tasks/parse'" in app
    assert "async function applyAgentCandidate(openGraph = false)" in app
    assert "尚未执行图形正式校验或仿真" in app
