"""Real Chromium exercises thermal contracts through the existing Workbench."""
import json
from urllib.parse import urlsplit

import pytest

from test_fuel_tank_backend_browser import workbench, _output, _parameter  # Shared actual-API browser fixture.


PREFIX = "thermal.node."


@pytest.fixture
def thermal_workbench(workbench):
    page, expect = workbench
    page.locator('.capability-card[data-object-id="component.thermal_node"]').click()
    page.locator("#creatorFormTabButton").click()
    page.locator("#showAdvancedFields").check()
    if page.locator("#compactOutputs").get_attribute("open") is None:
        page.locator("#compactOutputs > summary").click()
    return page, expect


def test_switching_thermal_backend_preserves_temperature_and_hides_unsupported_physics(thermal_workbench):
    page, expect = thermal_workbench
    backend = _parameter(page, "physics_backend")
    initial = _parameter(page, "initial_temp_k")
    expect(backend).to_have_value("project_reference")
    expect(_parameter(page, "ambient_k")).to_be_visible()
    expect(_parameter(page, "surface_area_m2")).to_have_count(0)
    initial.fill("310")
    page.locator("#previewBtn").click()
    expect(page.locator("#singleRunGuide")).to_have_attribute("data-stage", "validated")
    backend.select_option("basilisk_native")
    expect(initial).to_have_value("310")
    expect(_parameter(page, "ambient_k")).to_have_count(0)
    expect(_parameter(page, "tau_s")).to_have_count(0)
    expect(_parameter(page, "surface_area_m2")).to_have_value("0.01")
    expect(_output(page, "plotOptions", PREFIX + "effective_tau_s")).to_have_count(0)
    expect(_output(page, "plotOptions", PREFIX + "measured_temp_k")).to_have_count(0)
    expect(_output(page, "plotOptions", PREFIX + "temp_c")).to_be_visible()
    expect(page.locator("#notice")).to_contain_text("已移除")
    expect(page.locator("#singleRunGuide")).to_have_attribute("data-stage", "configure")
    expect(page.locator("#taskSpecEditor")).to_have_value("")
    with page.expect_response(lambda response: urlsplit(response.url).path == "/tasks/parse") as preview:
        page.locator("#previewBtn").click()
    payload = preview.value.json()
    assert payload["ok"], payload
    spec = payload["result"]["task_spec"]
    assert spec["parameters"]["values"]["initial_temp_k"] == 310
    assert spec["parameters"]["values"]["physics_backend"] == "basilisk_native"
    assert "ambient_k" not in spec["parameters"]["values"]
    backend.select_option("project_reference")
    expect(initial).to_have_value("310")
    expect(_parameter(page, "ambient_k")).to_have_value("300")
    expect(_parameter(page, "surface_area_m2")).to_have_count(0)
    expect(_output(page, "plotOptions", PREFIX + "input_power_w")).to_have_count(0)


def test_native_thermal_template_projects_native_fields_and_keeps_empty_plots(thermal_workbench):
    page, expect = thermal_workbench
    page.locator("#creatorMoreActions > summary").click()
    page.locator("#scenarioTemplateSelect").select_option("component_thermal_node_basilisk_native")
    with page.expect_response(lambda response: urlsplit(response.url).path.endswith("component.thermal_node.v1/project")) as projection:
        page.locator("#applyScenarioTemplateBtn").click()
    assert projection.value.json()["projection"]["projection_is_lossless"] is True
    expect(_parameter(page, "physics_backend")).to_have_value("basilisk_native")
    expect(_parameter(page, "normal_b")).to_be_visible()
    expect(_parameter(page, "ambient_k")).to_have_count(0)
    expect(_output(page, "plotOptions", PREFIX + "temp_k")).to_be_checked()
    for checkbox in page.locator('#plotOptions input[type="checkbox"]').all():
        checkbox.uncheck()
    _parameter(page, "physics_backend").select_option("project_reference")
    _parameter(page, "physics_backend").select_option("basilisk_native")
    expect(page.locator('#plotOptions input[type="checkbox"]:checked')).to_have_count(0)
    with page.expect_response(lambda response: urlsplit(response.url).path == "/tasks/parse") as preview:
        page.locator("#previewBtn").click()
    payload = preview.value.json()
    assert payload["ok"], payload
    assert payload["result"]["task_spec"]["outputs"]["plots"] == []
    assert json.loads(page.locator("#taskSpecEditor").input_value())["parameters"]["values"]["physics_backend"] == "basilisk_native"
