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
    html = Path("src/sat_sim/web/index.html").read_text(encoding="utf-8")
    assert "原生自动恢复：关闭" in html
    assert "异常执行的遥控不会自动重放" in html


def test_both_realtime_modes_expose_a_consistent_current_session_summary():
    html = Path("src/sat_sim/web/index.html").read_text(encoding="utf-8")
    app = Path("src/sat_sim/web/app.js").read_text(encoding="utf-8")
    continuous = Path("src/sat_sim/web/continuous.js").read_text(encoding="utf-8")
    css = Path("src/sat_sim/web/styles.css").read_text(encoding="utf-8")

    assert html.count('class="realtime-current-session"') == 2
    assert 'id="interactiveSelectedSessionSummary"' in html
    assert 'id="continuousSelectedSessionSummary"' in html
    assert "function renderInteractiveSelectedSessionSummary()" in app
    assert "function renderSelectedSessionSummary()" in continuous
    assert "renderInteractiveSelectedSessionSummary();" in app
    assert "renderSelectedSessionSummary();" in continuous
    assert "const orderedSessions = [...state.interactiveSessions].sort" in app
    assert "const orderedSessions = [...sessions].sort" in continuous
    assert "session.session_id" in app
    assert "session.session_id" in continuous
    assert "data-state" in app
    assert "data-state" in continuous
    assert ".realtime-current-session" in css


def test_both_realtime_modes_share_visible_control_labels_and_button_contract():
    html = Path("src/sat_sim/web/index.html").read_text(encoding="utf-8")
    css = Path("src/sat_sim/web/styles.css").read_text(encoding="utf-8")
    for label in ("启动", "暂停", "继续", "单步", "停止并归档"):
        assert f">{label}</button>" in html
    assert html.count("realtime-control-button") >= 10
    assert ".realtime-control-button" in css
    assert 'data-continuous-action="start"' in html
    assert 'data-continuous-action="pause"' in html
    assert 'data-continuous-action="resume"' in html
    assert 'data-continuous-action="step"' in html
    assert 'data-continuous-action="stop"' in html


def test_realtime_creation_is_progressively_disclosed_around_active_work():
    html = Path("src/sat_sim/web/index.html").read_text(encoding="utf-8")
    app = Path("src/sat_sim/web/app.js").read_text(encoding="utf-8")
    continuous = Path("src/sat_sim/web/continuous.js").read_text(encoding="utf-8")
    css = Path("src/sat_sim/web/styles.css").read_text(encoding="utf-8")

    assert 'id="interactiveCreateDetails"' in html
    assert 'id="continuousCreateDetails"' in html
    assert "interactiveCreateDisclosureInitialized" in app
    assert "createDisclosure.open = false" in app
    assert "createDisclosureInitialized" in continuous
    assert "createDisclosure.open = false" in continuous
    assert ".realtime-create-disclosure > summary" in css
    # Advanced continuous native parameters remain available as a separate disclosure.
    assert 'class="continuous-native-config"' in html


def test_realtime_telemetry_and_feedback_use_user_readable_labels_without_losing_raw_evidence():
    app = Path("src/sat_sim/web/app.js").read_text(encoding="utf-8")
    continuous = Path("src/sat_sim/web/continuous.js").read_text(encoding="utf-8")
    assert "function interactiveTelemetryLabel(field)" in app
    assert "姿态指向误差（°）" in app
    assert "电池荷电状态（SOC）" in app
    assert "label.title = name" in app
    assert "function interactiveAckStateLabel(value)" in app
    assert "已排队，等待执行" in app
    assert "title.title = ack.state" in app
    assert "function continuousTelemetryLabel(field)" in continuous
    assert "轨道半径（m）" in continuous


def test_realtime_feedback_explains_telemetry_freshness_and_command_outcomes():
    html = Path("src/sat_sim/web/index.html").read_text(encoding="utf-8")
    app = Path("src/sat_sim/web/app.js").read_text(encoding="utf-8")
    continuous = Path("src/sat_sim/web/continuous.js").read_text(encoding="utf-8")

    assert 'id="interactiveTelemetryFreshness"' in html
    assert "interactiveLastTelemetryReceivedAt[frame.stream] = Date.now()" in app
    assert "最近收到 " in app
    assert "尚未收到遥测" in app
    assert "function interactiveAckStateLabel(value)" in app
    assert "已排队，等待执行" in app
    assert "function continuousAckLabel(ack)" in continuous
    assert "function continuousEventLabel(event)" in continuous
    assert "已拒绝" in app
    assert "指令执行失败" in continuous
    # Raw reason codes remain in the secondary evidence line.
    assert "row.reason_code" in continuous


def test_continuous_engineering_diagnostics_are_preserved_but_progressively_disclosed():
    html = Path("src/sat_sim/web/index.html").read_text(encoding="utf-8")
    assert 'id="continuousEngineeringDetails"' in html
    start = html.index('id="continuousEngineeringDetails"')
    end = html.index("</details>", start)
    block = html[start:end]
    for required in (
        "continuousRecoveryAuditBtn", "continuousEnduranceStart",
        "continuousEnduranceAuditBtn", "continuousWallElapsed",
        "continuousResidentBytes", "continuousDurableFrames",
    ):
        assert required in block
    assert "长稳证据" in block
    assert "原生恢复审计" in block


def test_returning_from_realtime_offers_direct_reentry_without_restarting_sessions():
    app = Path("src/sat_sim/web/app.js").read_text(encoding="utf-8")
    continuous = Path("src/sat_sim/web/continuous.js").read_text(encoding="utf-8")

    assert "返回正在运行的限时实时会话" in app
    assert "返回持续实时会话" in app
    assert "data-wizard-resume" in app
    assert "getSelectedSessionId: () => state.selectedId" in continuous
    assert "不会自动重启或重放遥控" in app
    assert "switchView('interactive')" in app
    assert "switchView('continuous')" in app
