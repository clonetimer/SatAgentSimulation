"""Browser control-flow evidence only; protocol responses are not AI accuracy."""
import copy
import json
import os
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest

from sat_sim.api import create_app
from sat_sim.form_schema import capability_form_schema
from sat_sim.unified_agent import normalize_form_task_spec


@pytest.mark.parametrize('restore_defaults', [False, True])
def test_manual_form_changes_remain_the_base_of_next_agent_turn(tmp_path, restore_defaults):
    playwright = pytest.importorskip('playwright.sync_api')
    executable = os.environ.get('SAT_SIM_BROWSER_EXECUTABLE')
    if not executable:
        pytest.skip('Set SAT_SIM_BROWSER_EXECUTABLE to an installed Chromium browser')
    app = create_app(runs_root=tmp_path/'runs', artifacts_root=tmp_path/'artifacts',
                     embedded_worker=False, auth_mode='disabled')
    form = capability_form_schema('component.thermal_node.v1')['default_form']
    form['parameters']['values']['power_w'] = 37
    original = normalize_form_task_spec(form)
    natural_requests, form_requests, errors = [], [], []
    with TestClient(app) as client, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=executable)
        page = browser.new_page(viewport={'width':1366, 'height':768})
        page.on('pageerror', lambda error: errors.append(str(error)))

        def proxy(route):
            request = route.request
            url = urlsplit(request.url)
            if request.method == 'POST' and url.path == '/tasks/parse':
                body = json.loads(request.post_data)
                if body.get('input_kind') == 'natural_language':
                    natural_requests.append(body)
                    spec = copy.deepcopy(body.get('base_task_spec') or original)
                    if len(natural_requests) > 1:
                        # A protocol response isolates UI request control flow.
                        next_form = client.post('/forms/capabilities/component.thermal_node.v1/project',
                            json={'task_spec':spec}).json()['projection']['form_data']
                        next_form['parameters']['values']['power_w'] = 42
                        spec = normalize_form_task_spec(next_form)
                    route.fulfill(status=200, json={'ok':True, 'result':{
                        'task_spec':spec, 'planning':None, 'route':{'provider_id':'protocol-test-only'}}})
                    return
                if body.get('input_kind') == 'form':
                    form_requests.append(body)
            response = client.request(request.method, url.path + ('?'+url.query if url.query else ''),
                content=request.post_data_buffer,
                headers={key:value for key,value in request.headers.items() if key in ('content-type','accept')})
            route.fulfill(status=response.status_code, body=response.content,
                headers={'content-type':response.headers.get('content-type','application/octet-stream')})

        page.route('https://workbench.test/**', proxy)
        page.goto('https://workbench.test/', wait_until='networkidle')
        page.wait_for_selector('.capability-card.active')
        page.locator('#creatorAgentTabButton').click()
        page.locator('#agentInput').fill('创建热节点仿真，功率37W。')
        page.locator('#agentParseBtn').click()
        page.wait_for_function("document.querySelector('[data-path=\"parameters.values.power_w\"]')?.value === '37'")
        assert natural_requests[0]['base_task_spec'] is None
        duration = page.locator('[data-path="simulation.duration_s"]')
        expected_duration = 60 if restore_defaults else 123
        expected_power = 10 if restore_defaults else 37
        duration.fill(str(expected_duration))
        duration.dispatch_event('change')
        if restore_defaults:
            # Restoring exact default values is still an edit of this task.
            power = page.locator('[data-path="parameters.values.power_w"]')
            power.fill('10')
            power.dispatch_event('change')
        page.locator('#creatorAgentTabButton').click()
        assert page.locator('#agentRefineCurrent').is_checked()
        page.locator('#agentInput').fill('将功率改为42W，保留其他配置。')
        page.locator('#agentParseBtn').click()
        page.wait_for_function("document.querySelector('[data-path=\"parameters.values.power_w\"]')?.value === '42'")
        base = natural_requests[-1]['base_task_spec']
        proof = {'scope':'Protocol UI control flow only, not model quality',
            'natural_requests':natural_requests, 'form_requests':form_requests,
            'result_duration':duration.input_value(), 'page_errors':errors}
        (tmp_path/'agent-refinement-proof.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
        assert base is not None, proof
        assert base['simulation']['duration_s'] == expected_duration
        # Canonical parameters are source records; project through the real API.
        base_form = client.post('/forms/capabilities/component.thermal_node.v1/project',
            json={'task_spec':base}).json()['projection']['form_data']
        assert base_form['parameters']['values']['power_w'] == expected_power
        assert duration.input_value() == str(expected_duration)
        assert len(form_requests) == 1
        # Invalid manual edits must block the next model call, not silently create.
        duration.fill('-1')
        duration.dispatch_event('change')
        page.locator('#creatorAgentTabButton').click()
        page.locator('#agentInput').fill('继续修改功率。')
        page.locator('#agentParseBtn').click()
        page.wait_for_function("document.querySelector('#notice')?.className.includes('error')")
        assert len(natural_requests) == 2
        assert duration.input_value() == '-1'
        assert not errors
        proof['invalid_edit_preserved'] = duration.input_value() == '-1'
        proof['invalid_edit_blocked_model_request'] = len(natural_requests) == 2
        (tmp_path/'agent-refinement-proof.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
        browser.close()


def test_previewed_task_restored_to_default_values_still_has_refinement_context(tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    executable = os.environ.get('SAT_SIM_BROWSER_EXECUTABLE')
    if not executable:
        pytest.skip('Set SAT_SIM_BROWSER_EXECUTABLE to an installed Chromium browser')
    app = create_app(runs_root=tmp_path/'runs', artifacts_root=tmp_path/'artifacts',
                     embedded_worker=False, auth_mode='disabled')
    natural_requests = []
    with TestClient(app) as client, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=executable)
        page = browser.new_page(viewport={'width':1366, 'height':768})

        def proxy(route):
            request = route.request
            url = urlsplit(request.url)
            if request.method == 'POST' and url.path == '/tasks/parse':
                body = json.loads(request.post_data)
                if body.get('input_kind') == 'natural_language':
                    natural_requests.append(body)
                    route.fulfill(status=200, json={'ok':False, 'result':{
                        'task_spec':None, 'planning':None, 'validation':{'errors':[]}, 'guards':{'issues':[]}}})
                    return
            response = client.request(request.method, url.path + ('?'+url.query if url.query else ''),
                content=request.post_data_buffer,
                headers={key:value for key,value in request.headers.items() if key in ('content-type','accept')})
            route.fulfill(status=response.status_code, body=response.content,
                headers={'content-type':response.headers.get('content-type','application/octet-stream')})

        page.route('https://workbench.test/**', proxy)
        page.goto('https://workbench.test/', wait_until='networkidle')
        page.wait_for_selector('.capability-card.active')
        duration = page.locator('[data-path="simulation.duration_s"]')
        default_duration = duration.input_value()
        page.locator('#previewBtn').click()
        page.wait_for_function("document.querySelector('#notice')?.className.includes('success')")
        duration.fill(str(float(default_duration)+1))
        duration.dispatch_event('change')
        duration.fill(default_duration)
        duration.dispatch_event('change')
        page.locator('#creatorAgentTabButton').click()
        page.locator('#agentInput').fill('保留当前配置，只补充输出。')
        page.locator('#agentParseBtn').click()
        page.wait_for_function("document.querySelector('#notice')?.className.includes('error')")
        base = natural_requests[-1]['base_task_spec']
        proof = {'scope':'Protocol request control flow, not AI accuracy',
            'default_duration':default_duration, 'natural_requests':natural_requests}
        (tmp_path/'default-context-proof.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
        browser.close()
        assert base is not None, proof
        assert base['simulation']['duration_s'] == float(default_duration)
