from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_p7c_c2_exact_run_evidence_navigation_contract(tmp_path):
    from fastapi.testclient import TestClient
    from sat_sim.api import create_app
    with TestClient(create_app(runs_root=tmp_path / "runs", artifacts_root=tmp_path / "artifacts", embedded_worker=False, auth_mode="disabled")) as client:
        html = client.get("/").text
        js = client.get("/assets/app.js").text
    assert 'id="runEvidencePanel"' in html
    assert 'data-p7-context aria-label="跨工作区共享任务上下文"' in html[html.index('id="creatorView"'):]
    assert "async function openExperimentRunEvidence(experimentId, variantRow)" in js
    assert "loadRun(runId)" in js
    assert "switchView('diagnostics')" in js
    assert "renderRunEvidencePanel()" in js
    assert "下载此 Run 诊断包" in js
    assert "/diagnostics/bundle?run_id=" in js
    assert "查看运行证据" in js
