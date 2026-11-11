from pathlib import Path


def test_run_results_panel_is_reused_inside_wizard_and_restored_for_advanced_workbench():
    html = Path("src/sat_sim/web/index.html").read_text(encoding="utf-8")
    js = Path("src/sat_sim/web/app.js").read_text(encoding="utf-8")

    # There must be one result panel and one host; the UI must not clone
    # result widgets with duplicate IDs when moving between workspaces.
    assert html.count('id="creatorRunPanel"') == 1
    assert html.count('id="wizardRunResultsHost"') == 1
    assert "move('creatorRunPanel', 'wizardRunResultsHost')" in js
    assert "el('creatorRunPanel')?.parentElement !== creator" in js
    assert "creator.appendChild(el('creatorRunPanel'))" in js
    assert "if (guided) wizardAttachPanels();" in js
    assert "else if (name === 'creator') wizardRestoreAdvancedPanels();" in js


def test_conversational_run_results_use_existing_execution_and_result_sources():
    js = Path("src/sat_sim/web/app.js").read_text(encoding="utf-8")
    html = Path("src/sat_sim/web/index.html").read_text(encoding="utf-8")

    assert "async function pollRunExecution(runId)" in js
    assert "api(`/runs/${encodeURIComponent(runId)}/execution`)" in js
    assert "async function loadRun(runId, options = {})" in js
    for endpoint in (
        "/metrics", "/events", "/plots", "/telemetry?limit=2000",
        "/artifacts", "/dataset", "/fmea", "/fault-traceability",
    ):
        assert endpoint in js
    for panel_id in (
        "activeRunCard", "metricGrid", "validationCard", "telemetryChart",
        "resultEventList", "traceabilityList", "artifactList",
    ):
        assert html.count(f'id="{panel_id}"') == 1
    assert "wizardRunResultsHost" in js
    assert "Run Bundle" in Path(
        "docs/superpowers/plans/2026-11-11-p4-run-results-closure.md"
    ).read_text(encoding="utf-8")
