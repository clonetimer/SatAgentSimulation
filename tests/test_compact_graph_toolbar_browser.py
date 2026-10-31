"""Actual Chrome: bounded toolbar layout and public native disclosure controls.

R46 static assets are replayed for the limited before images; Python backend
files are identical. Candidate uses its actual assets. No model/native calls.
"""
from pathlib import Path
import hashlib
import json
import os
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest
from sat_sim.api import create_app


@pytest.mark.parametrize('width,height,font', [(1440,1000,'100'),(390,844,'130')])
def test_compact_toolbar_public_keyboard_and_mobile_layout(tmp_path,width,height,font):
    playwright=pytest.importorskip('playwright.sync_api')
    executable=os.environ.get('SAT_SIM_BROWSER_EXECUTABLE')
    if not executable:
        pytest.skip('Set SAT_SIM_BROWSER_EXECUTABLE to installed Chromium')
    source=Path(create_app.__code__.co_filename).resolve().parents[2]
    baseline=source.parent/'requirements-r46-interaction-ownership-candidate'
    assert baseline.is_dir()
    app=create_app(runs_root=tmp_path/'runs',artifacts_root=tmp_path/'api',embedded_worker=False,auth_mode='disabled')
    facts={'scope':'Actual Chrome/CPU TestClient only. R46 asset replay before; no model/native execution.',
           'viewport':[width,height],'font':font,'phases':{},'errors':[],'requests':[]}
    def save():
        (tmp_path/'toolbar.json').write_text(json.dumps(facts,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    with TestClient(app) as client,playwright.sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=executable)
        try:
            for phase,asset_root in [('before',baseline/'src/sat_sim/web'),('after',source/'src/sat_sim/web')]:
                context=browser.new_context(viewport={'width':width,'height':height},accept_downloads=True)
                page=context.new_page(); page.on('pageerror',lambda error:facts['errors'].append(str(error)))
                phase_facts={'assets':{},'modes':{}}; facts['phases'][phase]=phase_facts
                def proxy(route):
                    request=route.request; url=urlsplit(request.url)
                    facts['requests'].append({'phase':phase,'method':request.method,'path':url.path})
                    assert not (request.method=='POST' and url.path.startswith(('/runs','/interactive/'))),url.path
                    response=client.request(request.method,url.path+('?'+url.query if url.query else ''),
                        content=request.post_data_buffer,headers={k:v for k,v in request.headers.items() if k in ('content-type','accept')})
                    content=response.content
                    asset=asset_root/'index.html' if url.path=='/' else asset_root/url.path.removeprefix('/assets/') if url.path.startswith('/assets/') else None
                    if asset is not None and asset.is_file() and asset.resolve().is_relative_to(asset_root.resolve()):
                        content=asset.read_bytes(); phase_facts['assets'][url.path]=hashlib.sha256(content).hexdigest()
                    route.fulfill(status=response.status_code,body=content,headers={k:v for k,v in response.headers.items() if k in ('content-type','content-disposition','content-security-policy')})
                page.route('https://workbench.test/**',proxy)
                page.goto('https://workbench.test/',wait_until='networkidle')
                page.locator('.capability-card.active').wait_for(state='attached')
                if font!='100':
                    page.locator('#toggleMobileToolsBtn').focus();page.keyboard.press('Enter')
                    page.locator('#fontScaleSelect').select_option(font)
                    page.locator('#toggleMobileToolsBtn').click()
                page.locator('#creatorGraphTabButton').click()
                for mode in ['flow','assembly']:
                    page.locator('#graphFlowModeBtn' if mode=='flow' else '#graphAssemblyModeBtn').click()
                    if mode=='assembly':
                        page.wait_for_function('() => document.querySelector("#assemblyStatus").textContent.startsWith("已加载")')
                    toolbar='#graphFlowToolbarActions' if mode=='flow' else '.assembly-toolbar'
                    status='#graphStatus' if mode=='flow' else '#assemblyStatus'
                    page.locator(toolbar).scroll_into_view_if_needed()
                    page.evaluate('() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                    visible_buttons=[node.get_attribute('id') for node in page.locator(toolbar+' button').all() if node.is_visible()]
                    geometry=page.locator(toolbar).bounding_box()
                    phase_facts['modes'][mode]={'visible_buttons':visible_buttons,'geometry':geometry,'keyboard_groups':{}}
                    page.screenshot(path=str(tmp_path/f'{phase}-{mode}-{width}-font{font}.png'))
                    save()
                    if phase=='before':
                        continue
                    expected_main=['graphValidateBtn','graphGenerateCodeBtn','graphRunBtn'] if mode=='flow' else ['assemblyValidateBtn','assemblyGenerateCodeBtn','assemblyRunBtn']
                    assert visible_buttons==expected_main
                    assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth+1')
                    assert 'Visual Composer' not in page.locator('#graphTab').inner_text()
                    assert 'Rapid Graph Programming' not in page.locator('#graphTab').inner_text()
                    assert not any(token in page.locator('#graphTab').inner_text() for token in ('V15','V8–V14','V4','V5','V12'))
                    for index,group_id in enumerate([mode+'EditActions',mode+'ProjectActions'] if mode=='assembly' else ['graphEditActions','graphProjectActions']):
                        group=page.locator('#'+group_id); summary=group.locator(':scope > summary')
                        assert group.get_attribute('open') is None
                        summary.focus();page.keyboard.press('Enter' if index==0 else 'Space')
                        assert group.get_attribute('open') is not None
                        controls=group.locator('button, select')
                        assert all(control.is_visible() for control in controls.all())
                        expected=[control.get_attribute('id') for control in group.locator('button:enabled, select:enabled').all()]
                        seen=[]
                        for _ in range(len(expected)+2):
                            page.keyboard.press('Tab')
                            active=page.evaluate('() => document.activeElement.id')
                            if active in expected:seen.append(active)
                            assert page.evaluate('() => { const r=document.activeElement.getBoundingClientRect(); return r.width>0 && r.height>0 && r.left>=-1 && r.right<=innerWidth+1 && r.top>=-1 && r.bottom<=innerHeight+1; }')
                            if set(seen)==set(expected):break
                        assert set(seen)==set(expected),(group_id,seen,expected)
                        phase_facts['modes'][mode]['keyboard_groups'][group_id]={'enabled':expected,'tab_reached':seen,'all_controls_visible':True}
                        if (width==1440 and mode=='flow' and index==0) or (width==390 and mode=='assembly' and index==1):
                            summary.scroll_into_view_if_needed()
                            page.screenshot(path=str(tmp_path/f'after-{group_id}-keyboard-{width}.png'))
                        page.keyboard.press('Escape')
                        assert group.get_attribute('open') is None
                        assert summary.evaluate('(node) => node===document.activeElement')
                    page.locator(status).scroll_into_view_if_needed()
                    assert page.locator(status).is_visible() and page.locator(status).locator('xpath=ancestor::details').count()==0
                    save()
                if phase=='after':
                    # Reversible edit and engineering actions use original public
                    # buttons; native validation errors stay outside disclosures.
                    page.locator('#graphFlowModeBtn').click()
                    edit=page.locator('#graphEditActions');edit.locator('summary').focus();page.keyboard.press('Enter')
                    page.locator('#graphClearBtn').click()
                    page.locator('#graphValidateBtn').click()
                    assert 'error' in (page.locator('#graphStatus').get_attribute('class') or '')
                    page.locator('#graphStatus').scroll_into_view_if_needed();assert page.locator('#graphStatus').is_visible()
                    page.locator('#graphResetBtn').click()
                    assert page.locator('#graphNodes .graph-node').count()>0
                    edit.locator('summary').focus();page.keyboard.press('Enter')
                    project=page.locator('#graphProjectActions');project.locator('summary').focus();page.keyboard.press('Space')
                    page.locator('#graphSaveDraftBtn').click();page.locator('#graphLoadDraftBtn').click()
                    with page.expect_download() as exported:page.locator('#graphExportProjectBtn').click()
                    target=tmp_path/'actual-exported-project.json';exported.value.save_as(str(target))
                    with page.expect_file_chooser() as imported:page.locator('#graphImportProjectBtn').click()
                    imported.value.set_files(str(target))
                    page.wait_for_function('() => document.querySelector("#graphStatus").classList.contains("success") && document.querySelector("#graphStatus").textContent.includes("已载入并通过")')
                    assert page.locator('#graphNodes .graph-node').count()>0
                    with page.expect_download() as roundtrip:
                        page.locator('#graphExportProjectBtn').click()
                    restored=tmp_path/'roundtrip-exported-project.json'
                    roundtrip.value.save_as(str(restored))
                    assert json.loads(restored.read_text(encoding='utf-8')) == json.loads(target.read_text(encoding='utf-8'))
                    phase_facts['reversible_project_roundtrip']=True
                    save()
                context.close()
            assert not facts['errors']
        finally:
            save();browser.close()
