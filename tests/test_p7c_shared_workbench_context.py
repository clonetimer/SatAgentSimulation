from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_p7c_shared_context_is_mounted_in_all_specialist_workspaces(tmp_path):
    from fastapi.testclient import TestClient
    from sat_sim.api import create_app
    with TestClient(create_app(runs_root=tmp_path / "runs", artifacts_root=tmp_path / "artifacts", embedded_worker=False, auth_mode="disabled")) as client:
        html = client.get("/").text
        js = client.get("/assets/app.js").text
    for view in ("experimentCenterView", "goalRunView", "runCenterView", "diagnosticView"):
        section = html[html.index('id="' + view + '"'):]
        assert 'data-p7-context' in section[:3500], view
    for marker in ("sharedWorkbenchContext", "updateSharedWorkbenchContext", "renderSharedWorkbenchContext", "returnToSharedContextSource", "noteSharedContextTransition", "activeTaskVersion", "bundleId"):
        assert marker in js


def test_p7c_context_source_and_references_are_preserved_across_tool_switches(tmp_path):
    from fastapi.testclient import TestClient
    from sat_sim.api import create_app
    with TestClient(create_app(runs_root=tmp_path / "runs", artifacts_root=tmp_path / "artifacts", embedded_worker=False, auth_mode="disabled")) as client:
        js = client.get("/assets/app.js").text
    assert "if(!SHARED_CONTEXT_VIEWS.has(current)&&!state.sharedWorkbenchContext?.sourceView)" in js
    assert "sourceView:null,sourceLabel:null" in js
    assert "recordContext.task_version" in js
    assert "updateSharedWorkbenchContext({ experimentId" in js
