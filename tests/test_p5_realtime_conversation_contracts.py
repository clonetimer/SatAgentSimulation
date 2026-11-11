from pathlib import Path


def test_live_modes_explain_distinct_execution_lifecycles_without_merging_controllers():
    js = Path("src/sat_sim/web/app.js").read_text(encoding="utf-8")
    continuous = Path("src/sat_sim/web/continuous.js").read_text(encoding="utf-8")

    assert "限时实时仿真" in js
    assert "持续实时仿真" in js
    assert "到达时长上限后结束并归档" in js
    assert "由用户主动停止并归档" in js
    assert "wizardLiveModeSupport(mode)" in js
    assert "interactiveCreationSupport(capabilityId, continuous)" in js
    assert "async function createInteractiveSession()" in js
    assert "async function interactiveControl" in js
    assert "async function sendInteractiveCommand" in js
    assert "async function createSession" in continuous
    assert "async function control" in continuous
    assert "async function sendCommand" in continuous
    assert "window.SatContinuousConsole = { enter, exit, applyNativeConfiguration, selectSession }" in continuous


def test_realtime_safety_contracts_remain_explicit():
    js = Path("src/sat_sim/web/app.js").read_text(encoding="utf-8")
    continuous = Path("src/sat_sim/web/continuous.js").read_text(encoding="utf-8")

    assert "实时会话仍处于活动状态" in js
    assert "会话不会自动停止" in js
    assert "自动恢复：关闭" in Path("src/sat_sim/web/index.html").read_text(encoding="utf-8")
    assert "不自动重放" in Path("src/sat_sim/web/index.html").read_text(encoding="utf-8")
    assert "automatic" not in continuous.lower() or "recovery" in continuous.lower()
