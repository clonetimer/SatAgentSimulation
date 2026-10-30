"""A delayed real CPU ZIP must not download after a newer task is applied.

The Agent reply is an explicitly mocked protocol fixture. TestClient performs
real validation, form projection, graph compilation and ZIP export; no model
service, GPU inference, simulation Run or native execution is requested.
"""
import copy
import hashlib
import io
import json
import os
import time
from urllib.parse import urlsplit
import zipfile

from fastapi.testclient import TestClient
import pytest

from sat_sim.api import create_app
from sat_sim.canonical_transport import task_spec_json
from sat_sim.form_schema import capability_form_schema
from sat_sim.task_models import canonicalize_task_spec
from sat_sim.unified_agent import normalize_form_task_spec


def _thermal_spec(power_w):
    form = copy.deepcopy(capability_form_schema('component.thermal_node.v1')['default_form'])
    form['parameters']['values']['power_w'] = power_w
    spec = canonicalize_task_spec(normalize_form_task_spec(form))
    # The same task ID ensures a mere ID comparison cannot hide value drift.
    spec['task']['id'] = 'program-export-snapshot'
    spec['task']['name'] = 'Program export snapshot protocol fixture'
    return spec


def _power(spec):
    return spec['parameters']['values']['power_w']


def test_program_zip_response_cannot_download_after_new_agent_task_is_applied(tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    executable = os.environ.get('SAT_SIM_BROWSER_EXECUTABLE')
    if not executable:
        pytest.skip('Set SAT_SIM_BROWSER_EXECUTABLE to installed Chromium')

    app = create_app(runs_root=tmp_path/'runs', artifacts_root=tmp_path/'artifacts',
                     embedded_worker=False, auth_mode='disabled')
    a, b = _thermal_spec(37.), _thermal_spec(57.)
    pending, requests, downloads, errors = {}, [], [], []
    facts = {'scope': 'Mock Agent plus delayed real TestClient CPU HTTP/ZIP protocol; not model accuracy or physics.',
             'model_calls': 0, 'simulation_run_requests': 0, 'checkpoints': {}}

    def dump(name, value):
        (tmp_path/name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

    def fulfill(route, response):
        route.fulfill(status=response.status_code, body=response.content, headers={
            key: value for key, value in response.headers.items()
            if key in ('content-type', 'content-disposition', 'x-sat-sim-program-kind')})

    with TestClient(app) as client, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=executable, args=['--disable-gpu'])
        page = browser.new_page(viewport={'width':1366, 'height':900}, accept_downloads=True)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('download', lambda download: downloads.append(download))

        def proxy(route):
            request = route.request
            url = urlsplit(request.url)
            body = json.loads(request.post_data) if request.post_data else None
            requests.append({'method': request.method, 'path': url.path, 'body': body})
            # An accidental Run can never reach a physics executor in this test.
            assert not (request.method == 'POST' and url.path.startswith(('/runs', '/interactive/'))), url.path
            if request.method == 'POST' and url.path == '/tasks/parse' and body.get('input_kind') == 'natural_language':
                facts['mock_agent_request'] = body
                route.fulfill(status=200, json={'ok': True, 'result': {
                    'task_spec': copy.deepcopy(b), 'task_spec_json': task_spec_json(b),
                    'planning': None, 'route': {'provider_id': 'protocol-only-mock-no-model'}}})
                return
            response = client.request(request.method, url.path + ('?'+url.query if url.query else ''),
                content=request.post_data_buffer,
                headers={k:v for k,v in request.headers.items() if k in ('content-type', 'accept')})
            if request.method == 'POST' and url.path.endswith('/project') and body and _power(body['task_spec']) == 57.:
                pending['project_b'] = (route, response)
                facts['projection_b_request'] = body
                return
            if request.method == 'POST' and url.path == '/tasks/export-program':
                pending['zip_a'] = (route, response)
                facts['zip_a_request'] = body
                facts['zip_a_response_status'] = response.status_code
                facts['zip_a_response_sha256'] = hashlib.sha256(response.content).hexdigest()
                # Retain the actual CPU-produced bytes even if browser download
                # is correctly prevented by the eventual snapshot check.
                (tmp_path/'server-produced-a.zip').write_bytes(response.content)
                return
            fulfill(route, response)

        page.route('https://workbench.test/**', proxy)
        def wait_pending(name):
            # The request event precedes completion of the synchronous route
            # proxy's real TestClient call. Pump Playwright until that exact
            # held response is ready, rather than equating HTTP intent to it.
            deadline = time.monotonic() + 20
            while name not in pending and time.monotonic() < deadline:
                page.wait_for_timeout(20)
            assert name in pending, f'Route callback did not retain {name}'
        try:
            page.goto('https://workbench.test/', wait_until='networkidle')
            page.wait_for_selector('.capability-card.active', state='attached')

            # Establish A through the visible JSON editor and real projection.
            page.locator('#creatorTaskSpecTabButton').click()
            page.locator('#taskSpecEditor').fill(task_spec_json(a))
            page.locator('#applyJsonToFormBtn').click()
            page.locator('#busyOverlay').wait_for(state='hidden')
            page.wait_for_function("() => document.querySelector('[data-path=\"parameters.values.power_w\"]')?.value === '37'")
            page.locator('#creatorGraphTabButton').click()
            page.locator('#graphFlowModeBtn').wait_for(state='visible')
            page.locator('#graphFlowModeBtn').click()
            # Compile once before B captures its context. The subsequent A
            # export compile must preserve that canonical context unchanged.
            with page.expect_response(lambda response: response.request.method == 'POST'
                    and urlsplit(response.url).path == '/tasks/compile-graph') as compiled_a:
                page.locator('#graphValidateBtn').click()
            assert compiled_a.value.status == 200
            assert compiled_a.value.json()['ok'] is True
            page.locator('#busyOverlay').wait_for(state='hidden')
            editor_a = page.locator('#taskSpecEditor').input_value()
            assert _power(json.loads(editor_a)) == 37.
            facts['checkpoints']['a_canonical_before_generation'] = json.loads(editor_a)

            # Return B's mocked answer, but hold its real CPU form projection.
            page.locator('#creatorAgentTabButton').click()
            page.locator('#agentRefineCurrent').uncheck()
            page.locator('#agentInput').fill('Create a thermal node at 57 W. Protocol fixture; do not run.')
            with page.expect_request(lambda request: request.method == 'POST'
                    and urlsplit(request.url).path.endswith('/project')):
                page.locator('#agentParseBtn').click()
            wait_pending('project_b')
            assert len([r for r in requests if r['path'] == '/tasks/parse'
                        and r['body'] and r['body'].get('input_kind') == 'natural_language']) == 1
            assert facts['mock_agent_request'].get('base_task_spec') is None
            assert _power(json.loads(page.locator('#taskSpecEditor').input_value())) == 37.

            # Export the still-current A via the public graph action. Both
            # compilation and ZIP are real CPU endpoints, only completion waits.
            page.locator('#creatorGraphTabButton').click()
            with page.expect_request(lambda request: request.method == 'POST'
                    and urlsplit(request.url).path == '/tasks/export-program'):
                page.locator('#graphExportProgramBtn').click()
            wait_pending('zip_a')
            assert facts['zip_a_response_status'] == 200
            assert _power(facts['zip_a_request']['task_spec']) == 37.
            assert page.locator('#taskSpecEditor').input_value() == editor_a, (
                'Causal setup failed: export compilation changed A before B projection could be applied')
            with zipfile.ZipFile(io.BytesIO(pending['zip_a'][1].content)) as archive:
                spec_names = [name for name in archive.namelist() if name.endswith('/task_spec.json')]
                assert len(spec_names) == 1
                zip_spec_a = json.loads(archive.read(spec_names[0]))
            assert _power(zip_spec_a) == 37.
            facts['server_zip_task_spec'] = zip_spec_a

            # B must actually apply before the A ZIP is released. Do not edit
            # any state/DOM or switch tabs here: B alone causes the drift.
            route_b, response_b = pending.pop('project_b')
            assert response_b.status_code == 200
            fulfill(route_b, response_b)
            page.wait_for_function("() => { try { return JSON.parse(document.querySelector('#taskSpecEditor').value).parameters.values.power_w === 57; } catch (_) { return false; } }")
            page.wait_for_function("() => !document.querySelector('#agentParseBtn').disabled")
            facts['checkpoints']['b_applied_before_a_zip'] = json.loads(page.locator('#taskSpecEditor').input_value())
            assert _power(facts['checkpoints']['b_applied_before_a_zip']) == 57.
            assert page.locator('[data-path="parameters.values.power_w"]').input_value() == '57'
            page.screenshot(path=str(tmp_path/'b-applied-a-zip-still-pending.png'))
            before_status = page.locator('#graphStatus').text_content()

            route_zip, response_zip = pending.pop('zip_a')
            with page.expect_response(lambda response: response.request.method == 'POST'
                    and urlsplit(response.url).path == '/tasks/export-program'):
                fulfill(route_zip, response_zip)
            page.locator('#busyOverlay').wait_for(state='hidden')
            # Handler completion is observed through its public status, rather
            # than a timer that could pass before blob/download continuation.
            page.wait_for_function('(previous) => document.querySelector("#graphStatus").textContent !== previous', arg=before_status)
            # Show the real public export feedback in the retained image, even
            # though B's successful projection returned to the parameter form.
            page.locator('#creatorGraphTabButton').click()
            page.locator('#graphStatus').scroll_into_view_if_needed()
            page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
            page.screenshot(path=str(tmp_path/'after-stale-a-zip-completion.png'))
            graph_class = page.locator('#graphStatus').get_attribute('class') or ''
            graph_text = page.locator('#graphStatus').inner_text()
            stale_success = 'success' in graph_class and any(token in graph_text.lower()
                for token in ('程序包', '导出', 'zip', 'export', 'download'))
            if stale_success and not downloads:
                # This wait only retains evidence after an already established
                # erroneous success. It cannot turn the verdict into a pass.
                try:
                    page.wait_for_event('download', timeout=3000)
                except playwright.TimeoutError:
                    pass
            saved_downloads = []
            for index, download in enumerate(downloads):
                target = tmp_path/f'actual-browser-download-{index}.zip'
                download.save_as(str(target))
                saved_downloads.append({'filename': download.suggested_filename,
                    'path': target.name, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
            facts.update(graph_status={'class': graph_class, 'text': graph_text},
                stale_zip_success=stale_success, downloads=saved_downloads,
                final_editor=json.loads(page.locator('#taskSpecEditor').input_value()))
            dump('program-export-snapshot.json', facts)
            assert _power(facts['final_editor']) == 57.
            assert not downloads, 'Obsolete A ZIP downloaded after B was applied; see retained ZIP and protocol evidence'
            assert not stale_success, 'Obsolete A export reported success for the newer B context'
            assert not errors
        finally:
            facts.update(requests=requests, page_errors=errors, download_event_count=len(downloads))
            dump('program-export-snapshot.json', facts)
            browser.close()


def test_assembly_compile_response_cannot_overwrite_new_agent_task(tmp_path):
    """Delay real A assembly compilation until mock B has actually committed."""
    from pathlib import Path

    playwright = pytest.importorskip('playwright.sync_api')
    executable = os.environ.get('SAT_SIM_BROWSER_EXECUTABLE')
    if not executable:
        pytest.skip('Set SAT_SIM_BROWSER_EXECUTABLE to installed Chromium')
    # This registered parent has a public default assembly contract. The
    # unified-native single capability is deliberately not assumed a catalog row.
    parent = 'whole_spacecraft.power_thermal_orbit_coupled.v1'
    form_a = copy.deepcopy(capability_form_schema(parent)['default_form'])
    form_a['task']['id'] = 'assembly-export-snapshot'
    form_a['task']['name'] = 'Assembly compile snapshot protocol fixture'
    form_a['simulation']['duration_s'] = 37.
    form_b = copy.deepcopy(form_a)
    form_b['simulation']['duration_s'] = 57.
    a = canonicalize_task_spec(normalize_form_task_spec(form_a))
    expected_asset = Path(create_app.__code__.co_filename).resolve().parent/'web'/'app.js'
    expected_asset_sha = hashlib.sha256(expected_asset.read_bytes()).hexdigest()
    app = create_app(runs_root=tmp_path/'runs', artifacts_root=tmp_path/'artifacts',
                     embedded_worker=False, auth_mode='disabled')
    pending, calls, downloads, errors = {}, [], [], []
    facts = {'scope': 'Mock Agent B and actual delayed CPU assembly compile; no model/native execution.',
             'expected_app_js_path':str(expected_asset), 'expected_app_js_sha256':expected_asset_sha,
             'model_calls':0, 'simulation_run_requests':0, 'hold_compile':False}

    def dump():
        (tmp_path/'assembly-compile-snapshot.json').write_text(
            json.dumps(facts, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

    def fulfill(route, response):
        route.fulfill(status=response.status_code, body=response.content, headers={
            k:v for k,v in response.headers.items()
            if k in ('content-type','content-disposition','x-sat-sim-program-kind')})

    with TestClient(app) as client, playwright.sync_playwright() as pw:
        # Only the protocol response is mocked. B's canonical task/plan are
        # generated by the real form CPU path, never a model or a simulation.
        parsed_b = client.post('/tasks/parse', json={
            'input_kind':'form', 'form_data':form_b, 'compile_if_valid':True})
        assert parsed_b.status_code == 200 and parsed_b.json()['ok'] is True
        b_result = parsed_b.json()['result']
        b = b_result['task_spec']
        assert b['simulation']['duration_s'] == 57.
        facts['b_cpu_result'] = b_result
        browser = pw.chromium.launch(headless=True, executable_path=executable, args=['--disable-gpu'])
        page = browser.new_page(viewport={'width':1366,'height':900}, accept_downloads=True)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('download', lambda download: downloads.append(download))

        def wait_pending(name):
            deadline = time.monotonic()+15
            while name not in pending and time.monotonic() < deadline:
                page.wait_for_timeout(10)  # Dispatch the real route callback, not a state injection.
            assert name in pending, f'CPU route callback did not retain {name}'

        def proxy(route):
            request = route.request
            url = urlsplit(request.url)
            body = json.loads(request.post_data) if request.post_data else None
            calls.append({'method':request.method, 'path':url.path, 'body':body})
            assert not (request.method == 'POST' and url.path.startswith(('/runs','/interactive/'))), url.path
            if request.method == 'POST' and url.path == '/tasks/parse' and body.get('input_kind') == 'natural_language':
                facts['mock_agent_request'] = body
                route.fulfill(status=200, json={'ok':True, 'result':{
                    'task_spec':copy.deepcopy(b), 'task_spec_json':task_spec_json(b),
                    'planning':b_result['planning'], 'route':{'provider_id':'protocol-only-mock-no-model'}}})
                return
            response = client.request(request.method, url.path+('?'+url.query if url.query else ''),
                content=request.post_data_buffer,
                headers={k:v for k,v in request.headers.items() if k in ('content-type','accept')})
            if request.method == 'GET' and url.path == '/assets/app.js':
                facts.setdefault('served_app_js_sha256', []).append(hashlib.sha256(response.content).hexdigest())
            if request.method == 'POST' and url.path.endswith('/project') and body and body['task_spec']['simulation']['duration_s'] == 57.:
                pending['project_b'] = (route, response)
                return
            if request.method == 'POST' and url.path == '/tasks/compile-assembly' and facts['hold_compile']:
                pending['compile_a'] = (route, response)
                facts['compile_a_request'] = body
                facts['compile_a_response_status'] = response.status_code
                facts['compile_a_response'] = response.json()
                return
            if request.method == 'POST' and url.path == '/tasks/export-program':
                facts.setdefault('unexpected_zip_requests', []).append(body)
                (tmp_path/'assembly-unexpected-server.zip').write_bytes(response.content)
            fulfill(route, response)

        page.route('https://workbench.test/**', proxy)
        try:
            page.goto('https://workbench.test/', wait_until='networkidle')
            page.wait_for_selector('.capability-card.active', state='attached')
            assert facts.get('served_app_js_sha256') and set(facts['served_app_js_sha256']) == {expected_asset_sha}
            page.locator('#creatorGraphTabButton').click()
            page.locator('#graphAssemblyModeBtn').click()
            page.wait_for_function('() => document.querySelector("#assemblyStatus").textContent.startsWith("已加载")')
            page.locator(f'#assemblyCapabilitySelect option[value="{parent}"]').wait_for(state='attached')
            page.locator('#assemblyCapabilitySelect').select_option(parent)
            with page.expect_response(lambda response: response.request.method == 'GET'
                    and urlsplit(response.url).path == '/visual-composer/assemblies/'+parent) as loaded:
                page.locator('#assemblyLoadBtn').click()
            assert loaded.value.status == 200
            contract_name = loaded.value.json()['assembly']['name']
            page.wait_for_function('(name) => document.querySelector("#assemblyStatus").textContent.includes(name)', arg=contract_name)

            page.locator('#creatorTaskSpecTabButton').click()
            page.locator('#taskSpecEditor').fill(task_spec_json(a))
            page.locator('#applyJsonToFormBtn').click()
            page.locator('#busyOverlay').wait_for(state='hidden')
            page.wait_for_function("() => document.querySelector('[data-path=\"simulation.duration_s\"]')?.value === '37'")
            page.locator('#creatorGraphTabButton').click()
            with page.expect_response(lambda response: response.request.method == 'POST'
                    and urlsplit(response.url).path == '/tasks/compile-assembly') as compiled:
                page.locator('#assemblyValidateBtn').click()
            assert compiled.value.status == 200 and compiled.value.json()['ok'] is True
            page.locator('#busyOverlay').wait_for(state='hidden')
            assert json.loads(page.locator('#taskSpecEditor').input_value())['simulation']['duration_s'] == 37.

            page.locator('#creatorAgentTabButton').click()
            page.locator('#agentRefineCurrent').uncheck()
            page.locator('#agentInput').fill('Create this coupled spacecraft with duration 57 seconds. Protocol fixture; do not run.')
            with page.expect_request(lambda request: request.method == 'POST'
                    and urlsplit(request.url).path.endswith('/project')):
                page.locator('#agentParseBtn').click()
            wait_pending('project_b')
            facts['hold_compile'] = True
            page.locator('#creatorGraphTabButton').click()
            with page.expect_request(lambda request: request.method == 'POST'
                    and urlsplit(request.url).path == '/tasks/compile-assembly'):
                page.locator('#assemblyExportProgramBtn').click()
            wait_pending('compile_a')
            assert facts['compile_a_response_status'] == 200 and facts['compile_a_response']['ok'] is True
            assert facts['compile_a_request']['form_data']['simulation']['duration_s'] == 37.
            assert facts['compile_a_response']['result']['task_spec']['simulation']['duration_s'] == 37.

            route_b, response_b = pending.pop('project_b')
            fulfill(route_b, response_b)
            page.wait_for_function('() => { try { return JSON.parse(document.querySelector("#taskSpecEditor").value).simulation.duration_s === 57; } catch (_) { return false; } }')
            page.wait_for_function('() => !document.querySelector("#agentParseBtn").disabled')
            b_editor = page.locator('#taskSpecEditor').input_value()
            facts['b_applied_before_a_compile'] = json.loads(b_editor)
            assert page.locator('[data-path="simulation.duration_s"]').input_value() == '57'
            page.screenshot(path=str(tmp_path/'assembly-b-applied-a-compile-held.png'))

            route_a, response_a = pending.pop('compile_a')
            with page.expect_response(lambda response: response.request.method == 'POST'
                    and urlsplit(response.url).path == '/tasks/compile-assembly'):
                fulfill(route_a, response_a)
            page.locator('#busyOverlay').wait_for(state='hidden')
            page.locator('#creatorGraphTabButton').click()
            page.locator('#assemblyStatus').scroll_into_view_if_needed()
            page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
            page.screenshot(path=str(tmp_path/'assembly-after-obsolete-compile.png'))
            final_editor = page.locator('#taskSpecEditor').input_value()
            status_class = page.locator('#assemblyStatus').get_attribute('class') or ''
            status_text = page.locator('#assemblyStatus').inner_text()
            saved = []
            for index, download in enumerate(downloads):
                dest = tmp_path/f'assembly-browser-download-{index}.zip'
                download.save_as(str(dest))
                saved.append({'path':dest.name,'sha256':hashlib.sha256(dest.read_bytes()).hexdigest()})
            facts.update(final_editor=json.loads(final_editor), final_form_duration=page.locator('[data-path="simulation.duration_s"]').input_value(),
                assembly_status={'class':status_class,'text':status_text}, downloads=saved,
                plan_scope='B and heldA CPU planning evidence retained; private UI planning state is not injected or read.')
            dump()
            assert final_editor == b_editor, 'Obsolete A assembly compile overwrote the newer B canonical document/provenance'
            assert facts['final_editor']['simulation']['duration_s'] == 57.
            assert facts['final_form_duration'] == '57'
            assert not facts.get('unexpected_zip_requests'), 'A stale compilation must not submit an export for another current task'
            assert not downloads
            assert not ('success' in status_class and any(token in status_text.lower()
                for token in ('程序包','导出','zip','export','download')))
            assert not errors
        finally:
            facts.update(requests=calls, page_errors=errors, download_event_count=len(downloads))
            dump()
            browser.close()


def test_assembly_python_response_cannot_become_current_code_after_new_task(tmp_path):
    """Hold A Python bytes until B commits; reject obsolete code and prove B export works."""
    import ast
    from pathlib import Path

    def script_task_spec(code):
        for node in ast.parse(code).body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'TASK_SPEC' for t in node.targets):
                return ast.literal_eval(node.value)
        raise AssertionError('Real exported Python must retain its editable TASK_SPEC literal')

    playwright = pytest.importorskip('playwright.sync_api')
    executable = os.environ.get('SAT_SIM_BROWSER_EXECUTABLE')
    if not executable:
        pytest.skip('Set SAT_SIM_BROWSER_EXECUTABLE to installed Chromium')
    # This registered parent has a public default assembly contract. The
    # unified-native single capability is deliberately not assumed a catalog row.
    parent = 'whole_spacecraft.power_thermal_orbit_coupled.v1'
    form_a = copy.deepcopy(capability_form_schema(parent)['default_form'])
    form_a['task']['id'] = 'assembly-export-snapshot'
    form_a['task']['name'] = 'Assembly Python snapshot protocol fixture'
    form_a['simulation']['duration_s'] = 37.
    form_b = copy.deepcopy(form_a)
    form_b['simulation']['duration_s'] = 57.
    a = canonicalize_task_spec(normalize_form_task_spec(form_a))
    expected_asset = Path(create_app.__code__.co_filename).resolve().parent/'web'/'app.js'
    expected_asset_sha = hashlib.sha256(expected_asset.read_bytes()).hexdigest()
    app = create_app(runs_root=tmp_path/'runs', artifacts_root=tmp_path/'artifacts',
                     embedded_worker=False, auth_mode='disabled')
    pending, calls, downloads, errors = {}, [], [], []
    facts = {'scope': 'Mock Agent B plus actual delayed CPU Python export; no model/native execution.',
             'expected_app_js_path':str(expected_asset), 'expected_app_js_sha256':expected_asset_sha,
             'model_calls':0, 'simulation_run_requests':0, 'hold_script':False}

    def dump():
        (tmp_path/'assembly-python-snapshot.json').write_text(
            json.dumps(facts, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

    def fulfill(route, response):
        route.fulfill(status=response.status_code, body=response.content, headers={
            k:v for k,v in response.headers.items()
            if k in ('content-type','content-disposition','x-sat-sim-program-kind')})

    with TestClient(app) as client, playwright.sync_playwright() as pw:
        # Only the protocol response is mocked. B's canonical task/plan are
        # generated by the real form CPU path, never a model or a simulation.
        parsed_b = client.post('/tasks/parse', json={
            'input_kind':'form', 'form_data':form_b, 'compile_if_valid':True})
        assert parsed_b.status_code == 200 and parsed_b.json()['ok'] is True
        b_result = parsed_b.json()['result']
        b = b_result['task_spec']
        assert b['simulation']['duration_s'] == 57.
        facts['b_cpu_result'] = b_result
        browser = pw.chromium.launch(headless=True, executable_path=executable, args=['--disable-gpu'])
        page = browser.new_page(viewport={'width':1366,'height':900}, accept_downloads=True)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('download', lambda download: downloads.append(download))

        def wait_pending(name):
            deadline = time.monotonic()+15
            while name not in pending and time.monotonic() < deadline:
                page.wait_for_timeout(10)  # Dispatch the real route callback, not a state injection.
            assert name in pending, f'CPU route callback did not retain {name}'

        def proxy(route):
            request = route.request
            url = urlsplit(request.url)
            body = json.loads(request.post_data) if request.post_data else None
            calls.append({'method':request.method, 'path':url.path, 'body':body})
            assert not (request.method == 'POST' and url.path.startswith(('/runs','/interactive/'))), url.path
            if request.method == 'POST' and url.path == '/tasks/parse' and body.get('input_kind') == 'natural_language':
                facts['mock_agent_request'] = body
                route.fulfill(status=200, json={'ok':True, 'result':{
                    'task_spec':copy.deepcopy(b), 'task_spec_json':task_spec_json(b),
                    'planning':b_result['planning'], 'route':{'provider_id':'protocol-only-mock-no-model'}}})
                return
            response = client.request(request.method, url.path+('?'+url.query if url.query else ''),
                content=request.post_data_buffer,
                headers={k:v for k,v in request.headers.items() if k in ('content-type','accept')})
            if request.method == 'GET' and url.path == '/assets/app.js':
                facts.setdefault('served_app_js_sha256', []).append(hashlib.sha256(response.content).hexdigest())
            if request.method == 'POST' and url.path.endswith('/project') and body and body['task_spec']['simulation']['duration_s'] == 57.:
                pending['project_b'] = (route, response)
                return
            if request.method == 'POST' and url.path == '/tasks/export-assembly-script':
                facts.setdefault('script_response_records', []).append({'request':body, 'status':response.status_code, 'response':response.json()})
            if (request.method == 'POST' and url.path == '/tasks/export-assembly-script'
                    and facts['hold_script'] and body['task_spec']['simulation']['duration_s'] == 37.):
                pending['script_a'] = (route, response)
                facts['script_a_request'] = body
                facts['script_a_response_status'] = response.status_code
                facts['script_a_response'] = response.json()
                return
            if request.method == 'POST' and url.path == '/tasks/export-program':
                facts.setdefault('unexpected_zip_requests', []).append(body)
                (tmp_path/'assembly-unexpected-server.zip').write_bytes(response.content)
            fulfill(route, response)

        page.route('https://workbench.test/**', proxy)
        try:
            page.goto('https://workbench.test/', wait_until='networkidle')
            page.wait_for_selector('.capability-card.active', state='attached')
            assert facts.get('served_app_js_sha256') and set(facts['served_app_js_sha256']) == {expected_asset_sha}
            page.locator('#creatorGraphTabButton').click()
            page.locator('#graphAssemblyModeBtn').click()
            page.wait_for_function('() => document.querySelector("#assemblyStatus").textContent.startsWith("已加载")')
            page.locator(f'#assemblyCapabilitySelect option[value="{parent}"]').wait_for(state='attached')
            page.locator('#assemblyCapabilitySelect').select_option(parent)
            with page.expect_response(lambda response: response.request.method == 'GET'
                    and urlsplit(response.url).path == '/visual-composer/assemblies/'+parent) as loaded:
                page.locator('#assemblyLoadBtn').click()
            assert loaded.value.status == 200
            contract_name = loaded.value.json()['assembly']['name']
            page.wait_for_function('(name) => document.querySelector("#assemblyStatus").textContent.includes(name)', arg=contract_name)

            page.locator('#creatorTaskSpecTabButton').click()
            page.locator('#taskSpecEditor').fill(task_spec_json(a))
            page.locator('#applyJsonToFormBtn').click()
            page.locator('#busyOverlay').wait_for(state='hidden')
            page.wait_for_function("() => document.querySelector('[data-path=\"simulation.duration_s\"]')?.value === '37'")
            page.locator('#creatorGraphTabButton').click()
            with page.expect_response(lambda response: response.request.method == 'POST'
                    and urlsplit(response.url).path == '/tasks/compile-assembly') as compiled:
                page.locator('#assemblyValidateBtn').click()
            assert compiled.value.status == 200 and compiled.value.json()['ok'] is True
            page.locator('#busyOverlay').wait_for(state='hidden')
            editor_a = page.locator('#taskSpecEditor').input_value()
            assert json.loads(editor_a)['simulation']['duration_s'] == 37.

            page.locator('#creatorAgentTabButton').click()
            page.locator('#agentRefineCurrent').uncheck()
            page.locator('#agentInput').fill('Create this coupled spacecraft with duration 57 seconds. Protocol fixture; do not run.')
            with page.expect_request(lambda request: request.method == 'POST'
                    and urlsplit(request.url).path.endswith('/project')):
                page.locator('#agentParseBtn').click()
            wait_pending('project_b')
            facts['hold_script'] = True
            page.locator('#creatorGraphTabButton').click()
            with page.expect_request(lambda request: request.method == 'POST'
                    and urlsplit(request.url).path == '/tasks/export-assembly-script'):
                page.locator('#assemblyGenerateCodeBtn').click()
            wait_pending('script_a')
            assert facts['script_a_response_status'] == 200 and facts['script_a_response']['ok'] is True
            assert facts['script_a_request']['task_spec']['simulation']['duration_s'] == 37.
            code_a = facts['script_a_response']['code']
            assert script_task_spec(code_a)['simulation']['duration_s'] == 37.
            (tmp_path/'server-response-a.py').write_bytes(code_a.encode('utf-8'))
            assert page.locator('#taskSpecEditor').input_value() == editor_a, (
                'Causal setup changed A during normal pre-export compilation; B must not be discarded for that reason')

            route_b, response_b = pending.pop('project_b')
            fulfill(route_b, response_b)
            page.wait_for_function('() => { try { return JSON.parse(document.querySelector("#taskSpecEditor").value).simulation.duration_s === 57; } catch (_) { return false; } }')
            page.wait_for_function('() => !document.querySelector("#agentParseBtn").disabled')
            b_editor = page.locator('#taskSpecEditor').input_value()
            facts['b_applied_before_a_script'] = json.loads(b_editor)
            assert page.locator('[data-path="simulation.duration_s"]').input_value() == '57'
            page.screenshot(path=str(tmp_path/'assembly-b-applied-a-python-held.png'))

            route_a, response_a = pending.pop('script_a')
            with page.expect_response(lambda response: response.request.method == 'POST'
                    and urlsplit(response.url).path == '/tasks/export-assembly-script'):
                fulfill(route_a, response_a)
            page.locator('#busyOverlay').wait_for(state='hidden')
            page.locator('#creatorGraphTabButton').click()
            page.locator('#assemblyStatus').scroll_into_view_if_needed()
            page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
            page.screenshot(path=str(tmp_path/'assembly-after-obsolete-python.png'))
            final_editor = page.locator('#taskSpecEditor').input_value()
            status_class = page.locator('#assemblyStatus').get_attribute('class') or ''
            status_text = page.locator('#assemblyStatus').inner_text()
            preview = page.locator('#assemblyInspector .assembly-code-preview')
            wrong_current_preview = False
            if preview.count():
                code_shown = preview.input_value()
                section = preview.locator('..')
                heading = section.locator('h4').inner_text()
                shown_duration = script_task_spec(code_shown)['simulation']['duration_s']
                explicitly_stale = '已过期' in heading
                wrong_current_preview = shown_duration != 57. and not explicitly_stale
                facts['preview_after_late_a'] = {'duration_s':shown_duration, 'heading':heading, 'explicitly_stale':explicitly_stale}
                # Probe the real cached-download action only when a code panel
                # exists. A correct stale panel may regenerate B; A must not be
                # downloaded as current B. No script is ever executed.
                with page.expect_download() as downloaded:
                    section.get_by_role('button', name='下载 .py', exact=True).click()
                dest = tmp_path/'after-late-response-download.py'
                downloaded.value.save_as(str(dest))
                facts['late_response_download'] = {'sha256':hashlib.sha256(dest.read_bytes()).hexdigest(),
                    'duration_s':script_task_spec(dest.read_text(encoding='utf-8'))['simulation']['duration_s']}
            facts.update(final_editor=json.loads(final_editor), wrong_current_preview=wrong_current_preview,
                script_status={'class':status_class,'text':status_text},
                final_form_duration=page.locator('[data-path="simulation.duration_s"]').input_value())
            dump()
            assert final_editor == b_editor, 'A Python response must not change B canonical document/provenance'
            assert facts['final_editor']['simulation']['duration_s'] == 57.
            assert facts['final_form_duration'] == '57'
            assert not wrong_current_preview, 'A37 Python was shown as fresh code for current B57'
            assert not ('success' in status_class and wrong_current_preview), 'Obsolete A generation reported success for B'
            assert not facts.get('late_response_download') or facts['late_response_download']['duration_s'] == 57., (
                'Cached download accepted A37 Python as current B57')
            assert not facts.get('unexpected_zip_requests')
            assert not errors

            # Positive control runs only after every stale-response oracle has
            # passed. It proves the guard still permits a genuine current B
            # Python generation and a byte-exact public cached download.
            facts['hold_script'] = False
            with page.expect_response(lambda response: response.request.method == 'POST'
                    and urlsplit(response.url).path == '/tasks/export-assembly-script') as positive:
                page.locator('#assemblyGenerateCodeBtn').click()
            assert positive.value.status == 200 and positive.value.json()['ok'] is True
            page.locator('#busyOverlay').wait_for(state='hidden')
            code_b = positive.value.json()['code']
            assert script_task_spec(code_b)['simulation']['duration_s'] == 57.
            latest = facts['script_response_records'][-1]
            assert latest['request']['task_spec']['simulation']['duration_s'] == 57.
            preview = page.locator('#assemblyInspector .assembly-code-preview')
            assert preview.input_value() == code_b
            with page.expect_download() as current_download:
                preview.locator('..').get_by_role('button', name='下载 .py', exact=True).click()
            dest = tmp_path/'positive-current-b.py'
            current_download.value.save_as(str(dest))
            assert dest.read_bytes() == code_b.encode('utf-8')
            facts['positive_current_b'] = {'request':latest['request'], 'code_sha256':hashlib.sha256(code_b.encode('utf-8')).hexdigest(),
                'download_sha256':hashlib.sha256(dest.read_bytes()).hexdigest(), 'duration_s':57.}
            assert json.loads(page.locator('#taskSpecEditor').input_value())['simulation']['duration_s'] == 57.
            assert not errors
            dump()
        finally:
            facts.update(requests=calls, page_errors=errors, download_event_count=len(downloads))
            dump()
            browser.close()
