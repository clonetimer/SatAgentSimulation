"""Real Chromium navigation acceptance; API journeys do not claim native physics."""
from pathlib import Path
import json
import os
import re
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'src/sat_sim/web'


def _browser(pw):
    executable = os.environ.get('SAT_SIM_BROWSER_EXECUTABLE')
    if not executable:
        pytest.skip('Set SAT_SIM_BROWSER_EXECUTABLE to installed Chromium')
    return pw.chromium.launch(headless=True, executable_path=executable)


def _evidence(page, tmp_path, name, facts):
    root = Path(os.environ.get('SAT_SIM_P9G_EVIDENCE', str(tmp_path))) / name
    root.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(root / 'navigation.png'), full_page=True)
    (root / 'report.json').write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding='utf-8')


@pytest.mark.parametrize('width,height', [(1440, 1000), (390, 844)])
def test_default_navigation_layout_is_visible_without_runtime(tmp_path, width, height):
    """Production DOM/CSS in Chromium; explicitly layout-only, no API/CSP claim."""
    pw = pytest.importorskip('playwright.sync_api')
    with pw.sync_playwright() as p:
        browser = _browser(p)
        try:
            page = browser.new_page(viewport={'width': width, 'height': height})
            html = re.sub(r'<script\b[^>]*>[\s\S]*?</script>', '', (WEB / 'index.html').read_text(encoding='utf-8'))
            html = re.sub(r'<link[^>]*rel="stylesheet"[^>]*>', '', html)
            page.set_content(html, wait_until='domcontentloaded')
            for name in ['styles.css', 'continuous.css', 'compact-workbench.css', 'satellite-scene.css']:
                page.add_style_tag(content=(WEB / name).read_text(encoding='utf-8'))
            # Loading the same adapter and stylesheet as production, without
            # serving the API, isolates the navigation-layout regression.
            page.add_script_tag(content=(WEB / 'compact-workbench.js').read_text(encoding='utf-8'))
            extra = WEB / 'workbench-navigation.css'
            if extra.is_file():
                page.add_style_tag(content=extra.read_text(encoding='utf-8'))
            nav = page.locator('.sidebar-nav')
            primary = nav.locator(':scope > button[data-view]')
            facts = {'scope': 'layout only', 'width': width, 'labels': primary.locator('b').all_text_contents()}
            _evidence(page, tmp_path, f'layout-{width}', facts)
            assert facts['labels'] == ['快速仿真', '任务中心', '卫星数字场景', '专业建模']
            assert all(button.is_visible() for button in primary.all())
            assert all(label.is_visible() for label in primary.locator('b').all())
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            # Layout-only checks of the production view selectors. Moving the
            # navigation must not leave a reserved, empty catalog column.
            for view in ('scene', 'runs', 'experiments', 'goals', 'evaluations', 'diagnostics', 'logs'):
                page.evaluate('(view) => { document.body.dataset.activeView = view; }', view)
                assert page.locator('.app-sidebar').is_hidden(), view
                assert page.locator('.app-content').bounding_box()['x'] <= 1, view
        finally:
            browser.close()


