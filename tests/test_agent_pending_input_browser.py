"""Actual browser/API projection; only model generation and execution are fixtures.

Protocol fixtures establish instruction ownership and application, not model
quality, native simulation correctness, or physics acceptance.
"""
import copy
from contextlib import contextmanager
import json
import os
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest

from sat_sim.api import create_app
from sat_sim.canonical_transport import task_spec_json
from sat_sim.form_schema import capability_form_schema
from sat_sim.unified_agent import normalize_form_task_spec


def wait_for_route(page, pending, key):
    # The request event can arrive before Playwright dispatches our route
    # callback. Wait for that boundary explicitly instead of reading an old
    # request from the capture list or sleeping for a presumed callback delay.
    # An exposed binding returns a Promise, which Playwright 1.57 treats as a
    # truthy predicate result. Poll a synchronous boolean instead; each call
    # owns a fresh object so an older binding cannot complete a newer wait.
    page.evaluate('''(key) => {
        window.protocolRouteWait = {key, ready:false, checking:false, error:null};
    }''', key)
    page.wait_for_function('''() => {
        const wait = window.protocolRouteWait;
        if (wait.error) throw new Error(wait.error);
        if (!wait.ready && !wait.checking) {
            wait.checking = true;
            window.protocolRouteReady(wait.key).then(
                ready => { wait.ready = ready === true; wait.checking = false; },
                error => { wait.error = String(error); wait.checking = false; },
            );
        }
        return wait.ready;
    }''')
    return pending.pop(key)


@contextmanager
def workbench(tmp_path, *, delay_projection=False, delay_preflight=False):
    playwright = pytest.importorskip('playwright.sync_api')
    executable = os.environ.get('SAT_SIM_BROWSER_EXECUTABLE')
    if not executable:
        pytest.skip('Set SAT_SIM_BROWSER_EXECUTABLE to installed Chromium')
    app = create_app(runs_root=tmp_path/'runs', artifacts_root=tmp_path/'artifacts',
                     embedded_worker=False, auth_mode='disabled')
    specs = {'source_request_ids':{}, 'validation_exchanges':[]}
    for name, duration in [('accepted', 60), ('updated', 120), ('rejected', 999)]:
        form = copy.deepcopy(capability_form_schema('component.thermal_node.v1')['default_form'])
        form['simulation']['duration_s'] = duration
        specs[name] = normalize_form_task_spec(form)
    natural, runs, forms, pending, errors = [], [], [], {}, []
    with TestClient(app) as client, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=executable)
        try:
            page = browser.new_page(viewport={'width':1366, 'height':900})
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.expose_function('protocolRouteReady', lambda key: key in pending)

            def answer(route, spec, ok=True):
                # Model response is a fixture; obtain a genuine persisted
                # receipt through the real non-LLM parse API for its TaskSpec.
                parsed = client.post('/tasks/parse', json={'input_kind':'task_spec', 'task_spec_json':task_spec_json(spec)})
                assert parsed.status_code == 200 and parsed.json()['ok'], parsed.text
                request_id = parsed.json()['request_id']
                specs['source_request_ids'][spec['simulation']['duration_s']] = request_id
                route.fulfill(status=200, json={'ok':ok, 'request_id':request_id, 'result':{
                    'task_spec':copy.deepcopy(spec), 'task_spec_json':task_spec_json(spec),
                    'planning':None, 'route':{'provider_id':'protocol-only'},
                    'validation':{'errors':[] if ok else [{'message':'Instruction rejected'}]},
                    'guards':{'issues':[]}}})

            def forward(route):
                req = route.request
                url = urlsplit(req.url)
                response = client.request(req.method, url.path+('?'+url.query if url.query else ''),
                    content=req.post_data_buffer,
                    headers={k:v for k,v in req.headers.items() if k in ('content-type', 'accept')})
                if req.method == 'POST' and url.path == '/tasks/validate':
                    specs['validation_exchanges'].append({
                        'request':json.loads(req.post_data), 'response':response.json()})
                route.fulfill(status=response.status_code, body=response.content,
                    headers={'content-type':response.headers.get('content-type', 'application/octet-stream')})

            def proxy(route):
                req = route.request
                url = urlsplit(req.url)
                body = json.loads(req.post_data) if req.post_data else None
                if req.method == 'POST' and url.path == '/tasks/parse':
                    if body.get('input_kind') == 'natural_language':
                        natural.append(body)
                        if len(natural) == 1:
                            answer(route, specs['accepted'])
                        else:
                            pending['generation'] = route
                        return
                    if body.get('input_kind') == 'form':
                        forms.append(body)
                        if delay_preflight:
                            pending['preflight'] = route
                            return
                if (delay_projection and len(natural) > 1 and req.method == 'POST'
                        and url.path.endswith('/project')):
                    pending['projection'] = route
                    return
                if req.method == 'POST' and url.path == '/runs':
                    runs.append(body)
                    route.fulfill(status=503, json={'detail':'Protocol fixture blocks all execution'})
                    return
                forward(route)

            page.route('https://workbench.test/**', proxy)
            page.goto('https://workbench.test/', wait_until='networkidle')
            page.locator('#wizardAdvancedBtn').click()
            page.wait_for_selector('.capability-card.active', state='attached')
            page.locator('#creatorAgentTabButton').click()
            page.locator('#agentRefineCurrent').uncheck()
            page.locator('#agentInput').fill('创建热节点仿真，运行60秒。')
            page.locator('#agentParseBtn').click()
            page.wait_for_function("JSON.parse(document.querySelector('#taskSpecEditor').value || '{}').simulation?.duration_s === 60")
            page.wait_for_function("!document.querySelector('#agentParseBtn').disabled")
            page.locator('#creatorAgentTabButton').click()
            page.locator('#agentRefineCurrent').check()
            yield page, natural, runs, forms, pending, errors, specs, answer, forward
        finally:
            browser.close()


