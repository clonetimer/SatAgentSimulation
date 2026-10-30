"""Viewing another task must never redirect updates from the open editor."""
import copy
import json
import os
import re
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest

from sat_sim.api import create_app
from sat_sim.form_schema import capability_form_schema
from sat_sim.unified_agent import normalize_form_task_spec


def test_task_details_cannot_change_editor_update_target(tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    executable = os.environ.get('SAT_SIM_BROWSER_EXECUTABLE')
    if not executable:
        pytest.skip('Set SAT_SIM_BROWSER_EXECUTABLE to an installed Chromium browser')
    app = create_app(runs_root=tmp_path/'runs', artifacts_root=tmp_path/'artifacts',
                     embedded_worker=False, auth_mode='disabled')
    with TestClient(app) as client, playwright.sync_playwright() as pw:
        default = normalize_form_task_spec(capability_form_schema('component.thermal_node.v1')['default_form'])
        for task_id, duration in [('identity-task-A', 120), ('identity-task-B', 240)]:
            spec = copy.deepcopy(default)
            spec['task']['id'] = task_id
            spec['task']['name'] = task_id
            spec['simulation']['duration_s'] = duration
            response = client.post('/task-center/tasks', json={'task_id':task_id, 'task_spec':spec})
            assert response.status_code == 200, response.text
        writes, clones, errors = [], [], []
        browser = pw.chromium.launch(headless=True, executable_path=executable)
        page = browser.new_page(viewport={'width':1366, 'height':768})
        page.on('pageerror', lambda error: errors.append(str(error)))

        def proxy(route):
            request = route.request
            url = urlsplit(request.url)
            if request.method == 'PUT' and url.path.startswith('/task-center/tasks/'):
                writes.append({'path':url.path, 'body':json.loads(request.post_data)})
            if request.method == 'POST' and url.path.endswith('/clone'):
                clones.append(url.path)
            response = client.request(request.method, url.path + ('?'+url.query if url.query else ''),
                content=request.post_data_buffer,
                headers={key:value for key,value in request.headers.items() if key in ('content-type','accept')})
            route.fulfill(status=response.status_code, body=response.content,
                headers={'content-type':response.headers.get('content-type','application/octet-stream')})

        page.route('https://workbench.test/**', proxy)
        page.goto('https://workbench.test/', wait_until='networkidle')
        page.wait_for_selector('.capability-card.active', state='attached')
        tasks_nav = page.locator('.nav-button[data-view="tasks"]')
        creator_nav = page.locator('.nav-button[data-view="creator"]')
        tasks_nav.click()
        page.locator('#taskTableBody tr').filter(has_text='identity-task-B').get_by_role('button', name='详情', exact=True).click()
        page.locator('#taskDetailsModal').wait_for(state='visible')
        page.locator('#closeTaskDetailsBtn').click()
        creator_nav.click()
        assert page.locator('#updateTaskBtn').is_hidden()
        tasks_nav.click()
        page.locator('#taskTableBody tr').filter(has_text='identity-task-A').get_by_role('button', name='编辑', exact=True).click()
        duration = page.locator('[data-path="simulation.duration_s"]')
        page.wait_for_function("document.querySelector('[data-path=\"simulation.duration_s\"]')?.value === '120'")
        assert 'identity-task-A' in page.locator('#updateTaskBtn').get_attribute('title')
        duration.fill('123')
        duration.dispatch_event('change')
        tasks_nav.click()
        page.locator('#taskTableBody tr').filter(has_text='identity-task-B').get_by_role('button', name='详情', exact=True).click()
        page.locator('#taskDetailsModal').wait_for(state='visible')
        assert page.locator('#taskDetailsTitle').inner_text() == 'identity-task-B'
        page.locator('#closeTaskDetailsBtn').click()
        creator_nav.click()
        assert duration.input_value() == '123'
        # The visible edit target and submitted target must remain the same.
        title = page.locator('#updateTaskBtn').get_attribute('title')
        page.locator('#updateTaskBtn').click()
        page.wait_for_function("document.querySelector('#notice')?.className.includes('success')")
        task_a = client.get('/task-center/tasks/identity-task-A').json()['task']
        task_b = client.get('/task-center/tasks/identity-task-B').json()['task']
        proof = {'editor_title':title, 'writes':writes,
            'task_a_duration':task_a['task_spec']['simulation']['duration_s'],
            'task_b_duration':task_b['task_spec']['simulation']['duration_s'],
            'task_a_version':task_a['current_version'], 'task_b_version':task_b['current_version'],
            'page_errors':errors}
        (tmp_path/'task-identity-proof.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
        assert [row['path'] for row in writes] == ['/task-center/tasks/identity-task-A'], proof
        assert proof['task_a_duration'] == 123
        assert proof['task_b_duration'] == 240
        assert proof['task_a_version'] == 2
        assert proof['task_b_version'] == 1
        tasks_nav.click()
        page.locator('#taskTableBody tr').filter(has_text='identity-task-B').get_by_role('button', name='详情', exact=True).click()
        page.locator('#taskDetailsModal').wait_for(state='visible')
        page.locator('#cloneTaskBtn').click()
        page.wait_for_function("document.querySelector('#notice')?.textContent.includes('已克隆')")
        assert clones == ['/task-center/tasks/identity-task-B/clone']
        creator_nav.click()
        assert 'identity-task-A' in page.locator('#updateTaskBtn').get_attribute('title')
        assert duration.input_value() == '123'
        proof['clone_paths'] = clones
        (tmp_path/'task-identity-proof.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
        # Loading B replaces both the form's canonical base and edit owner.
        # Undo must restore A's edited view/context and owner together.
        tasks_nav.click()
        page.locator('#taskTableBody tr').filter(has=page.locator('.task-id').filter(has_text=re.compile(r'^identity-task-B$'))).get_by_role('button', name='编辑', exact=True).click()
        page.wait_for_function("()=>document.querySelector('[data-path=\"simulation.duration_s\"]')?.value === '240'")
        page.locator('#undoAgentChangesBtn').click()
        page.locator('#previewBtn').click()
        page.wait_for_function("()=>document.querySelector('#busyOverlay').classList.contains('hidden')")
        restored = json.loads(page.locator('#taskSpecEditor').input_value())
        assert restored['task']['id'] == 'identity-task-A'
        assert restored['simulation']['duration_s'] == 123
        assert 'identity-task-A' in page.locator('#updateTaskBtn').get_attribute('title')
        page.locator('#updateTaskBtn').click()
        page.wait_for_function("()=>document.querySelector('#notice').textContent.includes('已生成新版本')")
        assert [row['path'] for row in writes] == ['/task-center/tasks/identity-task-A'] * 2
        assert writes[-1]['body']['task_spec'] == restored
        assert client.get('/task-center/tasks/identity-task-B').json()['task']['current_version'] == 1
        proof['after_load_undo_writes'] = writes
        (tmp_path/'task-identity-proof.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
        assert not errors
        browser.close()
