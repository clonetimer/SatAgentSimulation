from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from sat_sim.api import create_app


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(
        runs_root=tmp_path / 'runs',
        artifacts_root=tmp_path / 'artifacts',
        embedded_worker=False,
        auth_mode='disabled',
    ))


def test_default_entry_is_rule_driven_wizard_and_advanced_workbench_is_preserved(tmp_path):
    with _client(tmp_path) as client:
        index = client.get('/').text
        js = client.get('/assets/app.js').text
    assert '<body data-active-view="wizard">' in index
    assert 'id="wizardView"' in index
    assert 'data-view="wizard"' in index
    assert 'data-view="creator"' in index
    assert 'id="creatorView"' in index
    assert 'id="wizardOptions"' in index
    assert 'wizardStart' in js
    assert 'renderSimulationWizard' in js


def test_wizard_exposes_five_rule_based_goals_and_uses_registry_event_catalog(tmp_path):
    with _client(tmp_path) as client:
        index = client.get('/').text
        js = client.get('/assets/app.js').text
    for goal in ('normal_run', 'fault_run', 'history', 'compare', 'advanced'):
        assert goal in js
    assert "effectCatalog('fault')" in js
    assert 'selectCatalogObject(objectId)' in js
    assert 'previewForm()' in js
    assert 'runSimulation()' in js


def test_wizard_has_back_reset_and_schema_driven_runtime_configuration(tmp_path):
    with _client(tmp_path) as client:
        index = client.get('/').text
        js = client.get('/assets/app.js').text
    assert 'id="wizardBackBtn"' in index
    assert 'id="wizardResetBtn"' in index
    assert 'id="wizardConversation"' in index
    assert "simulation.duration_s" in js
    assert "simulation.sample_s" in js
    assert "wizard.validated" in js
