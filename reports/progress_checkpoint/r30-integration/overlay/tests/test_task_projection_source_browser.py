"""Projection provenance survives edits, saves and reset undo in the real UI.

The Agent and historical-Run responses are labelled protocol fixtures. They do
not contact a model, execute physics or make a simulation-success assertion.
"""
import copy
import json
import os
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest

from sat_sim.api import create_app
from sat_sim.form_schema import capability_form_schema
from sat_sim.unified_agent import normalize_form_task_spec


def source_proof(page):
    return page.evaluate('''() => ({
        title:document.querySelector('#agentChangeTitle').textContent,
        source:document.querySelector('#agentChangePanel').dataset.projectionSource,
        hidden:document.querySelector('#agentChangePanel').classList.contains('hidden'),
        fields:[...document.querySelectorAll('.form-field.agent-changed')].map(node=>({
            source:node.dataset.changeSource,
            badge:getComputedStyle(node,'::after').content,
            highlight:getComputedStyle(node).boxShadow,
            paths:[...node.querySelectorAll('[data-path]')].map(input=>input.dataset.path)
        }))
    })''')


def assert_source(page, label, badge):
    page.wait_for_function('(label) => document.querySelector("#agentChangeTitle").textContent === label+" 已更新表单"',arg=label)
    proof = source_proof(page)
    assert proof['source'] == label and not proof['hidden']
    assert proof['fields'], proof  # Difference highlighting must remain present.
    assert all(field['source']==badge and field['badge']=='"'+badge+'"'
               and field['highlight']!='none' for field in proof['fields']), proof
    assert page.locator('.form-field.agent-changed:visible').count() > 0
    return proof


