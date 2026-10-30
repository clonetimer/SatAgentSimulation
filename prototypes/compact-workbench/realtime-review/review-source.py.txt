"""Headless browser acceptance of the isolated real-time UI and real native API."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import time
import traceback
import urllib.request
import runpy
from html.parser import HTMLParser
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'reports/local_requirements_completion/compact-realtime-ui-review-v10'
SOURCE = ROOT/'reports/local_requirements_completion/compact-ui-preview-k0l0_yof'
URL = 'http://127.0.0.1:62918'
BOUNDED_URL = 'http://127.0.0.1:49189'
SID = 'compact-ui-review-' + str(int(time.time()))
OUT.mkdir(exist_ok=False)
report = {'scope': 'Isolated browser and actual native child. No mock API/telemetry. Not a full production or endurance qualification. Prior v1 failed because an expression-style browser wait violated CSP; retained unchanged.',
          'checks': {}, 'errors': [], 'mutations': [], 'observations': [], 'session_id': SID,
          'started_utc': datetime.now(timezone.utc).isoformat()}
fingerprints = runpy.run_path(str(ROOT/'scripts/run_native_endurance.py'))
fp = fingerprints['runtime_source_fingerprint']
report['application'] = fp(fingerprints['application_source_manifest'](SOURCE))
report['core'] = fp(fingerprints['runtime_source_manifest'](SOURCE))
report['root_application_before'] = fp(fingerprints['application_source_manifest']())
bounded_id = None

def api(path, body=None, base=URL):
    request = urllib.request.Request(base+path, data=None if body is None else json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)

def check(name, value):
    report['checks'][name] = bool(value)
    assert value, name

def capture(page, label):
    page.evaluate('scrollTo(0,0)')
    page.wait_for_timeout(350)  # Let theme transitions and resize-driven canvas redraw settle.
    page.screenshot(path=str(OUT/(label+'.png')))
    report['observations'].append(page.evaluate(r'''(label)=>{
      const active=document.querySelector('.app-view:not(.hidden)');
      const chart=active.querySelector('canvas'); const b=chart?.getBoundingClientRect();
      return {label,width:innerWidth,rootWidth:document.documentElement.scrollWidth,
        renderedText:active.innerText.replace(/\s/g,'').length,
        chart:b?{x:b.x,y:b.y,width:b.width,height:b.height}:null};
    }''', label))

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
    page = browser.new_page(viewport={'width':1366,'height':900})
    page.on('pageerror', lambda error: report['errors'].append(str(error)))
    page.on('dialog', lambda dialog: dialog.accept())
    page.on('response', lambda response: report['mutations'].append({'url':response.url,'status':response.status,
        'body':response.request.post_data}) if response.request.method=='POST' and '/interactive/' in response.url else None)
    try:
        page.goto(URL, wait_until='networkidle')
        page.locator('#continuousNavButton').wait_for(state='visible')
        check('no_duplicate_dom_ids', page.evaluate('''()=>{
            const ids=[...document.querySelectorAll('[id]')].map(n=>n.id);
            return ids.length===new Set(ids).size;
        }'''))
        class Ids(HTMLParser):
            def __init__(self): super().__init__(); self.ids=[]
            def handle_starttag(self,tag,attrs):
                for key,value in attrs:
                    if key=='id': self.ids.append(value)
        original=Ids(); original.feed((ROOT/'src/sat_sim/web/index.html').read_text(encoding='utf-8'))
        missing=page.evaluate('(ids)=>ids.filter(id=>!document.getElementById(id))',original.ids)
        report['original_static_id_count']=len(original.ids)
        check('all_original_static_ids_present',not missing)
        page.locator('#continuousNavButton').click()
        page.wait_for_function('()=>document.body.dataset.activeView==="continuous"')
        capture(page, 'continuous-new-desktop')
        existing=page.locator('#continuousSessionList .continuous-session.active').count()>0
        check('creation_disclosure_matches_selection', page.locator('#compactContinuousCreate').evaluate('(el)=>el.open') != existing)
        if existing: page.locator('#compactContinuousCreate > summary').click()
        check('diagnostics_initially_closed', not page.locator('#compactRuntimeDiagnostics').evaluate('(el)=>el.open'))
        check('telemetry_follows_status', page.evaluate('''()=>document.querySelector('.continuous-main').children[1].contains(document.querySelector('#continuousChart'))'''))
        page.locator('#continuousSessionId').fill(SID)
        with page.expect_response(lambda r: '/interactive/continuous/sessions' in r.url and r.request.method=='POST', timeout=60000) as created:
            page.locator('#continuousCreateForm button[type=submit]').click()
        check('real_native_session_created', created.value.ok)
        report['create_response'] = created.value.json()
        page.wait_for_function('()=>document.querySelector("#continuousState").textContent.includes("准备就绪")', timeout=30000)
        page.wait_for_function('()=>!document.querySelector("#compactContinuousCreate").open')
        check('form_closes_after_selection', True)
        page.locator('#continuousStartBtn').click()
        page.wait_for_function('()=>document.querySelector("#continuousState").textContent.includes("运行中")')
        page.wait_for_function('()=>parseFloat(document.querySelector("#continuousSimTime").textContent)>=2', timeout=20000)
        page.locator('[data-continuous-action=pause]').click()
        page.wait_for_function('()=>document.querySelector("#continuousState").textContent.includes("已暂停")')
        check('pause_works', True)
        snapshot = api('/interactive/continuous/sessions/'+SID+'/status')
        report['paused_snapshot'] = snapshot
        when = snapshot['session']['sim_time_s']+0.5
        page.locator('#continuousExecuteAt').fill(str(when))
        with page.expect_response(lambda r: r.url.endswith('/commands') and r.request.method=='POST') as command:
            page.locator('#continuousCommandSubmit').click()
        report['queued_ack'] = command.value.json()
        check('command_accepted_by_actual_api', command.value.ok and report['queued_ack']['state']!='REJECTED')
        page.locator('[data-continuous-action=step]').click()
        page.locator('#continuousRefreshBtn').click()
        for _ in range(20):
            acks=api('/interactive/sessions/'+SID+'/commands')
            if any(ack['state']=='ACKED' and ack['command_id']==report['queued_ack']['command_id'] for ack in acks['acks']): break
            time.sleep(0.25)
        report['actual_acks'] = acks
        check('actual_command_acked',any(ack['state']=='ACKED' and ack['command_id']==report['queued_ack']['command_id'] and ack['sim_time_s']==when for ack in acks['acks']))
        telemetry = api('/interactive/sessions/'+SID+'/telemetry/whole.continuous?after_sequence=-1')
        (OUT/'actual-telemetry.json').write_text(json.dumps(telemetry,indent=2),encoding='utf-8')
        check('actual_telemetry_frames', bool(telemetry.get('frames')))
        page.locator('#continuousRefreshBtn').click()
        page.wait_for_function('()=>document.querySelector("#continuousOrbit").textContent.includes("m")')
        for width,height in [(1920,1080),(1366,900),(768,1024),(390,844)]:
            page.set_viewport_size({'width':width,'height':height})
            capture(page, 'continuous-selected-'+str(width))
            check('continuous_no_overflow_'+str(width),page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
        check('mobile_stop_directly_visible', page.locator('[data-continuous-action=stop]').is_visible())
        page.set_viewport_size({'width':1366,'height':900})
        page.locator('#compactRuntimeDiagnostics > summary').focus()
        page.keyboard.press('Enter')
        check('diagnostics_keyboard_open', page.locator('#continuousRecoveryAuditBtn').is_visible())
        page.keyboard.press('Enter')
        check('diagnostics_keyboard_close', not page.locator('#continuousRecoveryAuditBtn').is_visible())
        page.locator('#compactContinuousCreate > summary').click()
        page.locator('#continuousRefreshBtn').click()
        page.wait_for_timeout(300)
        check('manual_reopen_survives_refresh', page.locator('#compactContinuousCreate').evaluate('(el)=>el.open'))
        page.locator('#compactContinuousCreate > summary').click()
        page.locator('[data-continuous-action=resume]').click()
        page.wait_for_function('()=>document.querySelector("#continuousState").textContent.includes("运行中")')
        page.locator('[data-continuous-action=stop]').click()
        page.wait_for_function('()=>document.querySelector("#continuousArchive").textContent.includes("已封存")', timeout=45000)
        report['final_status'] = api('/interactive/continuous/sessions/'+SID+'/status')
        check('stop_archives_actual_session',report['final_status']['session']['archive_status']=='SEALED')
        # Supervised runtime intentionally rejects bounded sessions. Use a separate
        # in-process native preview with identical UI bytes, not a physics mock.
        page.goto(BOUNDED_URL,wait_until='networkidle')
        report['bounded_runtime']='Separate in-process native preview; continuous cases use supervised native child.'
        check('bounded_preview_same_application',fp(fingerprints['application_source_manifest'](ROOT/'reports/local_requirements_completion/compact-ui-preview-v5p62fx2'))==report['application'])
        page.locator('#interactiveNavButton').click()
        page.wait_for_function('()=>document.body.dataset.activeView==="interactive"')
        # Create a genuine bounded session with this page's registered telemetry streams.
        if not page.locator('#compactInteractiveCreate').evaluate('(el)=>el.open'):
            page.locator('#compactInteractiveCreate > summary').click()
        page.locator('#interactiveDuration').fill('12')
        with page.expect_response(lambda r: r.url.endswith('/interactive/sessions') and r.request.method=='POST', timeout=60000) as bounded:
            page.locator('#createInteractiveSessionBtn').click()
        check('bounded_real_native_session_created',bounded.value.ok)
        bounded_id=bounded.value.json()['session_id']
        report['bounded_session_id']=bounded_id
        page.wait_for_function('()=>document.querySelector("#interactiveState").textContent==="READY"')
        page.locator('#interactiveStartBtn').click()
        page.wait_for_function('()=>parseFloat(document.querySelector("#interactiveSimTime").textContent)>=2',timeout=20000)
        page.locator('#interactivePauseBtn').click()
        page.wait_for_function('()=>document.querySelector("#interactiveState").textContent==="PAUSED"')
        page.wait_for_selector('#interactiveLatestValues .interactive-value')
        check('bounded_actual_telemetry_rendered',page.locator('#interactiveLatestValues .interactive-value').count()>0)
        check('interactive_controls_have_text',page.locator('#interactiveStopBtn').inner_text()=='停止并归档')
        for width,height in [(1366,900),(390,844)]:
            page.set_viewport_size({'width':width,'height':height})
            capture(page,'interactive-selected-'+str(width))
            check('interactive_no_overflow_'+str(width),page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
        check('mobile_dom_order_matches_reading_order',page.evaluate('document.querySelector(".interactive-layout").firstElementChild.classList.contains("interactive-console")'))
        page.set_viewport_size({'width':1366,'height':900})
        page.locator('#themeSelect').select_option('light')
        capture(page,'interactive-light')
        page.locator('#themeSelect').select_option('dark')
        capture(page,'interactive-dark')
        page.locator('#interactiveStopBtn').click()
        page.wait_for_function('()=>document.querySelector("#interactiveArchiveStatus").textContent.includes("已验证封存")',timeout=45000)
        report['bounded_final']=api('/interactive/sessions/'+bounded_id,base=BOUNDED_URL)
        check('bounded_stop_archives_actual_session',report['bounded_final']['archive_status']=='SEALED')
        report['bounded_telemetry']=api('/interactive/sessions/'+bounded_id+'/telemetry/whole.fast?after_sequence=-1',base=BOUNDED_URL)
        page.goto(URL,wait_until='networkidle')
        page.locator('#themeSelect').select_option('dark')
        page.locator('#continuousNavButton').click()
        page.wait_for_function('()=>document.body.dataset.activeView==="continuous"')
        capture(page,'continuous-dark')
        page.evaluate('document.documentElement.style.setProperty("--ui-scale","1.3")')
        capture(page,'continuous-font130')
        check('font130_no_overflow',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
        page.evaluate('document.documentElement.style.setProperty("--ui-scale","1")')
        check('no_browser_errors',not report['errors'])
        report['status']='PASS_REQUIRES_SCREENSHOT_REVIEW'
    except Exception:
        report['status']='FAIL'
        report['exception']=traceback.format_exc()
        page.screenshot(path=str(OUT/'failure.png'))
    finally:
        if bounded_id:
            try:
                current=api('/interactive/sessions/'+bounded_id,base=BOUNDED_URL)
                if current['state'] not in ('COMPLETED','ABORTED','FAILED','INTERRUPTED'):
                    api('/interactive/sessions/'+bounded_id+'/control',{'action':'stop'},base=BOUNDED_URL)
            except Exception as exc: report['bounded_cleanup_observation']=str(exc)
        try:
            final=api('/interactive/continuous/sessions/'+SID+'/status')
            if final['session']['state'] not in ('COMPLETED','ABORTED','FAILED','INTERRUPTED'):
                api('/interactive/sessions/'+SID+'/control',{'action':'stop'})
        except Exception as exc:
            report['cleanup_observation']=str(exc)
        browser.close()
report['root_application_after']=fp(fingerprints['application_source_manifest']())
report['checks']['root_unchanged']=report['root_application_before']==report['root_application_after']
report['ended_utc']=datetime.now(timezone.utc).isoformat()
(OUT/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps({'status':report['status'],'checks':report['checks'],'exception':report.get('exception')}),flush=True)
raise SystemExit(0 if report['status'].startswith('PASS') else 1)
