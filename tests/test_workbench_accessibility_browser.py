"""Exercise disclosure, field errors and modal keyboard behavior in a real browser."""
import os
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest

from sat_sim.api import create_app
from sat_sim.form_schema import capability_form_schema


def test_whole_form_groups_cover_every_field_once():
    schema = capability_form_schema('whole_spacecraft.unified_native.v1')
    paths = [path for section in schema['sections'] for path in section.get('field_paths', [])]
    assert len(paths) == len(set(paths))
    assert set(paths) == {field['path'] for field in schema['fields']}


def test_mobile_disclosures_preserve_controls_catalog_selection_and_focus(tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    executable = os.environ.get('SAT_SIM_BROWSER_EXECUTABLE')
    if not executable:
        pytest.skip('Set SAT_SIM_BROWSER_EXECUTABLE to an installed Chromium browser')
    app = create_app(runs_root=tmp_path/'runs',artifacts_root=tmp_path/'artifacts',
        embedded_worker=False,auth_mode='disabled')
    with TestClient(app) as client, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True,executable_path=executable)
        page = browser.new_page(viewport={'width':390,'height':844})
        def proxy(route):
            request = route.request
            url = urlsplit(request.url)
            response = client.request(request.method,url.path+('?'+url.query if url.query else ''),
                content=request.post_data_buffer,
                headers={key:value for key,value in request.headers.items() if key in ('content-type','accept')})
            route.fulfill(status=response.status_code,body=response.content,
                headers={'content-type':response.headers.get('content-type','application/octet-stream')})
        page.route('https://workbench.test/**',proxy)
        page.goto('https://workbench.test/',wait_until='networkidle')
        page.wait_for_selector('#selectedCapabilityName')
        tools = page.locator('#toggleMobileToolsBtn')
        assert not page.locator('#fontScaleSelect').is_visible()
        tools.focus()
        page.keyboard.press('Enter')
        assert tools.get_attribute('aria-expanded')=='true'
        assert page.locator('#fontScaleSelect').is_visible()
        page.keyboard.press('Enter')
        assert not page.locator('#fontScaleSelect').is_visible()
        catalog = page.locator('#toggleMobileCatalogBtn')
        catalog.click()
        assert page.locator('#capabilitySearch').is_visible()
        page.locator('.capability-card[data-object-id="component.battery"]').click()
        page.wait_for_function("document.querySelector('#selectedCapabilityName').textContent.includes('电池')")
        assert catalog.get_attribute('aria-expanded')=='false'
        assert not page.locator('#capabilitySearch').is_visible()
        assert catalog.evaluate('(node) => node === document.activeElement')
        page.set_viewport_size({'width':1366,'height':768})
        assert page.locator('#fontScaleSelect').is_visible()
        assert page.locator('#capabilitySearch').is_visible()
        browser.close()


