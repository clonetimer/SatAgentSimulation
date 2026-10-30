"""Read-only same-session layout comparison; replace UI assets only for baseline."""
from pathlib import Path
from urllib.parse import urlsplit
import json
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'reports/local_requirements_completion/compact-realtime-layout-comparison'
OUT.mkdir(exist_ok=False)
URL='http://127.0.0.1:62918'
report={'scope':'Read-only original UI assets versus candidate UI over the same actual native API and completed session. No mocked API/data or new simulation. Rendered text includes offscreen content; it is not a first-screen word count.', 'observations':[]}
with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
    for phase in ['before','after']:
        context=browser.new_context(viewport={'width':1366,'height':900})
        page=context.new_page()
        if phase=='before':
            def original_assets(route):
                path=urlsplit(route.request.url).path
                asset=ROOT/'src/sat_sim/web'/('index.html' if path=='/' else path.removeprefix('/assets/'))
                if (path=='/' or path.startswith('/assets/')) and asset.is_file():
                    content_type='text/html' if asset.suffix=='.html' else 'text/css' if asset.suffix=='.css' else 'application/javascript' if asset.suffix=='.js' else 'application/octet-stream'
                    route.fulfill(body=asset.read_bytes(),content_type=content_type)
                else: route.continue_()
            page.route(URL+'/**',original_assets)
        page.goto(URL,wait_until='networkidle')
        page.locator('#continuousNavButton').click()
        page.wait_for_function('()=>document.querySelector("#continuousState").textContent.includes("已完成")')
        page.wait_for_function('()=>document.querySelector("#continuousOrbit").textContent.includes("m")')
        page.wait_for_timeout(400)
        page.evaluate('scrollTo(0,0)')
        row=page.evaluate(r'''()=>({
            session:document.querySelector('#continuousSessionList .active strong').textContent,
            renderedText:document.querySelector('#continuousView').innerText.replace(/\s/g,'').length,
            chartTop:document.querySelector('#continuousChart').getBoundingClientRect().top,
            viewport:innerHeight,
            state:document.querySelector('#continuousState').textContent,
            time:document.querySelector('#continuousSimTime').textContent,
            orbit:document.querySelector('#continuousOrbit').textContent
        })''')
        row['phase']=phase; report['observations'].append(row)
        page.screenshot(path=str(OUT/(phase+'-1366.png')))
        context.close()
    browser.close()
before,after=report['observations']
report['checks']={'same_actual_session':before['session']==after['session'],
                  'same_actual_state':all(before[k]==after[k] for k in ['state','time','orbit']),
                  'chart_moved_forward':after['chartTop']<before['chartTop']}
assert all(report['checks'].values())
report['status']='PASS_REQUIRES_SCREENSHOT_REVIEW'
(OUT/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps(report,ensure_ascii=True))
