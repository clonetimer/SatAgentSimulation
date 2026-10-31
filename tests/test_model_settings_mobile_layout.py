"""Real browser dialog containment; local API proxy, no inference claim."""
import os
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest

from sat_sim.api import create_app


@pytest.mark.parametrize('width,height',[(390,844),(768,1024),(1366,900),(1920,1080)])
def test_model_settings_fit_the_viewport_at_large_font_and_restore_focus(tmp_path,width,height):
    playwright=pytest.importorskip('playwright.sync_api')
    executable=os.environ.get('SAT_SIM_BROWSER_EXECUTABLE')
    if not executable:
        pytest.skip('Set SAT_SIM_BROWSER_EXECUTABLE to an installed Chromium browser')
    app=create_app(runs_root=tmp_path/'runs',artifacts_root=tmp_path/'api',
                   embedded_worker=False,auth_mode='disabled')
    with TestClient(app) as client,playwright.sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=executable)
        page=browser.new_page(viewport={'width':1366,'height':900})
        def proxy(route):
            request=route.request
            url=urlsplit(request.url)
            response=client.request(request.method,url.path+('?'+url.query if url.query else ''),
                content=request.post_data_buffer,headers={k:v for k,v in request.headers.items()
                    if k in ('content-type','accept')})
            route.fulfill(status=response.status_code,body=response.content,
                headers={'content-type':response.headers.get('content-type','application/octet-stream')})
        page.route('https://workbench.test/**',proxy)
        page.goto('https://workbench.test/',wait_until='networkidle')
        settings_toggle = page.locator('#toggleMobileToolsBtn')
        if settings_toggle.get_attribute('aria-expanded') != 'true':
            settings_toggle.focus()
            settings_toggle.press('Enter')
        page.locator('#fontScaleSelect').select_option('130')
        page.set_viewport_size({'width':width,'height':height})
        page.locator('#openModelSettingsBtn').click()
        page.wait_for_selector('#modelSettingsModal:not(.hidden)')
        page.locator('#localModelName').fill('Qwen2.5-Coder-1.5B-SatAgent-v5-guided-complete-dataset')
        card=page.locator('#modelSettingsModal .modal-card').bounding_box()
        assert card and card['x']>=0 and card['y']>=0
        assert card['x']+card['width']<=width+1
        assert card['y']+card['height']<=height+1
        assert page.evaluate('document.documentElement.scrollWidth')<=width+1
        assert page.locator('#closeModelSettingsBtn').is_visible()
        page.keyboard.press('Escape')
        assert not page.locator('#modelSettingsModal').is_visible()
        assert page.locator('#openModelSettingsBtn').evaluate('(node)=>node===document.activeElement')
        browser.close()