def test_disclosure_values_errors_and_modal_keyboard(tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    executable = os.environ.get('SAT_SIM_BROWSER_EXECUTABLE')
    if not executable:
        pytest.skip('Set SAT_SIM_BROWSER_EXECUTABLE to an installed Chromium browser')
    app = create_app(runs_root=tmp_path/'runs', artifacts_root=tmp_path/'artifacts',
                     embedded_worker=False, auth_mode='disabled')
    with TestClient(app) as client, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=executable)
        page = browser.new_page(viewport={'width': 1366, 'height': 768})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def proxy(route):
            request = route.request
            url = urlsplit(request.url)
            response = client.request(request.method, url.path + ('?'+url.query if url.query else ''),
                content=request.post_data_buffer,
                headers={key:value for key,value in request.headers.items() if key in ('content-type','accept')})
            route.fulfill(status=response.status_code, body=response.content,
                          headers={'content-type':response.headers.get('content-type','application/octet-stream')})

        page.route('https://workbench.test/**', proxy)
        page.goto('https://workbench.test/', wait_until='networkidle')
        page.wait_for_selector('.capability-card.active')
        page.keyboard.press('Tab')
        assert page.locator('.skip-link').evaluate('(node) => node === document.activeElement')
        page.keyboard.press('Enter')
        assert page.evaluate('document.activeElement.id') == 'mainContent'
        assert page.locator('#capabilitySearch').get_attribute('aria-label') == '搜索仿真能力'
        form_tab = page.locator('#creatorFormTabButton')
        form_tab.focus()
        page.keyboard.press('End')
        assert page.locator('#creatorTaskSpecTabButton').get_attribute('aria-selected') == 'true'
        page.keyboard.press('ArrowRight')
        assert form_tab.get_attribute('aria-selected') == 'true'
        assert page.locator('.tab[tabindex="0"]').count() == 1
        page.keyboard.press('ArrowRight')
        canvas = page.locator('#graphCanvas:visible, #assemblyCanvas:visible').first
        canvas.wait_for(state='visible')
        canvas.focus()
        assert canvas.evaluate('(node) => getComputedStyle(node).outlineStyle') == 'solid'
        for control in ('themeSelect', 'fontScaleSelect', 'apiTokenInput'):
            field = page.locator('#'+control)
            field.focus()
            assert field.evaluate('(node) => getComputedStyle(node).outlineStyle') == 'solid'
        form_tab.focus()
        page.keyboard.press('Home')
        panels = page.locator('#dynamicForm details.parameter-section')
        assert panels.count() >= 7
        closed = panels.nth(1)
        assert closed.get_attribute('open') is None
        summary = closed.locator(':scope > summary')
        summary.focus()
        page.keyboard.press('Enter')
        assert closed.get_attribute('open') is not None
        # A search rebuilds the form; previously entered data must survive it.
        duration = page.locator('[data-path="simulation.duration_s"]')
        duration.fill('123')
        duration.dispatch_event('change')
        search = page.locator('#formFieldSearch')
        search.fill('orbit_radius_m')
        assert page.locator('[data-path$="orbit_radius_m"]').is_visible()
        search.fill('')
        assert page.locator('[data-path="simulation.duration_s"]').input_value() == '123'
        # Reset has a one-step undo; repeating reset must retain the first edit.
        page.locator('#resetFormBtn').click()
        assert page.locator('[data-path="simulation.duration_s"]').input_value() != '123'
        page.locator('#resetFormBtn').click()
        page.locator('#undoResetFormBtn').click()
        assert page.locator('[data-path="simulation.duration_s"]').input_value() == '123'
        # An undo must not silently overwrite edits made after reset.
        page.locator('#resetFormBtn').click()
        edited = page.locator('[data-path="simulation.duration_s"]')
        edited.fill('77')
        edited.dispatch_event('change')
        page.locator('#undoResetFormBtn').click()
        assert edited.input_value() == '77'
        assert '新改动已保留' in page.locator('#notice').inner_text()
        duration = page.locator('[data-path="simulation.duration_s"]')
        duration.fill('-1')
        duration.dispatch_event('change')
        assert duration.get_attribute('aria-invalid') == 'true'
        assert page.locator('#'+duration.get_attribute('aria-errormessage')).inner_text()
        duration.fill('123')
        duration.dispatch_event('change')
        assert duration.get_attribute('aria-invalid') is None
        # Open through the public control, then verify focus wrapping and restore.
        opener = page.locator('#openModelSettingsBtn')
        opener.click()
        modal = page.locator('#modelSettingsModal')
        assert modal.is_visible()
        controls = modal.locator('button:visible:enabled, input:visible:enabled, select:visible:enabled, textarea:visible:enabled')
        controls.last.focus()
        page.keyboard.press('Tab')
        assert controls.first.evaluate('(node) => node === document.activeElement')
        page.keyboard.press('Shift+Tab')
        assert controls.last.evaluate('(node) => node === document.activeElement')
        page.keyboard.press('Escape')
        assert not modal.is_visible()
        assert opener.evaluate('(node) => node === document.activeElement')
        assert not errors
        browser.close()
