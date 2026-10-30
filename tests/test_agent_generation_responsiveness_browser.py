"""Delayed protocol requests verify UI behavior, not model accuracy or physics."""
import copy
import json
import os
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest

from sat_sim.api import create_app
from sat_sim.form_schema import capability_form_schema
from sat_sim.unified_agent import normalize_form_task_spec


@pytest.mark.parametrize('change_during', ['none', 'generation', 'projection', 'error'])
def test_pending_generation_keeps_controls_usable_and_preserves_newer_edits(tmp_path, change_during):
    playwright = pytest.importorskip('playwright.sync_api')
    executable = os.environ.get('SAT_SIM_BROWSER_EXECUTABLE')
    if not executable:
        pytest.skip('Set SAT_SIM_BROWSER_EXECUTABLE to installed Chromium')
    app = create_app(runs_root=tmp_path/'runs', artifacts_root=tmp_path/'artifacts',
                     embedded_worker=False, auth_mode='disabled')
    form = capability_form_schema('component.thermal_node.v1')['default_form']
    form['parameters']['values']['power_w'] = 37.
    spec = normalize_form_task_spec(form)
    pending, calls, errors = {}, [], []
    with TestClient(app) as client, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=executable)
        page = browser.new_page(viewport={'width':1366, 'height':900})
        page.on('pageerror', lambda error: errors.append(str(error)))

        def proxy(route):
            request = route.request
            url = urlsplit(request.url)
            if request.method == 'POST' and url.path == '/tasks/parse':
                body = json.loads(request.post_data)
                if body.get('input_kind') == 'natural_language':
                    calls.append(body)
                    pending['generation'] = route
                    return
            if (change_during == 'projection' and request.method == 'POST'
                    and url.path.endswith('/project')):
                pending['projection'] = route
                return
            response = client.request(request.method, url.path + ('?'+url.query if url.query else ''),
                content=request.post_data_buffer,
                headers={k:v for k,v in request.headers.items() if k in ('content-type','accept')})
            route.fulfill(status=response.status_code, body=response.content,
                headers={'content-type':response.headers.get('content-type','application/octet-stream')})

        page.route('https://workbench.test/**', proxy)
        page.goto('https://workbench.test/', wait_until='networkidle')
        page.wait_for_selector('.capability-card.active')
        initial_editor = page.locator('#taskSpecEditor').input_value()
        page.locator('#creatorAgentTabButton').click()
        page.locator('#agentRefineCurrent').uncheck()
        page.locator('#agentInput').fill('Create a thermal node at 37W.')
        with page.expect_request(lambda r: r.method == 'POST' and r.url.endswith('/tasks/parse')):
            page.locator('#agentParseBtn').click()
        assert page.locator('#agentParseBtn').is_disabled()
        assert page.locator('#agentParseBtn').get_attribute('aria-busy') == 'true'
        assert not page.locator('#busyOverlay').is_visible()
        # Use pointer/keyboard controls; an overlay cannot intercept these.
        font = page.locator('#fontScaleSelect')
        original = font.input_value()
        font.click()
        font.press('ArrowDown')
        font.press('Enter')
        assert font.input_value() != original
        assert page.evaluate('document.documentElement.dataset.fontScale') == font.input_value()
        font.select_option(original)
        assert len(calls) == 1
        if change_during == 'generation':
            page.locator('#agentInput').fill('My newer unsent instruction')
        if change_during == 'error':
            pending['generation'].fulfill(status=503, json={'detail':'test service unavailable'})
        elif change_during == 'projection':
            with page.expect_request(lambda r: r.method == 'POST' and r.url.endswith('/project')):
                pending['generation'].fulfill(status=200, json={'ok':True, 'result':{
                    'task_spec':copy.deepcopy(spec), 'planning':None, 'route':{'provider_id':'protocol-only'}}})
        else:
            pending['generation'].fulfill(status=200, json={'ok':True, 'result':{
                'task_spec':copy.deepcopy(spec), 'planning':None, 'route':{'provider_id':'protocol-only'}}})
        if change_during == 'projection':
            # The editor remains available while the response is projected.
            page.locator('#agentInput').fill('Edited while form projection was pending')
            route = pending['projection']
            response = client.post(urlsplit(route.request.url).path, json=json.loads(route.request.post_data))
            route.fulfill(status=response.status_code, json=response.json())
        page.wait_for_function("!document.querySelector('#agentParseBtn').disabled")
        assert page.locator('#agentParseBtn').get_attribute('aria-busy') is None
        assert page.locator('#agentGenerationStatus').inner_text() == ''
        if change_during == 'none':
            assert page.locator('[data-path="parameters.values.power_w"]').input_value() == '37'
        elif change_during in {'generation','projection'}:
            assert page.locator('#agentInput').input_value() != ''
            assert 'warning' in page.locator('#notice').get_attribute('class')
            assert page.locator('#taskSpecEditor').input_value() == initial_editor
        else:
            assert 'error' in page.locator('#notice').get_attribute('class')
            assert page.locator('#agentInput').input_value() != ''
        assert not errors
        (tmp_path/'responsiveness.json').write_text(json.dumps({
            'scope':'Delayed HTTP protocol control flow only', 'scenario':change_during,
            'requests':len(calls), 'page_errors':errors}),encoding='utf-8')
        browser.close()
