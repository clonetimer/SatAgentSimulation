from __future__ import annotations

from fastapi.testclient import TestClient

from sat_sim.api import create_app


def test_interactive_workbench_assets_are_feature_complete(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SAT_SIM_INTERACTIVE_ENABLED", "1")
    app = create_app(
        artifacts_root=tmp_path / "artifacts",
        runs_root=tmp_path / "runs",
        embedded_worker=False,
    )
    with TestClient(app) as client:
        index = client.get("/").text
        css = client.get("/assets/styles.css").text
        javascript = client.get("/assets/app.js").text
        catalog = client.get("/interactive/commands/catalog").json()

    assert 'id="interactiveNavButton"' in index
    assert 'id="interactiveView"' in index
    assert 'id="interactiveTelemetryChart"' in index
    assert 'id="interactiveTelemetryObject"' in index
    assert 'id="interactiveCommandTarget"' in index
    assert 'id="interactiveCommandForm"' in index
    assert 'id="interactiveAckList"' in index
    assert '<option value="whole_spacecraft.unified_native.v1" selected>整星（推荐）</option>' in index
    assert '高级：分系统独立调试' in index
    assert 'body[data-active-view="interactive"]' in css
    assert ".interactive-status-strip" in css
    assert "@media (max-width: 760px)" in css
    assert "new WebSocket" in javascript
    assert "sat-auth." in javascript
    assert "/interactive/sessions" in javascript
    assert "targets: ['whole_spacecraft', 'subsystem.adcs', 'subsystem.eps', 'subsystem.comm_data']" in javascript
    assert "telemetryGroups" in javascript
    assert "renderInteractiveCommandTargets" in javascript
    assert "confirm('确认停止当前实时会话并归档？')" in javascript
    assert len(catalog["commands"]) == 10
    assert all(command["parameter_schema"]["additionalProperties"] is False for command in catalog["commands"])


def test_disabled_feature_keeps_realtime_navigation_hidden(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("SAT_SIM_INTERACTIVE_ENABLED", raising=False)
    app = create_app(
        artifacts_root=tmp_path / "artifacts",
        runs_root=tmp_path / "runs",
        embedded_worker=False,
    )
    with TestClient(app) as client:
        assert client.get("/interactive/sessions").status_code == 404
        index = client.get("/").text
    assert 'id="interactiveNavButton" class="nav-button hidden"' in index