def test_projection_sources_persist_without_relabelling_loaded_tasks_as_agent(tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    executable = os.environ.get('SAT_SIM_BROWSER_EXECUTABLE')
    if not executable:
        pytest.skip('An installed Chromium browser is required')
    app = create_app(runs_root=tmp_path/'runs',artifacts_root=tmp_path/'artifacts',
                     embedded_worker=False,auth_mode='disabled')
    base = normalize_form_task_spec(capability_form_schema('component.thermal_node.v1')['default_form'])
    task = copy.deepcopy(base)
    task['task']['id'] = task['task']['name'] = 'manual-projection-task'
    task['simulation']['duration_s'] = 120
    agent = copy.deepcopy(task)
    agent['task']['id'] = agent['task']['name'] = 'agent-protocol-fixture'
    agent['simulation']['duration_s'] = 210
    historical = copy.deepcopy(task)
    historical['task']['id'] = historical['task']['name'] = 'history-protocol-fixture'
    historical['simulation']['duration_s'] = 240
    pending, writes, errors, agent_calls, history_calls = [],[],[],[],[]
    proofs = {}
    with TestClient(app) as client, playwright.sync_playwright() as pw:
        assert client.post('/task-center/tasks',json={'task_id':'manual-projection-task','task_spec':task}).status_code==200
        browser = pw.chromium.launch(headless=True,executable_path=executable)
        page = browser.new_page(viewport={'width':1366,'height':900})
        page.on('pageerror',lambda error:errors.append(str(error)))

        def fulfill(route):
            request = route.request
            url = urlsplit(request.url)
            result = client.request(request.method,url.path+('?'+url.query if url.query else ''),
                content=request.post_data_buffer,headers={key:value for key,value in request.headers.items()
                    if key in ('content-type','accept')})
            route.fulfill(status=result.status_code,body=result.content,
                headers={'content-type':result.headers.get('content-type','application/octet-stream')})

        def proxy(route):
            request = route.request
            path = urlsplit(request.url).path
            if request.method=='PUT' and path=='/task-center/tasks/manual-projection-task':
                pending.append(route)
                writes.append(json.loads(request.post_data))
                return
            if (request.method=='POST' and path=='/tasks/parse'
                    and json.loads(request.post_data).get('input_kind')=='natural_language'):
                agent_calls.append(json.loads(request.post_data))
                route.fulfill(status=200,json={'ok':True,'result':{'task_spec':agent,'planning':None,
                    'route':{'provider_id':'protocol-fixture-not-model-inference'}}})
                return
            if request.method=='GET' and path=='/runs':
                route.fulfill(status=200,json={'runs':[{'run_id':'history-source-fixture',
                    'task_name':'History source fixture','status':'PREPARED'}]})
                return
            if request.method=='GET' and path.startswith('/runs/history-source-fixture'):
                history_calls.append(path)
                if path=='/runs/history-source-fixture':
                    payload={'run':{'task_spec':historical,'run_record':{'run_id':'history-source-fixture',
                        'task_id':'history-protocol-fixture','status':'PREPARED','attempts':[]},
                        'available_outputs':{'fmea':False,'fault_traceability':False}}}
                else:
                    payload={'metrics':{},'rows':[],'artifacts':[],'dataset':None,
                             'events':None,'plot_manifest':None,'traceability':None}
                route.fulfill(status=200,json=payload)
                return
            assert not (request.method=='POST' and path.startswith(('/runs','/interactive/'))), path
            fulfill(route)

        page.route('https://workbench.test/**',proxy)
        page.goto('https://workbench.test/',wait_until='networkidle')
        page.wait_for_selector('.capability-card.active')
        page.locator('.nav-button[data-view="tasks"]').click()
        page.locator('#taskTableBody tr').filter(has_text='manual-projection-task').get_by_role('button',name='编辑',exact=True).click()
        page.wait_for_function('document.querySelector(\'[data-path="simulation.duration_s"]\')?.value === "120"')
        proofs['task_center_loaded'] = assert_source(page,'任务中心','载入')
        duration = page.locator('[data-path="simulation.duration_s"]')
        duration.fill('123')
        duration.dispatch_event('change')
        proofs['task_center_after_manual'] = assert_source(page,'任务中心','载入')
        page.locator('#agentChangePanel').scroll_into_view_if_needed()
        page.screenshot(path=str(tmp_path/'task-center-after-manual.png'))
        with page.expect_request(lambda request:request.method=='PUT'
            and urlsplit(request.url).path=='/task-center/tasks/manual-projection-task'):
            page.locator('#updateTaskBtn').click()
        proofs['task_center_pending_save'] = assert_source(page,'任务中心','载入')
        assert len(pending)==1
        assert 'success' not in page.locator('#notice').get_attribute('class')
        assert client.get('/task-center/tasks/manual-projection-task').json()['task']['current_version']==1
        fulfill(pending.pop())
        page.locator('#busyOverlay').wait_for(state='hidden')
        saved=client.get('/task-center/tasks/manual-projection-task').json()['task']
        assert saved['current_version']==2 and saved['task_spec']['simulation']['duration_s']==123
        proofs['task_center_after_save'] = assert_source(page,'任务中心','载入')

        editor = copy.deepcopy(saved['task_spec'])
        editor['task']['id'] = editor['task']['name'] = 'taskspec-projection'
        editor['simulation']['duration_s'] = 180
        page.locator('#creatorTaskSpecTabButton').click()
        page.locator('#taskSpecEditor').fill(json.dumps(editor))
        page.locator('#applyJsonToFormBtn').click()
        proofs['taskspec_loaded'] = assert_source(page,'TaskSpec','载入')
        duration.fill('181')
        duration.dispatch_event('change')
        proofs['taskspec_after_manual'] = assert_source(page,'TaskSpec','载入')
        page.locator('#agentChangePanel').scroll_into_view_if_needed()
        page.screenshot(path=str(tmp_path/'taskspec-after-manual.png'))
        page.locator('#acceptAgentChangesBtn').click()
        cleared=source_proof(page)
        assert cleared['hidden'] and cleared['source']=='' and cleared['fields']==[]

        page.locator('#creatorAgentTabButton').click()
        page.locator('#agentRefineCurrent').uncheck()
        page.locator('#agentInput').fill('UI source-label protocol fixture; no model execution.')
        page.locator('#agentParseBtn').click()
        proofs['agent_loaded'] = assert_source(page,'Agent','Agent')
        duration.fill('211')
        duration.dispatch_event('change')
        proofs['agent_after_manual'] = assert_source(page,'Agent','Agent')
        if page.locator('#creatorMoreActions').get_attribute('open') is None:
            page.locator('#creatorMoreActions > summary').click()
        page.locator('#resetFormBtn').click()
        assert source_proof(page)['hidden'] and source_proof(page)['source']==''
        page.locator('#undoResetFormBtn').click()
        assert duration.input_value()=='211'
        proofs['agent_after_reset_undo'] = assert_source(page,'Agent','Agent')
        page.locator('#agentChangePanel').scroll_into_view_if_needed()
        page.screenshot(path=str(tmp_path/'agent-after-reset-undo.png'))
        page.locator('#undoAgentChangesBtn').click()
        undone=source_proof(page)
        assert undone['hidden'] and undone['source']=='' and undone['fields']==[]
        assert duration.input_value()=='181'

        page.locator('#runList .run-list-item[data-run-id="history-source-fixture"]').click()
        page.locator('#busyOverlay').wait_for(state='hidden')
        if page.locator('#resultMoreActions').get_attribute('open') is None:
            page.locator('#resultMoreActions > summary').click()
        page.once('dialog',lambda dialog:dialog.accept())
        page.locator('#reuseRunConfigBtn').click()
        proofs['history_run_loaded'] = assert_source(page,'历史 Run','载入')
        duration.fill('241')
        duration.dispatch_event('change')
        proofs['history_run_after_manual'] = assert_source(page,'历史 Run','载入')
        page.locator('#agentChangePanel').scroll_into_view_if_needed()
        page.screenshot(path=str(tmp_path/'history-run-after-manual.png'))
        assert len(agent_calls)==1 and history_calls and len(writes)==1
        assert not errors
        (tmp_path/'projection-source-proof.json').write_text(json.dumps({
            'observations':proofs,'agent_protocol_calls':agent_calls,'history_protocol_reads':history_calls,
            'writes':writes,'page_errors':errors,'scope':'Actual browser/source-label behavior; Agent and Run data are protocol fixtures, not model/native execution.'},
            ensure_ascii=False,indent=2),encoding='utf-8')
        browser.close()
