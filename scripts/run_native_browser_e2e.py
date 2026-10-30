#!/usr/bin/env python3
"""用途：浏览器经真实 HTTP 服务执行原生整星离线与实时仿真，无物理协议桩。
参数：--output-dir 保存独立服务、Run Bundle、原生会话、截图与检查报告。
输出：实际页面操作、原生遥测和控制证据；不替代长期性能或飞行标定。
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from run_browser_e2e import _browser_executable  # noqa: E402


def run(output):
    from playwright.sync_api import sync_playwright
    output = output.resolve()
    output.mkdir(parents=True,exist_ok=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    env = {**os.environ,'SAT_SIM_INTERACTIVE_ENABLED':'1','SAT_SIM_SUPERVISED_NATIVE_RUNTIME':'1',
           'SAT_SIM_AUTH_MODE':'disabled','SAT_SIM_EMBEDDED_WORKER':'1',
           'SAT_SIM_RUNS_ROOT':str(output/'runs'),'SAT_SIM_API_ARTIFACTS_ROOT':str(output/'api')}
    for key in ('SAT_SIM_QUEUE_DATABASE','SAT_SIM_TASK_CENTER_DATABASE','SAT_SIM_EXPERIMENT_DATABASE',
                'SAT_SIM_AUDIT_LOG','SAT_SIM_WORKBENCH_LOG','SAT_SIM_MODEL_PROVIDERS_FILE'):
        env.pop(key,None)
    report = {'schema_version':'sat-native-browser-e2e.v1','status':'RUNNING',
              'transport':'real_loopback_http','physics':'real_Basilisk_no_protocol_double',
              'checks':{},'page_errors':[],'failed_responses':[]}
    checks = report['checks']
    log = (output/'server.log').open('w',encoding='utf-8')
    process = subprocess.Popen([sys.executable,'-m','uvicorn','sat_sim.api:create_app','--factory',
        '--host','127.0.0.1','--port',str(port),'--no-access-log'],cwd=ROOT,env=env,
        stdout=log,stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    browser = None
    def get(path):
        with urllib.request.urlopen(base+path,timeout=20) as response:
            return json.load(response)

    def inspect_responsive_layout(page, phase):
        for width,height in ((1920,1080),(1366,768),(768,1024),(390,844)):
            page.set_viewport_size({'width':width,'height':height})
            page.evaluate('scrollTo(0,0)')
            page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
            geometry = page.evaluate('''() => ({
                viewport_width:innerWidth,
                root_width:document.documentElement.scrollWidth,
                body_width:document.body.scrollWidth,
                horizontal_scroll:scrollX,
            })''')
            report.setdefault('responsive_layouts',[]).append({'phase':phase,'width':width,**geometry})
            checks[f'responsive_{phase}_{width}'] = (
                geometry['root_width']<=width+1 and geometry['body_width']<=width+1)
            if phase=='offline' and width==390:
                action = page.locator('#runBtn').bounding_box()
                checks['mobile_primary_action_above_fold'] = bool(action and action['y']+action['height']<=height)
                tools = page.locator('#toggleMobileToolsBtn')
                checks['mobile_optional_settings_initially_collapsed'] = tools.get_attribute('aria-expanded')=='false' and not page.locator('#apiTokenInput').is_visible()
                tools.click()
                checks['mobile_settings_remain_accessible'] = all(page.locator('#'+key).is_visible() for key in ('fontScaleSelect','themeSelect','apiTokenInput'))
                page.screenshot(path=str(output/'mobile-settings-390.png'),full_page=False)
                tools.click()
                catalog = page.locator('#toggleMobileCatalogBtn')
                catalog.click()
                checks['mobile_catalog_remains_accessible'] = page.locator('#capabilitySearch').is_visible()
                catalog.click()
                page.evaluate('scrollTo(0,0)')
            if phase=='live-paused' and width==390:
                status = page.locator('#continuousState').bounding_box()
                creation = page.locator('#continuousCreateForm').bounding_box()
                checks['mobile_selected_session_state_above_fold'] = bool(status and status['y']+status['height']<=height)
                checks['mobile_selected_session_precedes_creation'] = bool(status and creation and status['y']<creation['y'])
                checks['mobile_continuous_keyboard_order_matches_layout'] = page.locator('#continuousView .continuous-grid').evaluate("node => node.firstElementChild.classList.contains('continuous-main')")
                toolbar = page.locator('#continuousSessionToolbar').bounding_box()
                checks['mobile_live_controls_visible_without_scrolling'] = bool(toolbar and toolbar['y']>=0 and toolbar['y']+toolbar['height']<=height)
                checks['mobile_controls_have_one_live_owner'] = page.locator('#continuousSessionToolbar [data-continuous-action]').count()==5
                page.locator('#toggleMobileToolsBtn').click()
                for scale in ('90','100','115','130'):
                    page.locator('#fontScaleSelect').select_option(scale)
                    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
                    bounds = page.locator('#continuousSessionToolbar').bounding_box()
                    root_width = page.evaluate('document.documentElement.scrollWidth')
                    checks[f'mobile_font_{scale}_controls_and_width'] = bool(
                        root_width<=width+1 and bounds and bounds['x']>=-1
                        and bounds['x']+bounds['width']<=width+1
                        and bounds['y']>=0 and bounds['y']+bounds['height']<=height+1)
                    report.setdefault('mobile_font_layouts',[]).append({'scale':scale,'root_width':root_width,'control_bounds':bounds})
                page.screenshot(path=str(output/'mobile-font-130.png'),full_page=False)
                page.locator('#fontScaleSelect').select_option('100')
                page.locator('#toggleMobileToolsBtn').click()
                page.locator('[data-continuous-action="resume"]').focus()
            page.screenshot(path=str(output/f'{phase}-{width}.png'),full_page=False)
        page.set_viewport_size({'width':1600,'height':1000})
        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        if phase=='live-paused':
            checks['desktop_restores_original_control_slot'] = page.locator('#continuousSessionToolbar').evaluate("node => node.parentElement.id==='continuousSessionActionSlot'")
            checks['viewport_change_preserves_control_focus'] = page.locator('[data-continuous-action="resume"]').evaluate('node => document.activeElement===node')
    try:
        deadline = time.monotonic()+60
        while True:
            if process.poll() is not None:
                raise RuntimeError('NATIVE_BROWSER_API_EXITED_DURING_STARTUP')
            try:
                health = get('/health')
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('NATIVE_BROWSER_API_STARTUP_TIMEOUT')
                time.sleep(.2)
        report['health'] = health
        with sync_playwright() as pw:
            executable = _browser_executable(pw)
            if executable is None:
                raise FileNotFoundError('SAT_SIM_BROWSER_EXECUTABLE must name installed Chromium')
            browser = pw.chromium.launch(headless=True,executable_path=executable)
            page = browser.new_page(viewport={'width':1600,'height':1000},locale='zh-CN')
            page.on('pageerror',lambda error:report['page_errors'].append(str(error)))
            page.on('response',lambda response:report['failed_responses'].append(
                {'url':response.url,'status':response.status}) if response.status>=400 else None)
            page.on('dialog',lambda dialog:dialog.accept())
            page.goto(base,wait_until='networkidle')
            page.wait_for_selector('.capability-card.active')
            page.locator('#scenarioTemplateSelect').select_option('generic_leo_small_sat_engineering')
            with page.expect_response(lambda response:'/scenario-templates/generic_leo_small_sat_engineering/instantiate' in response.url,timeout=60000) as template_pending:
                page.locator('#applyScenarioTemplateBtn').click()
            expected_engineering_scope = template_pending.value.json()['task_spec']['metadata']['engineering_scope']
            page.wait_for_function("() => document.querySelector('#agentChangePanel') && !document.querySelector('#agentChangePanel').classList.contains('hidden')")
            page.locator('#showAdvancedFields').check()
            for path,value in (('simulation.duration_s','20'),('simulation.sample_s','1'),('simulation.step_s','0.2')):
                field = page.locator(f'[data-path="{path}"]')
                field.fill(value)
                field.dispatch_event('change')
            page.locator('[data-path="parameters.values.environment_profile"]').select_option('spice_wmm')
            with page.expect_response(lambda response:response.url==base+'/runs' and response.request.method=='POST',timeout=60000) as pending:
                page.locator('#runBtn').click()
            submission = pending.value.json()
            assert pending.value.ok, submission
            run_id = submission['prepared_run']['run_id']
            report['offline_run_id'] = run_id
            page.wait_for_selector("#singleRunGuide[data-stage='complete'], #singleRunGuide[data-stage='failed']",timeout=120000)
            checks['offline_ui_complete'] = page.locator('#singleRunGuide').get_attribute('data-stage')=='complete'
            detail = get('/runs/'+run_id)
            summary = detail['run']['summary']
            checks['offline_native_truth'] = summary.get('runtime_truth_status')=='instantiated_connected_recorded_unified'
            checks['offline_spice_environment'] = summary.get('orbital_environment',{}).get('profile')=='spice_wmm'
            report['offline_environment'] = summary.get('orbital_environment')
            (output/'offline-run.json').write_text(json.dumps(detail,ensure_ascii=False,indent=2),encoding='utf-8')
            telemetry = get('/runs/'+run_id+'/telemetry?limit=2000')
            report['offline_trace_rows'] = len(telemetry.get('rows',[]))
            checks['offline_real_trace'] = report['offline_trace_rows']>=20
            checks['finite_earth_boundary_executed'] = summary.get('thermal_environment',{}).get('model')=='finite_spherical_earth_lambertian_v1'
            checks['native_earth_heat_recorded'] = all(
                float(row['thermal.payload_earth_ir_heat_w'])>0
                and float(row['thermal.payload_albedo_heat_w'])>=0
                for row in telemetry.get('rows',[])) and bool(telemetry.get('rows'))
            checks['offline_result_display'] = page.locator('#activeRunCard').inner_text().strip() != ''
            checks['offline_runtime_state_readable'] = page.locator('#metricGrid').get_by_text('已连接并记录', exact=True).count()==1
            metadata = get('/runs/'+run_id+'/metrics')['metric_metadata']
            checks['native_overview_units'] = all(metadata[key]['unit']==unit for key,unit in (
                ('final_storage_bits','bit'),('final_pointing_error_deg','deg'),('final_battery_soc','ratio')))
            storage_metric = page.locator('#metricGrid .metric-card').filter(has_text='最终存储数据量')
            checks['storage_is_data_not_energy'] = storage_metric.count()==1 and 'bit' in storage_metric.inner_text()
            checks['internal_versions_hidden_from_overview'] = summary['status_semantics_version'] not in page.locator('#metricGrid').inner_text()
            physical = detail['run']['validation_outcome']['physical_validation']
            coverage = page.locator('#validationCard .validation-coverage')
            checks['physical_coverage_disclosed'] = coverage.is_visible() and '物理检查覆盖：部分' in coverage.inner_text()
            checks['missing_conservation_not_passed'] = (
                physical['check_results']['data_conservation']['status']=='NOT_EVALUATED'
                and '未评估' in coverage.inner_text() and '数据守恒' in coverage.inner_text())
            context = page.get_by_role('region',name='工程假设与适用范围')
            checks['engineering_context_visible'] = context.is_visible() and '未标定' in context.inner_text()
            expected_paths = {f'parameters.values.{key}' for key in detail['run']['task_spec']['parameters']['values']}
            recorded_paths = {item['path'] for item in detail['run']['task_spec']['provenance']['assumptions']}
            checks['engineering_inputs_traced'] = expected_paths <= recorded_paths
            checks['engineering_scope_preserved'] = detail['run']['task_spec'].get('metadata',{}).get('engineering_scope')==expected_engineering_scope
            context.locator('.run-engineering-disclosure > summary').click()
            checks['thermal_boundary_disclosed_in_ui'] = expected_engineering_scope['thermal_boundary'] in context.inner_text()
            context.locator('.run-input-assumptions > summary').click()
            checks['engineering_parameter_sources_displayed'] = context.locator('.run-assumption-list li').count() >= len(expected_paths)
            page.screenshot(path=str(output/'engineering-input-sources.png'),full_page=False)
            context.locator('.run-engineering-disclosure > summary').click()
            page.screenshot(path=str(output/'offline.png'),full_page=False)
            inspect_responsive_layout(page,'offline')

            page.locator('#continuousNavButton').click()
            page.locator('#continuousSessionId').fill('native-browser-live')
            page.locator('#continuousQuantum').select_option('0.2')
            page.locator('#continuousHorizon').fill('20')
            page.locator('#continuousCreateForm details.continuous-native-config > summary').click()
            page.locator('[data-native-key="environment_profile"]').select_option('spice_wmm')
            page.locator('[data-native-key="epoch_utc"]').fill('2026-01-01T00:00:00Z')
            page.set_viewport_size({'width':390,'height':844})
            page.locator('#continuousCreateForm button[type=submit]').click()
            page.wait_for_function("() => document.querySelector('#continuousState').dataset.state==='READY'",timeout=60000)
            page.wait_for_function("document.activeElement.id==='continuousStatusHeading'",timeout=10000)
            checks['mobile_ready_state_readable'] = (
                page.locator('#continuousState').inner_text()=='准备就绪'
                and page.locator('#continuousState').get_attribute('title')=='READY')
            checks['mobile_creation_focuses_status_without_autoplay'] = (
                page.locator('#continuousState').get_attribute('data-state')=='READY'
                and float(page.locator('#continuousSimTime').inner_text().split()[0])==0.)
            page.set_viewport_size({'width':1600,'height':1000})
            page.locator('[data-continuous-action="start"]').click()
            page.wait_for_function("() => parseFloat(document.querySelector('#continuousSimTime').textContent)>=2",timeout=20000)
            page.locator('[data-continuous-action="pause"]').click()
            page.wait_for_function("() => document.querySelector('#continuousState').dataset.state==='PAUSED'",timeout=20000)
            paused = float(page.locator('#continuousSimTime').inner_text().split()[0])
            page.locator('[data-continuous-action="step"]').click()
            page.wait_for_function('(before)=>parseFloat(document.querySelector("#continuousSimTime").textContent)>before',arg=paused,timeout=20000)
            checks['live_pause_and_step'] = page.locator('#continuousState').get_attribute('data-state')=='PAUSED'
            checks['live_pause_state_readable'] = (
                page.locator('#continuousState').inner_text()=='已暂停'
                and page.locator('#continuousState').get_attribute('title')=='PAUSED')
            checks['live_chart_visible'] = page.locator('#continuousChart').is_visible()
            checks['platform_fence_label'] = (os.name!='nt' or 'Windows' in page.locator('#continuousParentFence').inner_text())
            page.screenshot(path=str(output/'live-paused.png'),full_page=False)
            inspect_responsive_layout(page,'live-paused')
            page.locator('[data-continuous-action="stop"]').click()
            page.wait_for_function("() => ['COMPLETED','STOPPED'].includes(document.querySelector('#continuousState').dataset.state)",timeout=30000)
            report['live_terminal_state'] = page.locator('#continuousState').get_attribute('data-state')
            report['live_terminal_label'] = page.locator('#continuousState').inner_text()
            page.wait_for_function("() => document.querySelector('#continuousArchive').dataset.state==='SEALED'",timeout=30000)
            checks['live_archive_state_readable'] = (
                page.locator('#continuousArchive').inner_text()=='已封存'
                and page.locator('#continuousArchive').get_attribute('title')=='SEALED')
            deadline = time.monotonic()+20
            while True:
                report['live_detail'] = get('/interactive/sessions/native-browser-live')
                if not report['live_detail']['worker_alive']:
                    break
                if time.monotonic()>=deadline:
                    raise TimeoutError('TERMINAL_NATIVE_WORKER_DID_NOT_EXIT')
                page.wait_for_timeout(100)
            checks['live_archive_sealed'] = (report['live_detail']['archive_status']=='SEALED' and
                                            report['live_detail']['archive_error'] is None)
            result = get('/interactive/sessions/native-browser-live/runtime-result')
            report['live_runtime_result'] = result
            # The general session snapshot contains lifecycle fields only.
            # Scheduling is part of the continuous runtime status contract.
            continuous_status = get('/interactive/continuous/sessions/native-browser-live/status')
            report['live_continuous_status'] = continuous_status
            scheduling = continuous_status.get('native_cpu_scheduling')
            archived_scheduling = result['result'].get('metadata',{}).get('native_cpu_scheduling')
            checks['native_scheduling_policy_recorded'] = (
                isinstance(scheduling,dict)
                and scheduling.get('schema_version')=='sat-native-cpu-scheduling.v1'
                and isinstance(scheduling.get('pid'),int)
                and scheduling['pid']==continuous_status.get('native_child_pid')
                and scheduling.get('mode') in {'SYSTEM_DEFAULT','PERFORMANCE_CORES'}
                and isinstance(scheduling.get('applied'),bool)
                and scheduling==archived_scheduling)
            checks['live_spice_environment'] = result['result']['summary']['orbital_environment']['profile']=='spice_wmm'
            history = get('/interactive/sessions/native-browser-live/telemetry-history/whole.continuous?start_s=0&end_s=20&fields=orbit.radius_m,adcs.pointing_error_deg')
            report['live_history_frame_count'] = history['frame_count']
            checks['live_original_history'] = history['exact_original_samples'] and history['frame_count']>=5
            checks['live_terminal'] = report['live_terminal_state']=='COMPLETED'
            checks['page_clean'] = not report['page_errors']
            checks['http_clean'] = not report['failed_responses']
            browser.close()
            browser = None
        report['status'] = 'PASS' if all(checks.values()) else 'FAIL'
    except Exception as exc:
        report.update(status='FAIL',error={'type':type(exc).__name__,'message':str(exc)})
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        # Only processes descended from this script's own server are cleaned up.
        import psutil
        children = psutil.Process(process.pid).children(recursive=True) if process.poll() is None else []
        for child in children:
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=20)
        log.close()
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'reports/native_browser_e2e')
    result = run(parser.parse_args().output_dir)
    print(json.dumps({key:result[key] for key in ('status','checks','error') if key in result},ensure_ascii=False))
    raise SystemExit(0 if result['status']=='PASS' else 1)
