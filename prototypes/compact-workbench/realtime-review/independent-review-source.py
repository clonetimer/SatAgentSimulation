"""Bind final browser observations, original failure reports and UI bytes."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import runpy
import shutil
import importlib.util
import psutil

ROOT=Path(__file__).resolve().parents[2]
LOCAL=ROOT/'reports/local_requirements_completion'
PROTO=ROOT/'prototypes/compact-workbench'
OUT=PROTO/'realtime-review'
read=lambda p:json.loads(p.read_text(encoding='utf-8'))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
checks={}
def check(name,value):
    checks[name]=bool(value)
    assert value,name

review=read(LOCAL/'compact-realtime-ui-review-v10/report.json')
comparison=read(LOCAL/'compact-realtime-layout-comparison/report.json')
check('31_browser_checks',len(review['checks'])==31 and all(review['checks'].values()))
check('474_static_ids',review['original_static_id_count']==474)
check('3_same_session_comparison_checks',len(comparison['checks'])==3 and all(comparison['checks'].values()))
check('actual_continuous_archive',review['final_status']['session']['archive_status']=='SEALED')
check('actual_bounded_archive',review['bounded_final']['archive_status']=='SEALED')
check('actual_bounded_frames',bool(review['bounded_telemetry']['frames']))
bounded_requests=[json.loads(row['body']) for row in review['mutations'] if row['url'].endswith('/interactive/sessions')]
simulation=bounded_requests[-1]['task_spec']['simulation']
check('default_sample_alignment',simulation['sample_s']==.5 and simulation['solver']['step_s']==.1)
copies={
    'browser-report.json':LOCAL/'compact-realtime-ui-review-v10/report.json',
    'layout-comparison.json':LOCAL/'compact-realtime-layout-comparison/report.json',
    'review-source.py':LOCAL/'review_compact_realtime_ui.py',
    'comparison-source.py':LOCAL/'compare_compact_realtime_layout.py',
    'independent-review-source.py':Path(__file__),
    'actual-telemetry.json':LOCAL/'compact-realtime-ui-review-v10/actual-telemetry.json',
    'preview-retirement.json':LOCAL/'compact-realtime-preview-retirement.json',
}
for name,source in copies.items(): shutil.copyfile(source,OUT/name)
for folder in ['compact-realtime-ui-review-v10','compact-realtime-layout-comparison']:
    for source in (LOCAL/folder).glob('*.png'): shutil.copyfile(source,OUT/source.name)
history=OUT/'history'; history.mkdir(exist_ok=True)
for n in range(1,10):
    source=LOCAL/f'compact-realtime-ui-review-v{n}'/'report.json'
    if source.is_file(): shutil.copyfile(source,history/f'v{n}-report.json')
for n in [6,7]:
    source=LOCAL/f'compact-realtime-ui-review-v{n}'/'bounded-create-error.json'
    if source.is_file():shutil.copyfile(source,history/f'v{n}-bounded-create-error.json')
m=runpy.run_path(str(ROOT/'scripts/run_native_endurance.py'));fp=m['runtime_source_fingerprint']
candidate=LOCAL/'compact-ui-preview-k0l0_yof'
check('tested_application_unchanged',fp(m['application_source_manifest'](candidate))==review['application'])
check('production_app_unchanged',fp(m['application_source_manifest']())=='4cce6d46062a27bc310cc168c1d9587a458c56b4692bd60cf2c60548c2698f82')
check('production_core_unchanged',fp(m['runtime_source_manifest']())=='59d0f3613c8f21e267d8c2fd7886bb3af6a4be71a32f521d3e8b4646133a9cbe')
check('main_environment_websocket_not_modified',importlib.util.find_spec('websockets') is None)
manifest=read(PROTO/'manifest.json')
for row in manifest['files']:
    check('source_'+row['path'],sha(PROTO/row['path'])==row['sha256']==sha(candidate/row['target']))
manifest['candidate_application']=review['application']
manifest['realtime_scope']='31 browser checks plus3 same-session layout checks; isolated candidate, not full-app or endurance qualification.'
manifest['realtime_browser_report']='realtime-review/browser-report.json'
manifest['default_cadence_fix']='Bounded default sample0.5s now uses native step0.1s; previous0.2s was rejected as misaligned. No sampling period replacement.'
(PROTO/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
screens=sorted(OUT.glob('*.png'))
check('13_reviewed_final_images',len(screens)==13)
image_review={'status':'ACTUALLY_VIEWED','scope':'Root assistant viewed all11 final browser screenshots and2 same-session comparison screenshots with the image tool; no automated claim of universal visual perfection.',
              'images':[{'path':p.name,'sha256':sha(p)} for p in screens]}
(OUT/'visual-review.json').write_text(json.dumps(image_review,indent=2),encoding='utf-8')
preview_states=[]
for name in ['compact-ui-preview-k0l0_yof','compact-ui-preview-v5p62fx2']:
    p=LOCAL/name/'preview-state.json'; state=read(p)
    check('preview_'+name,fp(m['application_source_manifest'](LOCAL/name))==review['application'])
    state['initial_preview_application']=state.get('initial_preview_application',state['preview_application'])
    state['preview_application']=review['application']
    state['latest_ui_observed_utc']=datetime.now(timezone.utc).isoformat()
    p.write_text(json.dumps(state,indent=2),encoding='utf-8')
    shutil.copyfile(p,OUT/('preview-'+state['native_runtime']+'.json'))
    preview_states.append({'url':state['url'],'native_runtime':state['native_runtime']})
original=psutil.Process(80520)
check('original_native_identity',abs(original.create_time()-1793337824.415924)<.01 and original.is_running())
result={'status':'SCOPED_CHECKS_PASS_NOT_PRODUCTION_QUALIFICATION','checks':checks,
        'application':review['application'],'previews':preview_states,'observed_utc':datetime.now(timezone.utc).isoformat(),
        'scope':'Independent re-read of real browser/API artifacts and source bytes, no re-execution or changed thresholds. Does not replace final UI screenshots, full regression, runtime capability UX, AI integration or original endurance disposition.'}
(OUT/'independent-review.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
files=[p for p in OUT.rglob('*') if p.is_file() and p.name!='manifest.json']
(OUT/'manifest.json').write_text(json.dumps({'scope':'Byte binding, not correctness proof. The manifest excludes itself.',
    'files':[{'path':str(p.relative_to(OUT)).replace('\\','/'),'sha256':sha(p)} for p in sorted(files)]},indent=2),encoding='utf-8')
print(json.dumps({'status':result['status'],'checks':len(checks),'application':review['application'],'files':len(files),'images':len(screens)}))