@pytest.mark.parametrize('outcome', ['success', 'reject', 'stale_generation', 'stale_projection', 'transport_error'])
def test_run_applies_latest_pending_instruction_and_keeps_accepted_state_on_failure(tmp_path, outcome):
    with workbench(tmp_path, delay_projection=outcome=='stale_projection') as context:
        page, natural, runs, forms, pending, errors, specs, answer, forward = context
        old_editor = page.locator('#taskSpecEditor').input_value()
        page.locator('#agentInput').fill('将仿真时长改为120秒。')
        assert '未应用' in page.locator('#agentGenerationStatus').inner_text()
        with page.expect_request(lambda req: req.method=='POST' and req.url.endswith('/tasks/parse')) as generation:
            page.locator('#runBtn').click()
        generation_route = wait_for_route(page, pending, 'generation')
        assert not runs
        assert not page.locator('#busyOverlay').is_visible()
        request_body = generation.value.post_data_json
        assert request_body['request_text'] == '将仿真时长改为120秒。'
        assert request_body['base_task_spec']['simulation']['duration_s'] == 60
        if outcome == 'stale_generation':
            page.locator('#agentInput').fill('改为180秒的新指令。')
        if outcome == 'transport_error':
            generation_route.fulfill(status=503, json={'detail':'Generation unavailable'})
        elif outcome == 'reject':
            answer(generation_route, specs['rejected'], ok=False)
        elif outcome == 'stale_projection':
            with page.expect_request(lambda req: req.method=='POST' and req.url.endswith('/project')):
                answer(generation_route, specs['updated'])
            page.locator('#agentInput').fill('改为180秒的新指令。')
            forward(wait_for_route(page, pending, 'projection'))
        else:
            answer(generation_route, specs['updated'])
        page.wait_for_function("!document.querySelector('#agentParseBtn').disabled && !document.querySelector('#runBtn').disabled")
        if outcome == 'success':
            assert len(runs) == 1
            assert runs[0]['task_spec']['simulation']['duration_s'] == 120
            assert runs[0]['task_spec_json'] == task_spec_json(specs['updated'])
            assert runs[0]['source_request_id'] == specs['source_request_ids'][120]
            assert page.locator('#agentInput').input_value() == ''
            assert json.loads(page.locator('#taskSpecEditor').input_value())['simulation']['duration_s'] == 120
        else:
            assert not runs
            assert page.locator('#taskSpecEditor').input_value() == old_editor
            assert page.locator('[data-path="simulation.duration_s"]').input_value() == '60'
            assert page.locator('#agentInput').input_value()
            assert '未应用' in page.locator('#agentGenerationStatus').inner_text()
        assert not errors


