"""Actual Chrome regression of current toolbar and public disclosure controls.

Historical before/after comparisons remain in the frozen R48 evidence packet.
This portable regression requires only the current checkout, no local candidate
directories. All current layout, keyboard and project roundtrip checks remain.
No model/native calls.
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
    app=create_app(runs_root=tmp_path/'runs',artifacts_root=tmp_path/'api',embedded_worker=False,auth_mode='disabled')
    facts={'scope':'Actual Chrome/CPU TestClient over current checkout only; no model/native execution.',
           'viewport':[width,height],'font':font,'phases':{},'errors':[],'requests':[]}
    def save():
        (tmp_path/'toolbar.json').write_text(json.dumps(facts,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    with TestClient(app) as client,playwright.sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=executable)
        try:
            for phase,asset_root in [('after',source/'src/sat_sim/web')]:
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
                tools = page.locator('#wizardAdditionalTools')
                tools.locator('summary').click()
                assert tools.get_attribute('open') is not None
                tools.locator('[data-wizard-goal="graph"]').click()
                page.locator('#creatorGraphTabButton').wait_for(state='visible')
                page.locator('#graphConversationGuide').wait_for(state='visible')
                assert '建模助手' in page.locator('#graphConversationGuide').inner_text()
                assert page.locator('#graphGuideNextTitle').inner_text().strip()
                assert page.locator('#graphGuideNextDetail').inner_text().strip()
                page.locator('#graphGuideChooseModuleBtn').click()
                assert page.locator('#graphCapabilitySearch').evaluate(
                    '(node) => node === document.activeElement'
                )
                capability = page.locator('#graphCapabilityPalette .graph-palette-item').first
                capability.dblclick()
                page.wait_for_function(
                    '() => document.querySelector("#graphStatus").textContent.includes("已装入执行能力")'
                )
                assert not page.locator('#graphGuideRestoreBtn').is_disabled()
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
                    # On desktop, prove the module palette performs real HTML
                    # drag/drop. At narrow widths keep the same canvas and
                    # keyboard checks without assuming touch drag support.
                    if width >= 760:
                        canvas_box = page.locator('#graphCanvas').bounding_box()
                        assert canvas_box and canvas_box['width'] > 40 and canvas_box['height'] > 40
                        drop_position = {
                            'x': min(160, max(20, canvas_box['width'] - 20)),
                            'y': min(160, max(20, canvas_box['height'] - 20)),
                        }
                        page.locator('[data-graph-block="config"]').drag_to(
                            page.locator('#graphCanvas'), target_position=drop_position
                        )
                        assert page.locator('#graphNodes .graph-node').count() == 1
                        assert page.locator('.graph-node[data-node-id="graph-config"]').is_visible()
                    page.locator('#graphResetBtn').click()
                    assert page.locator('#graphNodes .graph-node').count()>0

                    # Exercise the conversational guide against the real graph
                    # controller: confirmed restore, constrained auto-wire,
                    # and server-side graph compilation (no simulation run).
                    restore_dialogs = []
                    page.once('dialog', lambda dialog: (restore_dialogs.append(dialog.message), dialog.accept()))
                    page.locator('#graphGuideRestoreBtn').click()
                    assert restore_dialogs and '恢复推荐流程' in restore_dialogs[0]
                    assert page.locator('#graphNodes .graph-node').count() == 6
                    wire_dialogs = []
                    page.once('dialog', lambda dialog: (wire_dialogs.append(dialog.message), dialog.accept()))
                    page.locator('#graphGuideAutoWireBtn').click()
                    assert wire_dialogs and '自动布线' in wire_dialogs[0]
                    assert page.locator('#graphEdges .graph-edge').count() == 6
                    with page.expect_response(lambda response: response.url.endswith('/tasks/compile-graph')) as compile_response:
                        page.locator('#graphGuideValidateBtn').click()
                    assert compile_response.value.ok
                    assert 'success' in (page.locator('#graphStatus').get_attribute('class') or '')
                    assert page.locator('#graphGuideNextTitle').inner_text() == '正式校验已通过，可以生成 Python'

                    # Real HTML node drag plus undo/redo on desktop; narrow
                    # viewports retain the responsive and keyboard contracts.
                    node = page.locator('.graph-node[data-node-id="graph-config"]')
                    assert node.is_visible()
                    if width >= 760:
                        canvas = page.locator('#graphCanvas')
                        canvas_box = canvas.bounding_box()
                        node_box = node.bounding_box()
                        assert canvas_box and node_box
                        relative_left = node_box['x'] - canvas_box['x']
                        relative_top = node_box['y'] - canvas_box['y']
                        right_target = relative_left + node_box['width'] + 100
                        target_x = (
                            right_target if right_target <= canvas_box['width'] - 24
                            else max(24, relative_left - 100)
                        )
                        target_y = min(
                            canvas_box['height'] - 24,
                            max(70, relative_top + node_box['height'] / 2),
                        )
                        head = node.locator('.graph-node-head')
                        head_box = head.bounding_box()
                        viewport = page.viewport_size
                        assert head_box and viewport
                        visible_width = min(canvas_box['width'], viewport['width'] - canvas_box['x'])
                        visible_height = min(canvas_box['height'], viewport['height'] - canvas_box['y'])
                        target_abs_x = min(
                            canvas_box['x'] + visible_width - 24,
                            viewport['width'] - 12,
                        )
                        target_abs_y = min(
                            canvas_box['y'] + visible_height - 24,
                            viewport['height'] - 12,
                        )
                        page.evaluate("""() => {
                            window.__graphDragTrace = [];
                            for (const name of ['dragstart', 'dragover', 'drop', 'dragend']) {
                                document.addEventListener(name, (event) => {
                                    window.__graphDragTrace.push({
                                        type: name,
                                        target: event.target.id || event.target.className || event.target.tagName,
                                        payload: name === 'drop'
                                            ? (event.dataTransfer.getData('application/x-sat-graph') || event.dataTransfer.getData('text/plain'))
                                            : '',
                                    });
                                }, true);
                            }
                        }""")
                        before = node.evaluate('(el) => [el.style.left, el.style.top]')
                        page.mouse.move(head_box['x'] + 28, head_box['y'] + 16)
                        page.mouse.down()
                        page.mouse.move(target_abs_x, target_abs_y, steps=16)
                        page.mouse.up()
                        after = node.evaluate('(el) => [el.style.left, el.style.top]')
                        drag_trace = page.evaluate('() => window.__graphDragTrace')
                        assert any(item['type'] == 'dragstart' for item in drag_trace), drag_trace
                        drop_events = [item for item in drag_trace if item['type'] == 'drop']
                        assert drop_events and 'move-node' in drop_events[-1]['payload'], drag_trace
                        assert after != before, {'before': before, 'after': after, 'trace': drag_trace, 'target': [target_abs_x, target_abs_y]}
                        page.locator('#graphUndoBtn').click()
                        assert node.evaluate('(el) => [el.style.left, el.style.top]') == before
                        page.locator('#graphRedoBtn').click()
                        assert node.evaluate('(el) => [el.style.left, el.style.top]') == after

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
