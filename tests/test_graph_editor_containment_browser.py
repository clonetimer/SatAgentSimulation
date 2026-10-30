"""Canvas panning must not move or clip the surrounding editor after export."""
import json
import os
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest

from sat_sim.api import create_app


@pytest.mark.parametrize('font_scale', ['100','130'])
def test_graph_export_and_viewport_changes_keep_editor_content_inside_panel(tmp_path,font_scale):
    playwright=pytest.importorskip('playwright.sync_api')
    executable=os.environ.get('SAT_SIM_BROWSER_EXECUTABLE')
    if not executable:
        pytest.skip('Set SAT_SIM_BROWSER_EXECUTABLE to installed Chromium')
    app=create_app(runs_root=tmp_path/'runs',artifacts_root=tmp_path/'api',embedded_worker=False,auth_mode='disabled')
    evidence=[]
    with TestClient(app) as client,playwright.sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=executable)
        try:
            page=browser.new_page(viewport={'width':1600,'height':1000})
            def proxy(route):
                request=route.request
                url=urlsplit(request.url)
                result=client.request(request.method,url.path+('?' + url.query if url.query else ''),
                    content=request.post_data_buffer,headers={k:v for k,v in request.headers.items() if k in ('content-type','accept')})
                route.fulfill(status=result.status_code,body=result.content,
                    headers={'content-type':result.headers.get('content-type','application/octet-stream')})
            page.route('https://workbench.test/**',proxy)
            page.goto('https://workbench.test/',wait_until='networkidle')
            page.wait_for_selector('.capability-card.active')
            page.locator('#fontScaleSelect').select_option(font_scale)
            page.locator('#previewBtn').click()
            page.wait_for_function("document.querySelector('#notice').className.includes('success')")
            page.locator('#creatorGraphTabButton').click()
            page.locator('#graphFlowModeBtn').click()
            with page.expect_response(lambda response:response.url.endswith('/tasks/export-script')):
                page.locator('#graphGenerateCodeBtn').click()
            page.locator('.graph-node[data-node-id="graph-code"]').click()
            # Clicking the inspector download forces its real focus/scroll path.
            with page.expect_download():
                page.locator('#graphInspector').get_by_role('button',name='下载 .py',exact=True).click()
            for mode in ['flow','assembly','flow']:
                page.locator('#graphFlowModeBtn' if mode=='flow' else '#graphAssemblyModeBtn').click()
                for width,height in [(1920,1080),(1366,900),(768,1024),(390,844),(1600,1000)]:
                    page.set_viewport_size({'width':width,'height':height})
                    page.evaluate('() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                    bounds=page.evaluate('''() => {
                        const editor=document.querySelector('.editor-panel');
                        const panel=editor.getBoundingClientRect();
                        return {scrollLeft:editor.scrollLeft,client:editor.clientWidth,scroll:editor.scrollWidth,
                            panelX:panel.x,panelRight:panel.right,
                            headers:[...document.querySelectorAll('.editor-toolbar,.tabbar')].map(e=>({x:e.getBoundingClientRect().x,right:e.getBoundingClientRect().right})),
                            topbar:[...document.querySelectorAll('.topbar,.topbar-tools,.topbar-status,.workspace,.app-content')].map(e=>({cls:e.className,rect:e.getBoundingClientRect().toJSON(),scroll:e.scrollWidth,client:e.clientWidth})),
                            documentWidth:document.documentElement.scrollWidth};
                    }''')
                    evidence.append({'mode':mode,'width':width,'font':font_scale,**bounds})
                    (tmp_path/'editor-containment.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
                    assert bounds['scroll']<=bounds['client']+1,evidence
                    assert bounds['scrollLeft']==0,evidence
                    assert all(x['x']>=bounds['panelX']-1 and x['right']<=bounds['panelRight']+1 for x in bounds['headers']),evidence
                    assert bounds['documentWidth']<=width+1,evidence
            (tmp_path/'editor-containment.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
        finally:
            browser.close()