@pytest.mark.parametrize('width,height', [(1440, 1000), (390, 844)])
def test_navigation_uses_existing_routes_and_preserves_task_form(tmp_path, monkeypatch, width, height):
    from fastapi.testclient import TestClient
    from sat_sim.api import create_app
    pw = pytest.importorskip('playwright.sync_api')
    monkeypatch.setenv('SAT_SIM_INTERACTIVE_ENABLED', '0')
    app = create_app(runs_root=tmp_path/'runs', artifacts_root=tmp_path/'api', embedded_worker=False, auth_mode='disabled')
    requests, errors = [], []
    with TestClient(app) as client, pw.sync_playwright() as p:
        browser = _browser(p)
        try:
            page = browser.new_page(viewport={'width': width, 'height': height})
            page.on('pageerror', lambda error: errors.append(str(error)))
            def proxy(route):
                request = route.request
                url = urlsplit(request.url)
                assert url.netloc == 'workbench.test'
                requests.append((request.method, url.path))
                response = client.request(request.method, url.path + ('?' + url.query if url.query else ''),
                    content=request.post_data_buffer,
                    headers={k:v for k,v in request.headers.items() if k in ('content-type','accept')})
                route.fulfill(status=response.status_code, body=response.content,
                    headers={k:v for k,v in response.headers.items() if k in ('content-type','content-security-policy')})
            page.route('https://workbench.test/**', proxy)
            page.goto('https://workbench.test/', wait_until='networkidle')
            page.locator('.capability-card.active').wait_for(state='attached')
            nav = page.locator('.sidebar-nav')
            primary = nav.locator(':scope > button[data-view]')
            assert primary.count() == 4 and all(item.is_visible() for item in primary.all())
            assert page.locator('#wizardView').is_visible()
            # The independent professional editor remains an actual route.
            nav.get_by_role('button', name=re.compile(r'专业建模$')).click()
            assert page.locator('#creatorView').is_visible()
            inputs = page.locator('#dynamicForm input:not([type=checkbox]):not([type=radio])')
            initial = inputs.evaluate_all('(items) => items.map(x => [x.id, x.value])')
            assert initial
            nav.get_by_role('button', name=re.compile(r'任务中心$')).click()
            assert page.locator('#wizardTaskWorkspace').is_visible()
            nav.get_by_role('button', name=re.compile(r'快速仿真$')).click()
            assert not page.locator('#wizardTaskWorkspace').is_visible()
            pw.expect(page.get_by_role('button', name='运行正常仿真')).to_be_visible()
            nav.get_by_role('button', name=re.compile(r'专业建模$')).click()
            assert inputs.evaluate_all('(items) => items.map(x => [x.id, x.value])') == initial
            more = page.locator('#workbenchMoreNavigation')
            assert more.get_attribute('open') is None
            more.locator(':scope > summary').focus()
            page.keyboard.press('Enter')
            analysis = nav.locator('.sidebar-nav-group').filter(has=page.locator('summary', has_text='实验与分析'))
            analysis.locator(':scope > summary').click()
            for route_name, view_id in [('实验中心','experimentCenterView'), ('目标实验','goalRunView'), ('运行中心','runCenterView'), ('效能评估','evaluationView')]:
                nav.get_by_role('button', name=re.compile(re.escape(route_name) + r'$')).click()
                assert page.locator('#'+view_id).is_visible()
            assert page.locator('#interactiveNavButton').is_hidden()
            realtime = nav.locator('.sidebar-nav-group').filter(has=page.locator('summary', has_text='实时仿真'))
            assert realtime.is_hidden()
            more.locator(':scope > summary').focus()
            page.keyboard.press('Escape')
            assert more.get_attribute('open') is None
            assert more.locator(':scope > summary').evaluate('(node) => node === document.activeElement')
            # Help is read-only and returns keyboard focus to its opener.
            page.locator('#workbenchHelpBtn').click()
            assert page.get_by_role('dialog', name='使用帮助').is_visible()
            assert '专业建模' in page.get_by_role('dialog').inner_text()
            page.keyboard.press('Escape')
            assert page.get_by_role('dialog', name='使用帮助').is_hidden()
            assert page.locator('#workbenchHelpBtn').evaluate('(node) => node === document.activeElement')
            assert not [r for r in requests if r[0]=='POST' and r[1].startswith(('/runs','/interactive/'))]
            assert not errors, errors
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')

            # P9-G3: navigation is bookmarkable and participates in browser history.
            page.evaluate("window.history.replaceState({}, '', window.location.pathname + window.location.search)")
            nav.get_by_role('button', name='任务中心').click()
            assert page.url.endswith('#view=tasks'), page.url
            nav.get_by_role('button', name='卫星数字场景').click()
            assert page.url.endswith('#view=scene'), page.url
            page.go_back()
            assert page.locator('#wizardTaskWorkspace').is_visible()
            assert page.url.endswith('#view=tasks'), page.url
            page.go_forward()
            assert page.locator('#satelliteSceneView').is_visible()
            assert page.url.endswith('#view=scene'), page.url
            bookmarked = page.url
            page.reload(wait_until='networkidle')
            assert page.locator('#satelliteSceneView').is_visible()
            assert page.url == bookmarked
            page.goto('https://workbench.test/#view=creator', wait_until='networkidle')
            assert page.locator('#creatorView').is_visible()
            assert page.url.endswith('#view=creator'), page.url
            _evidence(page, tmp_path, f'journey-{width}', {'scope':'real browser and API, no native execution', 'requests':requests, 'errors':errors, 'history':'back-forward-reload-direct-link'})
        finally:
            browser.close()
