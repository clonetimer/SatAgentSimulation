"""Exercise completed-run navigation and truthful built-in-provider labels."""
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest

from sat_sim.api import create_app
from sat_sim.form_schema import capability_form_schema
from sat_sim.run_bundle import execute_prepared_run, prepare_run
from sat_sim.unified_agent import normalize_form_task_spec


def test_completed_run_tabs_and_builtin_provider_status_remain_readable(tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    executable = os.environ.get('SAT_SIM_BROWSER_EXECUTABLE')
    if not executable:
        pytest.skip('Set SAT_SIM_BROWSER_EXECUTABLE to an installed Chromium browser')
    runs_root = tmp_path/'runs'
    form = capability_form_schema('component.thermal_node.v1')['default_form']
    form['task']['name'] = 'generated_task'
    prepared = prepare_run(normalize_form_task_spec(form), output_root=runs_root, run_id='ui-presentation-run')
    execution = execute_prepared_run(prepared.bundle_root,
        expected_plan_sha256=prepared.execution_plan_sha256, max_attempts=1, hard_timeout=True)
    assert execution.validation.result.value == 'PASS'
    app = create_app(runs_root=runs_root, artifacts_root=tmp_path/'artifacts',
        embedded_worker=False, auth_mode='disabled')
    observations, errors, violations = [], [], []
    with TestClient(app) as client, playwright.sync_playwright() as pw:
        # Saving configuration does not contact or probe this test endpoint.
        response = client.post('/models/local-services', json={
            'service_type':'openai_compatible', 'provider_id':'local-ui-unprobed',
            'model':'unprobed-ui-test-model', 'base_url':'http://127.0.0.1:9/v1'})
        assert response.status_code == 200
        browser = pw.chromium.launch(headless=True, executable_path=executable)
        page = browser.new_page(viewport={'width':1366, 'height':900})
        page.on('pageerror', lambda error: errors.append(str(error)))

        def proxy(route):
            request = route.request
            url = urlsplit(request.url)
            result = client.request(request.method, url.path+('?'+url.query if url.query else ''),
                content=request.post_data_buffer,
                headers={k:v for k,v in request.headers.items() if k in ('content-type','accept')})
            route.fulfill(status=result.status_code, body=result.content,
                headers={k:v for k,v in result.headers.items() if k.lower() in
                    ('content-type','content-security-policy','x-content-type-options','referrer-policy')})

        try:
            page.route('https://workbench.test/**', proxy)
            page.goto('https://workbench.test/', wait_until='networkidle')
            page.locator('#runList .run-list-item').first.click()
            page.wait_for_function("() => document.querySelector('#activeRunCard')?.textContent.includes('VERIFIED')")
            for width,height,scale in [(1366,900,'100'), (1366,768,'100'), (768,1024,'100'), (1920,1080,'130')]:
                page.set_viewport_size({'width':width, 'height':height})
                page.locator('#fontScaleSelect').select_option(scale)
                page.locator('.result-tabs').scroll_into_view_if_needed()
                page.evaluate('() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                bounds = page.locator('.result-tabs').evaluate('''bar => {
                    const b=bar.getBoundingClientRect();
                    return {bar:b.toJSON(), clientHeight:bar.clientHeight, scrollHeight:bar.scrollHeight,
                        flexShrink:getComputedStyle(bar).flexShrink,
                        documentWidth:document.documentElement.scrollWidth,
                        tabs:[...bar.querySelectorAll('button')].map(button=>{
                            const r=button.getBoundingClientRect();const range=document.createRange();
                            range.selectNodeContents(button);const text=range.getBoundingClientRect();
                            return {label:button.textContent,rect:r.toJSON(),text:text.toJSON(),
                                hit:document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.closest('.result-tabs')===bar};
                        })};
                }''')
                observations.append({'viewport':[width,height], 'fontScale':scale, **bounds})
                page.screenshot(path=str(tmp_path/f'result-tabs-{width}-{height}-{scale}.png'))
                if bounds['documentWidth'] > width+1:
                    violations.append(f'Outer page overflow at {width}/{scale}')
                for tab in bounds['tabs']:
                    if tab['rect']['top'] < bounds['bar']['top']-1 or tab['rect']['bottom'] > bounds['bar']['bottom']+1:
                        violations.append(f'Tab vertically clipped at {width}x{height}/{scale}: {tab["label"]}')
                    if not tab['hit']:
                        violations.append(f'Tab hit target obscured at {width}x{height}/{scale}: {tab["label"]}')
                if not violations:
                    page.locator('.result-tab[data-result-tab="charts"]').click()
                    assert page.locator('#resultCharts').is_visible()
                    canvas = page.locator('#telemetryChart')
                    canvas.scroll_into_view_if_needed()
                    page.evaluate('() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                    visible = canvas.evaluate('''canvas => {
                        const r=canvas.getBoundingClientRect();
                        const clip={left:0,top:0,right:innerWidth,bottom:innerHeight};
                        for(let p=canvas.parentElement;p;p=p.parentElement){
                            const style=getComputedStyle(p);const b=p.getBoundingClientRect();
                            // Client/offset sizes use CSS pixels; DOMRects include
                            // the workbench's CSS zoom. Compare one coordinate space.
                            const sx=p.offsetWidth?b.width/p.offsetWidth:1;
                            const sy=p.offsetHeight?b.height/p.offsetHeight:1;
                            if(style.overflowX!=='visible'){clip.left=Math.max(clip.left,b.left+p.clientLeft*sx);clip.right=Math.min(clip.right,b.left+(p.clientLeft+p.clientWidth)*sx);}
                            if(style.overflowY!=='visible'){clip.top=Math.max(clip.top,b.top+p.clientTop*sy);clip.bottom=Math.min(clip.bottom,b.top+(p.clientTop+p.clientHeight)*sy);}
                        }
                        const panel=document.querySelector('#resultCharts');
                        return {rect:r.toJSON(),clip,
                            fullyVisible:r.left>=clip.left-1&&r.right<=clip.right+1&&r.top>=clip.top-1&&r.bottom<=clip.bottom+1,
                            clientHeight:panel.clientHeight,scrollHeight:panel.scrollHeight,scrollTop:panel.scrollTop};
                    }''')
                    observations[-1]['canvas'] = visible
                    page.screenshot(path=str(tmp_path/f'curve-visible-{width}-{height}-{scale}.png'))
                    if not visible['fullyVisible']:
                        violations.append(f'Canvas clipped by viewport or ancestor at {width}x{height}/{scale}')
                    page.locator('.result-tab[data-result-tab="overview"]').click()
                    assert page.locator('#resultOverview').is_visible()
            page.set_viewport_size({'width':1366, 'height':900})
            page.locator('#fontScaleSelect').select_option('100')
            page.locator('#openModelSettingsBtn').click()
            page.locator('#modelSettingsModal').wait_for(state='visible')
            builtin = page.locator('#modelProviderList .provider-item').filter(has_text='本地确定性模板')
            unprobed = page.locator('#modelProviderList .provider-item').filter(has_text='unprobed-ui-test-model')
            labels = {'builtin':builtin.inner_text(), 'unprobed':unprobed.inner_text(),
                'builtin_option':page.locator('#agentBackend option[value="local-template"]').inner_text()}
            page.screenshot(path=str(tmp_path/'provider-status-1366.png'))
            if '完整测试通过' in labels['builtin'] or '尚无完整测试记录' in labels['builtin']:
                violations.append('Built-in template misrepresents real-model service testing')
            if '内置模板可用' not in labels['builtin'] or '内置模板可用' not in labels['builtin_option']:
                violations.append('Built-in availability is not clearly distinguished')
            if '未完整测试' not in labels['unprobed'] or '尚无完整测试记录' not in labels['unprobed']:
                violations.append('Unprobed model provider lost its untested status')
            evidence = {'scope':'Real completed thermal run and ordinary local API; no model generation or GPU request',
                'source_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in
                    [Path('src/sat_sim/web/app.js'),Path('src/sat_sim/web/styles.css')]},
                'run_id':prepared.run_id, 'observations':observations, 'provider_labels':labels,
                'violations':violations, 'page_errors':errors}
            (tmp_path/'presentation-proof.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
            assert not violations, violations
            assert not errors
        finally:
            browser.close()
