"""Exercise FuelTank forms in Chromium through the actual workbench API."""
import json
import os
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from sat_sim.api import create_app


CAPABILITY = "component.fuel_tank.v1"
FORM_PATH = f"/forms/capabilities/{CAPABILITY}"
NATIVE_TEMPLATE = "component_fuel_tank_basilisk_native"
FIELD_PREFIX = "propulsion.fuel_tank."
PRESSURE_QOI = FIELD_PREFIX + "final_pressure_pa"
PRESSURE_PLOT = FIELD_PREFIX + "pressure_pa"
MASS_RATE_PLOT = FIELD_PREFIX + "mass_rate_kg_s"
MAX_MASS_PLOT = FIELD_PREFIX + "max_mass_kg"


@pytest.fixture
def workbench(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("SAT_SIM_BROWSER_EXECUTABLE")
    if not executable:
        pytest.skip("Set SAT_SIM_BROWSER_EXECUTABLE to an installed Chromium browser")
    app = create_app(
        runs_root=tmp_path / "runs", artifacts_root=tmp_path / "artifacts",
        embedded_worker=False, auth_mode="disabled",
    )
    with TestClient(app) as client, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=executable)
        try:
            page = browser.new_page(viewport={"width": 1366, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def forward(route):
                request = route.request
                url = urlsplit(request.url)
                response = client.request(
                    request.method, url.path + ("?" + url.query if url.query else ""),
                    content=request.post_data_buffer,
                    headers={key: value for key, value in request.headers.items()
                             if key in ("content-type", "accept")},
                )
                route.fulfill(
                    status=response.status_code, body=response.content,
                    headers={"content-type": response.headers.get("content-type", "application/octet-stream")},
                )

            page.route("https://workbench.test/**", forward)
            page.goto("https://workbench.test/", wait_until="networkidle")
            page.wait_for_selector(".capability-card.active", state="attached")
            advanced = page.locator("#wizardAdvancedBtn")
            if advanced.is_visible():
                advanced.click()
            page.locator('.capability-card[data-object-id="component.fuel_tank"]').click()
            page.locator("#creatorFormTabButton").click()
            page.locator("#showAdvancedFields").check()
            if page.locator("#compactOutputs").get_attribute("open") is None:
                page.locator("#compactOutputs > summary").click()
            yield page, playwright.expect
            assert not errors
        finally:
            browser.close()


def _parameter(page, name):
    return page.locator(f'#dynamicForm [data-path="parameters.values.{name}"]')


def _output(page, container_id, field):
    return page.locator(f"#{container_id} label").filter(
        has=page.get_by_text(field, exact=True),
    ).locator('input[type="checkbox"]')


def test_switching_backends_preserves_shared_mass_and_prunes_pressure(workbench):
    page, expect = workbench
    backend = _parameter(page, "physics_backend")
    mass = _parameter(page, "initial_mass_kg")
    pressure = _parameter(page, "full_pressure_pa")
    dry_pressure = _parameter(page, "dry_pressure_pa")
    pressure_qoi = _output(page, "qoiOptions", PRESSURE_QOI)
    pressure_plot = _output(page, "plotOptions", PRESSURE_PLOT)
    mass_rate_plot = _output(page, "plotOptions", MASS_RATE_PLOT)
    max_mass_plot = _output(page, "plotOptions", MAX_MASS_PLOT)
    notice = page.locator("#notice")
    editor = page.locator("#taskSpecEditor")
    guide = page.locator("#singleRunGuide")

    expect(backend).to_have_value("project_reference")
    expect(pressure_qoi).to_be_checked()
    expect(pressure_plot).to_be_checked()
    expect(mass_rate_plot).to_have_count(0)
    mass.fill("1.75")
    pressure.fill("2400000")
    page.locator("#previewBtn").click()
    expect(notice).to_contain_text("校验通过")
    expect(guide).to_have_attribute("data-stage", "validated")
    assert json.loads(editor.input_value())["parameters"]["values"]["full_pressure_pa"] == 2400000

    backend.select_option("basilisk_native")
    expect(pressure).to_have_count(0)
    expect(dry_pressure).to_have_count(0)
    expect(pressure_qoi).to_have_count(0)
    expect(pressure_plot).to_have_count(0)
    expect(mass_rate_plot).to_be_visible()
    expect(max_mass_plot).to_be_visible()
    expect(mass).to_have_value("1.75")
    expect(notice).to_be_visible()
    expect(notice).to_contain_text("已移除")
    for removed in ("full_pressure_pa", "dry_pressure_pa", PRESSURE_QOI, PRESSURE_PLOT):
        expect(notice).to_contain_text(removed)
    expect(guide).to_have_attribute("data-stage", "configure")
    expect(editor).to_have_value("")

    mass_rate_plot.check()
    with page.expect_response(lambda response: urlsplit(response.url).path == "/tasks/parse") as preview:
        page.locator("#previewBtn").click()
    payload = preview.value.json()
    assert payload["ok"], payload
    spec = payload["result"]["task_spec"]
    assert spec["parameters"]["values"]["initial_mass_kg"] == 1.75
    assert spec["parameters"]["values"]["physics_backend"] == "basilisk_native"
    assert not {"full_pressure_pa", "dry_pressure_pa"} & spec["parameters"]["values"].keys()
    assert PRESSURE_QOI not in spec["outputs"]["qoi"]
    assert PRESSURE_PLOT not in spec["outputs"]["plots"]
    assert MASS_RATE_PLOT in spec["outputs"]["plots"]
    expect(notice).to_contain_text("校验通过")

    backend.select_option("project_reference")
    expect(pressure).to_have_value("2500000")
    expect(dry_pressure).to_have_value("150000")
    expect(pressure_qoi).to_be_visible()
    expect(pressure_plot).to_be_visible()
    expect(mass_rate_plot).to_have_count(0)
    expect(max_mass_plot).to_have_count(0)
    expect(mass).to_have_value("1.75")
    expect(notice).to_contain_text(MASS_RATE_PLOT)
    expect(editor).to_have_value("")


def test_applying_native_template_replaces_the_reference_form_schema(workbench):
    page, expect = workbench
    backend = _parameter(page, "physics_backend")
    expect(backend).to_have_value("project_reference")
    page.locator("#creatorMoreActions > summary").click()
    page.locator("#scenarioTemplateSelect").select_option(NATIVE_TEMPLATE)
    with page.expect_response(lambda response: urlsplit(response.url).path == FORM_PATH + "/project") as projection:
        page.locator("#applyScenarioTemplateBtn").click()

    payload = projection.value.json()
    assert payload["ok"], payload
    projected = payload["projection"]
    assert projected["projection_is_lossless"] is True
    assert projected["form_schema"]["default_form"]["parameters"]["values"]["physics_backend"] == "basilisk_native"
    expect(backend).to_have_value("basilisk_native")
    expect(_parameter(page, "full_pressure_pa")).to_have_count(0)
    expect(_parameter(page, "dry_pressure_pa")).to_have_count(0)
    expect(_output(page, "qoiOptions", PRESSURE_QOI)).to_have_count(0)
    expect(_output(page, "plotOptions", PRESSURE_PLOT)).to_have_count(0)
    expect(_output(page, "plotOptions", MASS_RATE_PLOT)).to_be_checked()
    expect(_output(page, "plotOptions", MAX_MASS_PLOT)).to_be_checked()
