"""An outstanding task write cannot display a previous operation's success."""
import copy
import json
import os
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from sat_sim.api import create_app
from sat_sim.form_schema import capability_form_schema
from sat_sim.unified_agent import normalize_form_task_spec


def test_save_clears_previous_success_until_actual_write_completes(tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    executable = os.environ.get('SAT_SIM_BROWSER_EXECUTABLE')
    if not executable:
        pytest.skip('An installed Chromium browser is required')
    app = create_app(runs_root=tmp_path/'runs', artifacts_root=tmp_path/'artifacts',
                     embedded_worker=False, auth_mode='disabled')
    with TestClient(app) as client, playwright.sync_playwright() as pw:
        spec = normalize_form_task_spec(capability_form_schema('component.thermal_node.v1')['default_form'])
        spec = copy.deepcopy(spec)
        spec['task']['id'] = spec['task']['name'] = 'feedback-task'
        spec['simulation']['duration_s'] = 120
        assert client.post('/task-center/tasks', json={'task_id':'feedback-task','task_spec':spec}).status_code == 200
        pending = []
        writes = []
        errors = []
        browser = pw.chromium.launch(headless=True, executable_path=executable)
        page = browser.new_page(viewport={'width':1366,'height':900})
        page.on('pageerror', lambda error: errors.append(str(error)))

        def fulfill(route):
            request = route.request
            url = urlsplit(request.url)
            response = client.request(request.method, url.path + ('?'+url.query if url.query else ''),
                content=request.post_data_buffer,
                headers={key:value for key,value in request.headers.items() if key in ('content-type','accept')})
            route.fulfill(status=response.status_code, body=response.content,
                headers={'content-type':response.headers.get('content-type','application/octet-stream')})

        def proxy(route):
            request = route.request
            if request.method == 'PUT' and urlsplit(request.url).path.startswith('/task-center/tasks/'):
                pending.append(route)
                writes.append({'path':urlsplit(request.url).path,'body':json.loads(request.post_data)})
                return
            fulfill(route)

        page.route('https://workbench.test/**', proxy)
        page.goto('https://workbench.test/', wait_until='networkidle')
        page.wait_for_selector('.capability-card.active')
        page.locator('.nav-button[data-view="tasks"]').click()
        page.locator('#taskTableBody tr').filter(has_text='feedback-task').get_by_role('button',name='\u7f16\u8f91',exact=True).click()
        duration = page.locator('[data-path="simulation.duration_s"]')
        page.wait_for_function("document.querySelector('[data-path=\"simulation.duration_s\"]')?.value === '120'")
        duration.fill('123')
        duration.dispatch_event('change')
        with page.expect_request(lambda request: request.method == 'PUT' and urlsplit(request.url).path == '/task-center/tasks/feedback-task'):
            page.locator('#updateTaskBtn').click()
        # The actual PUT stays pending under our control, rather than relying on
        # a sleep or interpreting any stale success notice as completion.
        notice_class = page.locator('#notice').get_attribute('class')
        assert len(pending) == 1
        current = client.get('/task-center/tasks/feedback-task').json()['task']
        (tmp_path/'pending-proof.json').write_text(json.dumps({
            'notice_class':notice_class,'task_version':current['current_version'],
            'duration':current['task_spec']['simulation']['duration_s'],'writes':writes},indent=2),encoding='utf-8')
        page.screenshot(path=str(tmp_path/'pending-save.png'))
        fulfill(pending.pop())
        page.wait_for_function("document.querySelector('#notice')?.className.includes('success')")
        page.locator('#busyOverlay').wait_for(state='hidden')
        page.locator('.creator-primary-actions').scroll_into_view_if_needed()
        page.screenshot(path=str(tmp_path/'saved-edit-actions.png'))
        saved = client.get('/task-center/tasks/feedback-task').json()['task']
        browser.close()
        assert 'success' not in notice_class
        assert current['current_version'] == 1
        assert current['task_spec']['simulation']['duration_s'] == 120
        assert saved['current_version'] == 2
        assert saved['task_spec']['simulation']['duration_s'] == 123
        assert [item['path'] for item in writes] == ['/task-center/tasks/feedback-task']
        assert not errors
