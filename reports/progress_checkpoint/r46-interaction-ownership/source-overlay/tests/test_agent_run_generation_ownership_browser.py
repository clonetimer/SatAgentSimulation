"""Actual Chrome with delayed protocol responses; no real model/native execution."""
import copy
import hashlib
import json
import os
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest

from sat_sim.api import create_app
from sat_sim.canonical_transport import task_spec_json
from sat_sim.form_schema import capability_form_schema
from sat_sim.unified_agent import normalize_form_task_spec


@pytest.mark.parametrize('first_action', ['agent_button', 'implicit_run'])
def test_pending_generation_has_one_owner_across_agent_and_run(tmp_path, first_action):
    playwright = pytest.importorskip('playwright.sync_api')
    executable = os.environ.get('SAT_SIM_BROWSER_EXECUTABLE')
    if not executable:
        pytest.skip('Set SAT_SIM_BROWSER_EXECUTABLE to installed Chromium')
    form = capability_form_schema('component.thermal_node.v1')['default_form']
    form['parameters']['values']['power_w'] = 37.
    spec = normalize_form_task_spec(form)
    app = create_app(runs_root=tmp_path/'runs', artifacts_root=tmp_path/'artifacts',
                     embedded_worker=False, auth_mode='disabled')
    pending, calls, run_calls, errors = [], [], [], []
    report = {'scope':'Delayed HTTP synchronization fixture; actual Chrome/public controls, zero model/native calls',
              'first_action':first_action, 'screenshots':[]}
    def save(page, label):
        path = tmp_path/(label+'.png')
        page.screenshot(path=str(path))
        report['screenshots'].append({'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        (tmp_path/'ownership.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    with TestClient(app) as client, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=executable)
        try:
            page = browser.new_page(viewport={'width':1366,'height':900})
            page.on('pageerror', lambda error: errors.append(str(error)))
            def proxy(route):
                req = route.request
                url = urlsplit(req.url)
                body = json.loads(req.post_data) if req.post_data else None
                if req.method=='POST' and url.path=='/tasks/parse' and body.get('input_kind')=='natural_language':
                    calls.append(body); pending.append(route); return
                if req.method=='POST' and url.path=='/runs':
                    run_calls.append(body)
                    route.fulfill(status=503,json={'detail':'Synchronization fixture deliberately blocks all execution'})
                    return
                response = client.request(req.method,url.path+('?'+url.query if url.query else ''),
                    content=req.post_data_buffer,headers={k:v for k,v in req.headers.items() if k in ('content-type','accept')})
                route.fulfill(status=response.status_code,body=response.content,
                    headers={'content-type':response.headers.get('content-type','application/octet-stream')})
            page.route('https://workbench.test/**',proxy)
            page.goto('https://workbench.test/',wait_until='networkidle')
            page.locator('.capability-card.active').wait_for(state='attached')
            assert page.locator('#taskSpecEditor').input_value()==''
            page.locator('#creatorAgentTabButton').click()
            page.locator('#agentRefineCurrent').uncheck()
            page.locator('#agentInput').fill('Create a thermal node at 37W.')
            first = '#agentParseBtn' if first_action=='agent_button' else '#runBtn'
            second = '#runBtn' if first_action=='agent_button' else '#agentParseBtn'
            with page.expect_request(lambda req:req.method=='POST' and urlsplit(req.url).path=='/tasks/parse'):
                page.locator(first).click()
            report.update(initial_parse_calls=len(calls),second_control_initially_disabled=page.locator(second).is_disabled(),
                user_messages_before_second=page.locator('#agentConversation .agent-message.user').count(),
                input_enabled_during_pending=page.locator('#agentInput').is_enabled(),
                clear_enabled_during_pending=page.locator('#clearAgentConversationBtn').is_enabled(),
                overlay_visible_during_pending=page.locator('#busyOverlay').is_visible())
            # The original source leaves the other entry enabled. Use its real
            # keyboard activation even if Run's unrelated pointer overlay is up.
            if page.locator(second).is_enabled():
                page.locator(second).focus()
                page.keyboard.press('Enter')
            elif first_action=='agent_button':
                # A second public Run entry must also respect the same owner.
                page.locator('#creatorGraphTabButton').click()
                page.locator('#graphRunBtn').click()
            page.wait_for_timeout(200)
            report.update(parse_calls_after_second=len(calls),
                user_messages_after_second=page.locator('#agentConversation .agent-message.user').count(),
                pending_routes=len(pending),run_calls_before_answer=len(run_calls))
            save(page,'pending-after-second-action')
            pending[0].fulfill(status=200,json={'ok':True,'result':{'task_spec':copy.deepcopy(spec),
                'task_spec_json':task_spec_json(spec),'planning':None,'route':{'provider_id':'protocol-only'}}})
            page.wait_for_timeout(250)
            report['notice_after_first_answer']=page.locator('#notice').inner_text()
            # Complete only any duplicate protocol request created by the old
            # source so red evidence does not leave a route hanging.
            for route in pending[1:]:
                route.fulfill(status=503,json={'detail':'Duplicate protocol request, no model invoked'})
            page.wait_for_function("()=>!document.querySelector('#agentParseBtn').disabled && document.querySelector('#busyOverlay').classList.contains('hidden')",timeout=30000)
            text = page.locator('#taskSpecEditor').input_value()
            current = json.loads(text) if text else None
            report.update(final_parse_calls=len(calls),final_user_messages=page.locator('#agentConversation .agent-message.user').count(),
                final_assistant_messages=page.locator('#agentConversation .agent-message.assistant').count(),
                committed_power_w=current.get('parameters',{}).get('values',{}).get('power_w') if current else None,
                current_editor=current,run_calls=len(run_calls),page_errors=errors)
            page.locator('#creatorAgentTabButton').click()
            page.locator('#agentConversation').scroll_into_view_if_needed()
            save(page,'first-answer-finished')
            assert len(calls)==1, report
            assert report['final_user_messages']==1 and report['final_assistant_messages']==1, report
            assert report['committed_power_w']==37., report
            assert report['run_calls_before_answer']==0, report
            assert report['run_calls']==(1 if first_action=='implicit_run' else 0), report
            assert report['second_control_initially_disabled'], report
            assert report['input_enabled_during_pending'] and report['clear_enabled_during_pending'], report
            assert not report['overlay_visible_during_pending'], report
            assert not errors, report
        finally:
            browser.close()