def test_blank_rerun_and_manual_edits_use_the_current_configuration(tmp_path):
    with workbench(tmp_path) as context:
        page, natural, runs, forms, pending, errors, specs, answer, forward = context
        page.locator('#agentInput').fill('  ')
        with page.expect_request(lambda req: req.method=='POST' and req.url.endswith('/runs')):
            page.locator('#runBtn').click()
        page.wait_for_function("!document.querySelector('#runBtn').disabled")
        assert len(natural) == 1
        assert runs[0]['task_spec']['simulation']['duration_s'] == 60
        assert runs[0]['source_request_id'] == specs['source_request_ids'][60]
        page.locator('#creatorFormTabButton').click()
        duration = page.locator('[data-path="simulation.duration_s"]')
        duration.fill('90')
        duration.dispatch_event('change')
        page.locator('#creatorAgentTabButton').click()
        assert page.locator('#agentRefineCurrent').is_checked()
        page.locator('#agentInput').fill('将仿真时长改为120秒。')
        with page.expect_request(lambda req: req.method=='POST' and req.url.endswith('/tasks/parse')
                                 and json.loads(req.post_data).get('input_kind')=='natural_language') as generation:
            page.locator('#runBtn').click()
        generation_route = wait_for_route(page, pending, 'generation')
        assert len(forms) == 1
        assert generation.value.post_data_json['base_task_spec']['simulation']['duration_s'] == 90
        assert len(natural) == 2
        answer(generation_route, specs['updated'])
        page.wait_for_function("!document.querySelector('#runBtn').disabled")
        assert runs[-1]['task_spec']['simulation']['duration_s'] == 120
        assert runs[-1]['source_request_id'] == specs['source_request_ids'][120]
        # Invalid manual edits must stop implicit generation and submission.
        duration.fill('-1')
        duration.dispatch_event('change')
        page.locator('#creatorAgentTabButton').click()
        page.locator('#agentInput').fill('继续修改。')
        page.locator('#runBtn').click()
        page.wait_for_function("!document.querySelector('#runBtn').disabled")
        assert len(natural) == 2
        assert len(runs) == 2
        assert duration.input_value() == '-1'
        assert not errors


def test_new_instruction_during_manual_preflight_never_submits_old_text(tmp_path):
    with workbench(tmp_path, delay_preflight=True) as context:
        page, natural, runs, forms, pending, errors, specs, answer, forward = context
        page.locator('#creatorFormTabButton').click()
        duration = page.locator('[data-path="simulation.duration_s"]')
        duration.fill('90')
        duration.dispatch_event('change')
        page.locator('#creatorAgentTabButton').click()
        page.locator('#agentInput').fill('将仿真时长改为120秒。')
        with page.expect_request(lambda req: req.method=='POST' and req.url.endswith('/tasks/parse')):
            page.locator('#runBtn').click()
        page.locator('#agentInput').fill('改为180秒的新指令。')
        forward(wait_for_route(page, pending, 'preflight'))
        page.wait_for_function("!document.querySelector('#agentParseBtn').disabled && !document.querySelector('#runBtn').disabled")
        assert len(natural) == 1
        assert not runs
        assert json.loads(page.locator('#taskSpecEditor').input_value())['simulation']['duration_s'] == 90
        assert page.locator('#agentInput').input_value() == '改为180秒的新指令。'
        assert '未应用' in page.locator('#agentGenerationStatus').inner_text()
        assert not errors


@pytest.mark.parametrize('duration, reuses_receipt', [(60, True), (90, False)])
def test_json_edit_receipt_matches_the_validated_canonical_configuration(tmp_path, duration, reuses_receipt):
    with workbench(tmp_path) as context:
        page, natural, runs, forms, pending, errors, specs, answer, forward = context
        page.locator('#creatorTaskSpecTabButton').click()
        text = page.locator('#taskSpecEditor').input_value()
        changed = text.replace('"duration_s": 60.0', f'"duration_s": {duration}')
        assert changed != text, 'Fixture must edit the accepted canonical document'
        page.locator('#taskSpecEditor').fill(changed)
        assert page.locator('.tab.active').get_attribute('data-tab') == 'taskspec'
        with page.expect_request(lambda req: req.method=='POST' and req.url.endswith('/runs')):
            page.locator('#runBtn').click()
        page.wait_for_function("!document.querySelector('#runBtn').disabled")
        assert len(runs) == 1 and len(natural) == 1
        assert not forms, 'JSON Run must validate the editor, not create a fresh form receipt'
        assert len(specs['validation_exchanges']) == 1
        exchange = specs['validation_exchanges'][0]
        assert exchange['request']['task_spec_json'] == changed
        validated = exchange['response']
        assert validated['ok'] and 'request_id' not in validated
        assert runs[0]['task_spec']['simulation']['duration_s'] == duration
        assert runs[0]['task_spec_json'] == validated['task_spec_json']
        if reuses_receipt:
            # The real validator canonicalizes integer 60 to float 60.0.
            # Its output is byte-identical to the previously accepted task.
            assert validated['task_spec_json'] == text
            assert runs[0]['source_request_id'] == specs['source_request_ids'][60]
        else:
            assert validated['task_spec_json'] != text
            assert 'source_request_id' not in runs[0]
        assert not errors
