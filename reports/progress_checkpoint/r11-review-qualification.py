"""Read-only qualification review. No simulation, model call, service or image inference.

Only a fresh --output report is written. --stage all requires separately supplied
actual-image-review attestations; this program never creates those attestations.
"""
from __future__ import annotations

import argparse
import ast
import csv
import fnmatch
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import runpy
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT/'reports/local_requirements_completion'
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT/'src'))
RUNTIME_CHECKS = {
    'runtime_generated_spec_bound', 'runtime_execution_succeeded', 'runtime_validation_passed',
    'runtime_physical_checks_passed', 'runtime_trace_present', 'runtime_numeric_outputs_finite',
    'runtime_real_adapter', 'runtime_bundle_integrity', 'runtime_seal_bound',
}
EARTH_FIELDS = {'orbit.radius_m', 'eps.battery_soc', 'thermal.payload_temp_k',
                'thermal.payload_earth_ir_heat_w', 'thermal.payload_albedo_heat_w'}
LINK_FIELDS = {'orbit.radius_m', 'eps.battery_soc', 'thermal.payload_temp_k',
               'thermal.solar_panel_temp_k', 'thermal.structure_temp_k'}
MODEL_DEFAULT = 'Qwen2.5-Coder-1.5B-SatAgent-v5-guided-graph-v8'


class ReviewError(RuntimeError):
    pass


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for part in iter(lambda: handle.read(1024*1024), b''):
            h.update(part)
    return h.hexdigest()


def resolve(value, owner=None):
    path = Path(value)
    if not path.is_absolute():
        path = (ROOT if path.parts and path.parts[0]=='reports' else owner or ROOT)/path
    return path.resolve()


class Reviewer:
    def __init__(self, args):
        self.args = args
        self.sources, self.checks, self.details, self.bundles = {}, {}, {}, {}

    def require(self, name, condition, detail=None):
        self.checks[name] = bool(condition)
        if not condition:
            raise ReviewError(name + (': '+str(detail) if detail is not None else ''))

    def remember(self, path):
        path = resolve(path)
        self.require('file_exists:'+str(path), path.is_file())
        key = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)
        self.sources[key] = digest(path)
        return path

    def read(self, path):
        path = self.remember(path)
        return json.loads(path.read_text(encoding='utf-8'))

    def all_checks(self, label, report, required=()):
        values = report.get('checks')
        self.require(label+':nonempty_checks', isinstance(values, dict) and bool(values))
        self.require(label+':required_checks_present', set(required)<=set(values))
        self.require(label+':every_declared_check_true', all(v is True for v in values.values()))
        self.require(label+':no_error', not report.get('error'))

    def report(self, path, required=()):
        value = self.read(path)
        self.require(str(path)+':terminal_PASS', value.get('status')=='PASS')
        self.all_checks(str(path), value, required)
        return value

    def driver(self, row, hash_key):
        command = row.get('command') or []
        candidates = [resolve(item) for item in command if isinstance(item,str) and item.endswith('.py')]
        self.require('recorded_driver_path_present', bool(candidates))
        path = self.remember(candidates[0])
        self.require('recorded_driver_hash:'+path.name, digest(path)==row.get(hash_key))
        return path

    def declared_checks(self, path):
        tree = ast.parse(self.remember(path).read_text(encoding='utf-8'))
        keys = set()
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='check' and node.args:
                if isinstance(node.args[0],ast.Constant) and isinstance(node.args[0].value,str):
                    keys.add(node.args[0].value)
            if isinstance(node,ast.Subscript) and isinstance(node.value,ast.Name) and node.value.id=='checks' and isinstance(node.ctx,ast.Store):
                if isinstance(node.slice,ast.Constant) and isinstance(node.slice.value,str):
                    keys.add(node.slice.value)
        return keys

    def browser_required_checks(self, path):
        # These are the existing r11 driver's loop-generated gates, not new
        # visual criteria. Static literal AST extraction alone misses f-strings.
        keys = self.declared_checks(path)
        for width in (1920,1366,768,390):
            for phase in ('model-settings','generated-python','actual-model-result'):
                keys.update({f'{phase}_layout_{width}',f'{phase}_editor_content_fits_{width}'})
            keys.add(f'model_dialog_fits_{width}')
            for phase in ('generated-python','actual-model-result'):
                keys.add(f'{phase}_target_actually_in_viewport_{width}')
            keys.update({f'generated-python_visible_editor_matches_export_{width}',
                f'actual-model-result_tab_labels_not_vertically_clipped_{width}',
                f'actual_model_curves_whole_canvas_visible_{width}',
                f'actual_model_curves_have_drawn_series_{width}'})
        return keys

    def chart_required_checks(self, path):
        keys = self.declared_checks(path)
        for phase in ('offline','live-paused'):
            keys.update(f'responsive_{phase}_{width}' for width in (1920,1366,768,390))
        keys.update(f'mobile_font_{scale}_controls_and_width' for scale in ('90','100','115','130'))
        phases = [f'offline-curves-{width}' for width in (1920,1366,768,390)]
        phases += ['live-curve-1600','live-curve-390','verified-history-1600','verified-history-390']
        for phase in phases:
            keys.update({phase+'_real_colored_curve_spans_plot',phase+'_whole_canvas_in_viewport'})
        return keys

    def qualification(self):
        args = self.args
        state = self.read(args.qualification_state)
        self.require('qualification_terminal_success', state.get('status')=='CHECKS_PASS_INDEPENDENT_AND_VISUAL_REVIEW_PENDING')
        self.require('qualification_source_bound', state.get('application')==state.get('application_after')==args.application
                     and state.get('core')==state.get('core_after')==args.core)
        self.require('qualification_unchanged_flags', state.get('tests_unchanged') is True and state.get('driver_unchanged') is True)
        self.require('qualification_no_error', not state.get('error'))
        self.driver(state, 'driver_sha256')
        children = state.get('children') or []
        self.require('qualification_all_three_children_finished', {r.get('name') for r in children}=={'full-suite','ai-followup','charts'}
                     and all(r.get('returncode')==0 and r.get('finished_at_utc') for r in children))
        expected = self.read(args.qualification_state.parent/'test-source-before.json')
        current = {p.relative_to(ROOT).as_posix():digest(p) for p in (ROOT/'tests').rglob('*.py')}
        self.require('exact_current_test_source_set_and_bytes', current==expected)
        self.expected_tests = expected
        observation = self.read(BASE/(args.prefix+'-source-observation.json'))
        self.require('source_observation_same_frozen_candidate',observation.get('status')=='FROZEN_CANDIDATE_READY'
                     and observation.get('application')==args.application and observation.get('core')==args.core)
        xml = self.remember(args.qualification_state.parent/'whole-suite.xml')
        cases = list(ET.parse(xml).getroot().iter('testcase'))
        failed = [case.attrib for case in cases if any(n.tag in {'failure','error'} for n in case)]
        skipped = [case.attrib for case in cases if any(n.tag=='skipped' for n in case)]
        suite = state.get('full_suite') or {}
        self.require('complete_junit_no_failure_or_error', not failed and len(cases)==args.expected_testcases and len(skipped)==args.expected_skips)
        self.require('junit_matches_wrapper_counts_and_hash', suite.get('junit_sha256')==digest(xml)
                     and suite.get('cases')==len(cases) and suite.get('passed')==len(cases)-len(skipped)
                     and suite.get('failures')==failed and suite.get('skipped')==skipped)
        self.details['full_suite'] = {'cases':len(cases),'passed':len(cases)-len(skipped),'skipped':len(skipped)}
        self.require('qualification_thermal_state_hash', state.get('thermal_state_sha256')==digest(self.remember(args.thermal_state)))
        self.require('qualification_ai_state_hash', state.get('ai_state_sha256')==digest(self.remember(args.ai_state)))
        self.require('qualification_chart_report_hash', state.get('chart_report_sha256')==digest(self.remember(args.chart_report)))
        return state

    def wrapper_states(self):
        a = self.args
        thermal = self.read(a.thermal_state)
        self.require('original_thermal_wrapper_terminal_success', thermal.get('status')=='PASS_REQUIRES_INDEPENDENT_REVIEW')
        self.require('original_thermal_wrapper_unchanged', all(thermal.get(k) is True for k in ('source_unchanged','driver_unchanged','wrapper_unchanged')))
        self.require('original_thermal_version_bound', thermal.get('application_before')==thermal.get('application_after')==a.application
                     and thermal.get('core_before')==thermal.get('core_after')==a.core and thermal.get('returncode')==0)
        thermal_driver = self.driver(thermal,'driver_sha256')
        prefix = a.prefix.replace('-','_')
        thermal_wrapper = self.remember(BASE/f'run_{prefix}_real_thermal.py')
        self.require('original_thermal_wrapper_current_bytes', digest(thermal_wrapper)==thermal.get('wrapper_sha256'))
        ai = self.read(a.ai_state)
        self.require('AI_wrapper_terminal_success', ai.get('status')=='CHECKS_PASS_REQUIRES_INDEPENDENT_AND_VISUAL_REVIEW')
        self.require('AI_wrapper_source_bound', ai.get('application')==ai.get('application_after')==a.application
                     and ai.get('core')==ai.get('core_after')==a.core and ai.get('wrapper_unchanged') is True)
        self.require('AI_wrapper_prior_thermal_state_bound', ai.get('prior_thermal_state_sha256')==digest(a.thermal_state))
        self.driver(ai,'wrapper_sha256')
        steps = ai.get('steps') or []
        self.require('all_four_AI_steps_complete', {row.get('name') for row in steps}=={'NEW_THERMAL_PARAMETER','BATTERY','CREATE_REFINE','MODEL_BROWSER'}
                     and all(row.get('returncode')==0 and row.get('finished_at_utc') and row.get('driver_unchanged') is True for row in steps))
        drivers = {}
        for row in steps:
            drivers[row['name']] = self.driver(row,'driver_sha256')
            path = self.remember(row['report'])
            self.require(row['name']+':report_hash_bound', row.get('report_sha256')==digest(path))
        return thermal_driver, drivers

    def csv(self, path, duration, sample, fields):
        path = self.remember(path)
        with path.open(encoding='utf-8',newline='') as handle:
            rows = list(csv.DictReader(handle))
        self.require(str(path)+':exact_requested_sample_count', len(rows)==round(duration/sample)+1)
        self.require(str(path)+':requested_fields_present', bool(rows) and set(fields)|{'time_s'}<=set(rows[0]))
        self.require(str(path)+':exact_requested_sample_grid', all(math.isclose(float(r['time_s']),i*sample,abs_tol=1e-9,rel_tol=0) for i,r in enumerate(rows)))
        self.require(str(path)+':requested_values_finite', all(math.isfinite(float(r[k])) for r in rows for k in fields))
        for row in rows:
            for value in row.values():
                try:
                    numeric = float(value)
                except (TypeError,ValueError):
                    continue
                self.require(str(path)+':all_numeric_cells_finite', math.isfinite(numeric))
        return rows

    def bundle(self, root, owner):
        from sat_sim.run_bundle import verify_run_bundle
        root = resolve(root,owner)
        self.require('bundle_belongs_to_current_stage:'+str(root), root.is_relative_to(owner.resolve()))
        self.require('bundle_verified:'+str(root), verify_run_bundle(root).get('ok') is True)
        seal = self.read(root/'SEALED.json')
        manifest = self.read(root/'bundle_manifest.json')
        self.require('seal_manifest_hash:'+str(root), seal.get('bundle_manifest_sha256')==digest(root/'bundle_manifest.json')
                     and seal.get('run_id')==manifest.get('run_id'))
        self.bundles[root.relative_to(ROOT).as_posix()] = {'seal_sha256':digest(root/'SEALED.json'),
            'manifest_sha256':digest(root/'bundle_manifest.json')}
        return root

    def native_proof(self, folder, duration, sample, fields):
        from sat_sim.task_models import canonicalize_task_spec, to_runtime_task_spec
        proof = self.read(folder/'runtime-verification.json')
        self.require(str(folder)+':production_execution_proof_pass', proof.get('passed') is True)
        self.all_checks(str(folder)+':runtime', proof, RUNTIME_CHECKS)
        root = self.bundle(proof['bundle_root'],folder)
        task = self.read(folder/'task_spec.json')
        bound = self.read(root/'input/task_spec.json')
        self.require(str(folder)+':input_spec_really_bound', bound==canonicalize_task_spec(task))
        port = self.read(root/'runtime/execution_port.json')
        self.require(str(folder)+':real_adapter_port', port.get('status')=='SUCCEEDED' and port.get('test_integration_seam') is not True)
        validation = self.read(root/'validation/validation_outcome.json')
        self.require(str(folder)+':native_validation_pass', validation.get('result')=='PASS' and validation.get('execution_status')=='SUCCEEDED')
        physical = validation.get('physical_validation') or {}
        executed = physical.get('checks_run') or []
        self.require(str(folder)+':physical_evidence_observed', bool(executed) and physical.get('fail_count')==0
                     and all((physical.get('check_results') or {}).get(k,{}).get('status')=='PASS' for k in executed))
        summary = self.read(root/'results/summary.json')
        self.require(str(folder)+':actual_native_runtime', summary.get('engine')=='basilisk_unified_native_runtime'
                     and summary.get('runtime_truth_status')=='instantiated_connected_recorded_unified')
        runtime = to_runtime_task_spec(task)
        self.require(str(folder)+':actual_duration_and_sample', runtime['simulation']['duration_s']==duration and runtime['simulation']['sample_s']==sample)
        outputs = task.get('outputs') or {}
        selected = list(outputs.get('plots') or [])+list(outputs.get('record_fields') or [])
        selected += [field for stream in outputs.get('telemetry_streams',[]) for field in stream.get('fields',[])]
        self.require(str(folder)+':requested_numeric_outputs_selected',all(any(fnmatch.fnmatchcase(field,pattern) for pattern in selected) for field in fields))
        rows = self.csv(root/'results/telemetry.csv',duration,sample,fields)
        return task, runtime, summary, rows

    def raw_response(self, folder, identity, require_identity=True):
        if not require_identity:
            row_evidence = identity
            agent = self.read(folder/'unified_agent_report.json')
            identity = agent.get('facade_result',{}).get('backend_identity') or {}
            self.require(str(folder)+':case_invocation_matches_original_agent_identity',
                         all(identity.get('invocation_evidence',{}).get(k)==row_evidence.get(k)
                             for k in ('request_id','model_id','response_sha256','structured_output_sha256')))
        self.require(str(folder)+':actual_model_identity', identity.get('actual_model_used') is True
                     and identity.get('actual_model_execution_verified') is True and identity.get('backend')=='openai_compatible'
                     and identity.get('backend_class')=='actual_model_service')
        evidence = identity.get('invocation_evidence') or {}
        self.require(str(folder)+':verified_model_invocation', evidence.get('verified') is True and evidence.get('model_id')==self.args.model)
        self.require(str(folder)+':invocation_identifiers_present', bool(evidence.get('request_id'))
                     and all(re.fullmatch('[0-9a-f]{64}',str(evidence.get(k) or '')) for k in ('prompt_sha256','response_sha256','structured_output_sha256')))
        calls = []
        for path in folder.glob('model_call*.json'):
            call = self.read(path)
            if call.get('ok') is True and call.get('metadata',{}).get('response_id')==evidence['request_id']:
                if hashlib.sha256(call.get('raw_text','').encode()).hexdigest()==evidence['response_sha256']:
                    calls.append((path,call))
        self.require(str(folder)+':one_hash_and_id_bound_final_raw_call', len(calls)==1)
        path, call = calls[0]
        metadata = call.get('metadata') or {}
        raw = json.loads(call['raw_text'])
        structured = hashlib.sha256(json.dumps(raw,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        self.require(str(folder)+':actual_structured_output_hash', structured==evidence['structured_output_sha256'])
        decoding = evidence.get('structured_generation') or {}
        self.require(str(folder)+':grounded_schema_hash_bound_and_no_substitution', decoding.get('enabled') is True
                     and decoding.get('answer_substitution') is False and re.fullmatch('[0-9a-f]{64}',str(decoding.get('schema_sha256') or '')) is not None
                     and decoding.get('schema_sha256')==metadata.get('structured_generation',{}).get('schema_sha256'))
        self.require(str(folder)+':prompt_hash_and_model_bound', metadata.get('prompt_sha256')==evidence['prompt_sha256'] and metadata.get('model')==self.args.model)
        self.details.setdefault('actual_raw_responses',[]).append({'path':path.relative_to(ROOT).as_posix(),
            'request_id':evidence['request_id'],'raw_response_sha256':evidence['response_sha256'],
            'grounded_schema_sha256':decoding['schema_sha256']})
        return raw.get('task_spec',raw)

    def exported(self, folder, log, duration, sample, fields):
        payload = self.read(log)
        self.require(str(folder)+':exported_native_execution_payload', payload.get('ok') is True
                     and payload.get('summary',{}).get('runtime_truth_status')=='instantiated_connected_recorded_unified'
                     and payload.get('validation_outcome',{}).get('result')=='PASS')
        dataset = payload['dataset']
        data_root = resolve(dataset['output_root'],folder)
        self.require('exported_dataset_owned:'+str(data_root), data_root.is_relative_to(folder.resolve()))
        rows = self.csv(resolve(dataset['files']['trace'],data_root),duration,sample,fields)
        self.remember(folder/'generated_simulation.py')
        return payload, rows

    def model_stages(self, thermal_driver, drivers):
        a = self.args
        thermal_root, followup = a.thermal_state.parent, a.ai_state.parent
        original = thermal_root/'actual-request'
        stages = [
            ('original_thermal', original, original/'actual', 20.,1.,EARTH_FIELDS,thermal_driver),
            ('thermal_link', followup/'thermal-link', followup/'thermal-link/actual',20.,1.,LINK_FIELDS,drivers['NEW_THERMAL_PARAMETER']),
            ('battery', followup/'battery', followup/'battery/actual',20.,.2,{'eps.effective_battery_capacity_wh','eps.battery_capacity_ratio','eps.battery_soc'},drivers['BATTERY']),
        ]
        for label, stage, folder, duration, sample, fields, driver in stages:
            report = self.report(stage/'report.json',self.declared_checks(driver)|RUNTIME_CHECKS)
            self.require(label+':core_before_and_after', report.get('source_before')==report.get('source_after')==a.core)
            raw = self.raw_response(folder,report.get('backend_identity') or {})
            raw_values = raw.get('parameters') or {}
            raw_values = raw_values.get('values',raw_values)
            self.require(label+':no_unrequested_optional_geometry_or_indexed_rw_overrides', not any(
                k.startswith(('rw_axis_b_','rw_mount_b_')) or k in {'rw_geometry_mode','rw_mount_radius_m'}
                or k.startswith('rw_') and k.endswith(('_0','_1','_2','_3')) for k in raw_values))
            task,runtime,summary,native_rows = self.native_proof(folder,duration,sample,fields)
            payload,script_rows = self.exported(folder,stage/'script.stdout.log',duration,sample,fields)
            values = runtime['parameters']
            self.require(label+':requested_environment_and_flux', values.get('environment_profile')=='spice_wmm'
                         and values.get('epoch_utc')=='2026-01-01T00:00:00Z'
                         and values.get('thermal_earth_ir_w_m2')==234. and values.get('thermal_earth_albedo')==.30)
            self.require(label+':requested_step_and_capability', runtime['simulation'].get('solver',{}).get('step_s')==.2
                         and task.get('model',{}).get('capability_id')=='whole_spacecraft.unified_native.v1')
            self.require(label+':no_template_fallback', task.get('metadata',{}).get('agent_safety',{}).get('model_fallback_used') is not True)
            if label!='battery':
                self.require(label+':no_generated_events', not any(task['events'].get(k) for k in ('faults','degradations','constraints')))
            if label=='original_thermal':
                self.require(label+':raw_requested_flux', raw_values.get('thermal_earth_ir_w_m2')==234. and raw_values.get('thermal_earth_albedo')==.30)
                self.require(label+':actual_finite_Earth_model', summary.get('thermal_environment',{}).get('model')=='finite_spherical_earth_lambertian_v1')
                self.require(label+':actual_recorded_Earth_heat', all(float(r['thermal.payload_earth_ir_heat_w'])>0 and float(r['thermal.payload_albedo_heat_w'])>=0 for r in native_rows))
            elif label=='thermal_link':
                self.require(label+':raw_and_bound_4W_parameter', raw_values.get('solar_panel_structure_conductance_w_k')==values.get('solar_panel_structure_conductance_w_k')==4.)
                for kind, actual in [('exported',payload['summary']),('native',summary)]:
                    links = [p for p in actual.get('thermal_conduction_paths',[]) if {p.get('from_node'),p.get('to_node')}=={'solar_panel','structure'}]
                    self.require(label+':'+kind+'_one_actual_4W_link', len(links)==1 and links[0].get('conductance_w_per_k')==4.)
            else:
                faults = list(raw.get('faults') or [])+list(raw.get('modifiers',{}).get('faults') or [])+list(raw.get('events',{}).get('faults') or [])
                self.require('battery_raw_exact_event', len(faults)==1 and faults[0].get('effect',faults[0].get('fault_type'))=='eps_battery_capacity_loss'
                             and faults[0].get('magnitude',faults[0].get('severity'))==.3 and faults[0].get('start_s',faults[0].get('onset_time_s'))==5.)
                self.require('battery_bound_exact_event', len(task['events']['faults'])==1
                             and task['events']['faults'][0]['effect']=='eps_battery_capacity_loss'
                             and task['events']['faults'][0]['magnitude']==.3 and task['events']['faults'][0]['start_s']==5.
                             and not task['events']['degradations'] and not task['events']['constraints'])
                self.require('battery_requested_hardware_parameters', values.get('battery_capacity_wh')==160. and values.get('initial_soc')==.62 and values.get('spacecraft_mass_kg')==100.)
                for kind, rows in [('exported',script_rows),('native',native_rows)]:
                    changed = [r for r in rows if abs(float(r['eps.effective_battery_capacity_wh'])-160.)>1e-8]
                    self.require('battery_'+kind+':actual_onset_within_one_native_step', bool(changed) and 5.-1e-9<=float(changed[0]['time_s'])<=5.2+1e-9)
                    self.require('battery_'+kind+':before_onset_is_nominal', all(math.isclose(float(r['eps.effective_battery_capacity_wh']),160.,abs_tol=1e-8) and math.isclose(float(r['eps.battery_capacity_ratio']),1.,abs_tol=1e-9) for r in rows if float(r['time_s'])<5.))
                    self.require('battery_'+kind+':actual_30pct_loss_persists', all(math.isclose(float(r['eps.effective_battery_capacity_wh']),112.,abs_tol=1e-8) and math.isclose(float(r['eps.battery_capacity_ratio']),.7,abs_tol=1e-9) for r in changed))
        chain = self.read(followup/'chain/report.json')
        self.require('three_create_refine_cases_terminal_pass', chain.get('status')=='PASS' and chain.get('passed_cases')==3 and chain.get('model_source_unchanged') is True
                     and {r.get('case_id') for r in chain.get('cases',[])}=={'adcs_create','adcs_refine','whole_create'})
        tasks = {}
        for row in chain['cases']:
            name = row['case_id']
            self.require(name+':case_passed', row.get('passed') is True)
            self.all_checks(name,row,RUNTIME_CHECKS|{'actual_model_invocation','no_fallback','valid','compiled','capability','requested_outputs','requested_duration','generated_script_executed'})
            folder = followup/'chain'/name
            self.require(name+':runtime_proof_path_bound', resolve(row['runtime_verification'])==(folder/'runtime-verification.json').resolve())
            self.raw_response(folder,row.get('invocation_evidence') or {},require_identity=False)
            duration = {'adcs_create':20.,'adcs_refine':30.,'whole_create':40.}[name]
            fields = {'adcs.pointing_error_deg','adcs.rw.speed_rad_s_0'} if name!='whole_create' else {'eps.battery_soc','thermal.payload_temp_k'}
            task,_,_,_ = self.native_proof(folder,duration,1.,fields)
            self.require(name+':actual_capability',task.get('model',{}).get('capability_id')==
                         ('whole_spacecraft.unified_native.v1' if name=='whole_create' else 'subsystem.adcs_unified_native.v1'))
            self.exported(folder,folder/'stdout.log',duration,1.,fields)
            self.require(name+':exported_script_hash_bound', digest(folder/'generated_simulation.py')==row.get('script_sha256'))
            tasks[name] = task
        self.require('refinement_does_not_reset_hardware_or_events', tasks['adcs_create']['parameters']==tasks['adcs_refine']['parameters']
                     and tasks['adcs_create']['events']==tasks['adcs_refine']['events'])
        return followup/'browser'

    def browser_and_charts(self, browser_root, drivers):
        from sat_sim.task_models import canonicalize_task_spec, to_runtime_task_spec
        browser_required = self.browser_required_checks(drivers['MODEL_BROWSER'])
        report = self.report(browser_root/'report.json',browser_required)
        self.details['required_model_browser_check_count'] = len(browser_required)
        health = self.read(browser_root/'actual-model-health.json')
        provenance = self.read(self.args.training_provenance)
        manifest = self.remember(self.args.dataset_manifest)
        source = health.get('source') or {}
        self.require('browser_actual_trained_ModelScope_identity', health.get('ok') is True and health.get('model')==self.args.model
                     and source.get('engine')=='transformers' and source.get('adapter_loaded') is True
                     and source.get('training_provenance_sha256')==digest(self.args.training_provenance)
                     and source.get('base_model')==provenance.get('base_model') and source.get('base_content_sha256')==provenance.get('base_content_sha256')
                     and source.get('dataset_manifest_sha256')==provenance.get('dataset_manifest_sha256') and provenance.get('completed_at_utc')
                     and bool(provenance.get('adapter_files')) and provenance.get('state')!='SMOKE_TRAINING_ONLY')
        self.require('actual_training_manifest_identity',digest(manifest)==provenance.get('dataset_manifest_sha256'))
        for entry in provenance.get('adapter_files') or []:
            path = self.remember(resolve(entry['path'],resolve(provenance['adapter_dir'])))
            self.require('actual_trained_adapter_file:'+path.name,digest(path)==entry['sha256'])
        generation = self.read(browser_root/'browser-agent-generation.json')
        result = generation.get('result') or {}
        self.require('browser_generation_result_really_succeeded', generation.get('ok') is True and result.get('ok') is True)
        files = result.get('files') or {}
        folder = resolve(files['task_spec_json']).parent
        self.require('browser_agent_artifacts_owned',folder.is_relative_to(browser_root.resolve()))
        self.raw_response(folder,result.get('facade_result',{}).get('backend_identity') or {})
        detail = self.read(browser_root/'browser-run-detail.json')
        root = self.bundle(detail['prepared_run']['bundle_root'],browser_root)
        task = self.read(root/'input/task_spec.json')
        generated = canonicalize_task_spec(result['task_spec'])
        from sat_sim.capability_registry import get_capability
        actual_parameters = to_runtime_task_spec(task)['parameters']
        generated_parameters = to_runtime_task_spec(generated)['parameters']
        definitions = get_capability('whole_spacecraft.unified_native.v1').data['parameters']
        additions = {k: v for k, v in actual_parameters.items() if k not in generated_parameters}
        self.remember(ROOT/'src/sat_sim/capabilities/whole_spacecraft.unified_native.v1.yaml')
        self.require('browser_every_generated_parameter_is_preserved', all(
            k in actual_parameters and actual_parameters[k] == v for k,v in generated_parameters.items()))
        self.require('browser_added_parameters_are_exact_registered_defaults', all(
            k in definitions and 'default' in definitions[k] and v == definitions[k]['default']
            for k,v in additions.items()))
        self.details['browser_default_materialization'] = {
            'generated_parameter_count': len(generated_parameters), 'sealed_parameter_count': len(actual_parameters),
            'additional_registered_defaults': additions,
            'scope': 'Form serialization materializes registered defaults omitted by the generated draft. All generated values remain exact; added values are individually checked against the frozen registry. This is not raw-model accuracy.'}
        self.require('browser_generated_configuration_bound_to_native_run',
                     all(task['parameters'].get(k) == generated['parameters'].get(k) for k in ['profile','overrides'])
                     and task.get('events')==generated.get('events')
                     and task.get('model',{}).get('capability_id')==generated.get('model',{}).get('capability_id')=='whole_spacecraft.unified_native.v1'
                     and task==canonicalize_task_spec(detail['task_spec']))
        runtime = to_runtime_task_spec(task)
        self.require('browser_request_parameters_survived',runtime['simulation']['duration_s']==20.
                     and runtime['simulation']['sample_s']==1. and runtime['simulation'].get('solver',{}).get('step_s')==.2
                     and runtime['parameters'].get('environment_profile')=='spice_wmm'
                     and runtime['parameters'].get('thermal_earth_ir_w_m2')==234. and runtime['parameters'].get('thermal_earth_albedo')==.30)
        self.csv(root/'results/telemetry.csv',20.,1.,EARTH_FIELDS)
        downloaded = self.read(browser_root/'downloaded-script-result.json')
        self.require('browser_downloaded_python_native_result',downloaded.get('ok') is True and downloaded.get('validation_outcome',{}).get('result')=='PASS')
        trace = self.read(browser_root/'downloaded-script-trace-check.json')
        trace_path = self.remember(trace['path'])
        self.require('browser_downloaded_trace_hash',trace.get('sha256')==digest(trace_path))
        self.csv(trace_path,20.,1.,EARTH_FIELDS)
        charts = self.report(self.args.chart_report)
        self.require('native_charts_bound_to_frozen_application_and_core', charts.get('application_before')==charts.get('application_after')==self.args.application
                     and charts.get('core_before')==charts.get('core_after')==self.args.core)
        chart_driver = self.remember(BASE/f'verify_{self.args.prefix.replace("-","_")}_charts.py')
        self.require('native_chart_driver_hash',charts.get('driver_sha256')==digest(chart_driver))
        chart_required = self.chart_required_checks(chart_driver)
        self.require('native_chart_declared_checks_present',chart_required<=set(charts['checks']))
        self.details['required_native_chart_check_count'] = len(chart_required)
        self.require('browser_core_source_unchanged',report.get('source_before')==report.get('source_after')==self.args.core)
        for owner in (browser_root,self.args.chart_report.parent):
            seals = list(owner.rglob('SEALED.json'))
            self.require('extra_browser_chart_native_seals_exist:'+str(owner),bool(seals))
            for seal in seals:
                self.bundle(seal.parent,owner)
        self.require('at_least_six_distinct_production_native_proofs',len(self.bundles)>=6)
        self.details['required_visual_source_reports'] = [str(browser_root/'report.json'),str(self.args.chart_report)]
        self.details['required_visual_images'] = [str(p.resolve()) for owner in (browser_root,self.args.chart_report.parent) for p in sorted(owner.glob('*.png'))]
        self.require('actual_browser_and_native_chart_images_present',bool(list(browser_root.glob('*.png'))) and bool(list(self.args.chart_report.parent.glob('*.png'))))

    def visual_reviews(self):
        self.require('independent_actual_image_reviews_supplied',bool(self.args.visual_review))
        viewed, bound_reports = {}, {}
        for path in self.args.visual_review:
            review = self.read(path)
            version = review.get('version_scope') or {}
            self.require(str(path)+':reviewer_and_method',bool(review.get('reviewer')) and bool(review.get('method')))
            self.require(str(path)+':same_application_and_core',review.get('application',version.get('application_sha256'))==self.args.application
                         and review.get('core',version.get('physics_source_sha256'))==self.args.core)
            self.require(str(path)+':no_pending_or_failed_visual_result',review.get('status') in {'PASS_ACTUAL_IMAGE_REVIEW','INDEPENDENT_ACTUAL_IMAGE_REVIEW_PASS'})
            self.require(str(path)+':no_unresolved_visual_findings',review.get('findings')==[] and review.get('unresolved_findings',[])==[])
            records = review.get('viewed_images') or []
            self.require(str(path)+':actual_view_records_present',bool(records))
            for image in records:
                image_path = self.remember(image['path'])
                self.require(str(image_path)+':actually_viewed_and_visually_passed',image.get('actually_viewed') is True
                             and image.get('visual_status')=='PASS' and bool(image.get('view_method')) and bool(image.get('observations')))
                self.require(str(image_path)+':viewed_bytes_unchanged',image.get('sha256')==digest(image_path))
                viewed[str(image_path)] = digest(image_path)
            report_map = review.get('source_report_sha256') or review.get('report_sha256') or {}
            for name, expected in report_map.items():
                source = self.remember(name)
                self.require(str(source)+':visual_report_source_hash',digest(source)==expected)
                bound_reports[str(source)] = expected
        self.require('every_current_source_report_is_bound_by_visual_review',set(self.details['required_visual_source_reports'])<=set(bound_reports))
        self.require('every_current_root_screenshot_was_actually_viewed',set(self.details['required_visual_images'])<=set(viewed))
        self.details['rendered_image_sha256'] = {resolve(path).relative_to(ROOT).as_posix():value for path,value in viewed.items()}
        self.details['visual_review_performed_by_this_program'] = False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--application',required=True)
    parser.add_argument('--core',required=True)
    parser.add_argument('--qualification-state',type=Path,required=True)
    parser.add_argument('--expected-testcases',type=int,required=True)
    parser.add_argument('--expected-skips',type=int,default=5)
    parser.add_argument('--thermal-state',type=Path)
    parser.add_argument('--ai-state',type=Path)
    parser.add_argument('--chart-report',type=Path)
    parser.add_argument('--training-provenance',type=Path,default=ROOT/'training_runs/qwen25_coder15_taskspec_lora_v5_current_prompt_event_aligned/training_provenance.json')
    parser.add_argument('--dataset-manifest',type=Path,default=ROOT/'data/training/taskspec_lora_v5_current_prompt_event_aligned/manifest.json')
    parser.add_argument('--model',default=MODEL_DEFAULT)
    parser.add_argument('--stage',choices=('artifacts','all'),default='all')
    parser.add_argument('--visual-review',type=Path,action='append',default=[])
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    for value in (args.application,args.core):
        if not re.fullmatch('[0-9a-f]{64}',value):
            parser.error('application/core must be explicitly supplied SHA-256 values')
    args.qualification_state = resolve(args.qualification_state)
    args.prefix = args.qualification_state.parent.name.removesuffix('-qualification')
    if not args.qualification_state.parent.name.endswith('-qualification'):
        parser.error('qualification-state must be inside the named *-qualification evidence directory')
    args.thermal_state = resolve(args.thermal_state or BASE/(args.prefix+'-real-thermal/state.json'))
    args.ai_state = resolve(args.ai_state or BASE/(args.prefix+'-ai-followup/state.json'))
    args.chart_report = resolve(args.chart_report or BASE/(args.prefix+'-visual-charts/report.json'))
    args.training_provenance = resolve(args.training_provenance)
    args.dataset_manifest = resolve(args.dataset_manifest)
    args.output = resolve(args.output)
    if not args.output.is_relative_to(BASE) or args.output.exists():
        parser.error('a fresh ignored reports/local_requirements_completion output file is required')
    reviewer = Reviewer(args)
    report = {'schema_version':'sat-independent-qualification-review.v1','status':'RUNNING',
        'application':args.application,'core':args.core,'stage':args.stage,
        'started_at_utc':datetime.now(timezone.utc).isoformat(),
        'scope':'Independent artifact verification for this frozen application; no model generation, browser action, visual inference, native execution, flight approval or actual24h credit.',
        'visual_review_performed_by_this_program':False,'actual24h_qualified':False,
        'review_driver_sha256':digest(Path(__file__))}
    report['historical_review_logic_reference_sha256'] = {
        path.relative_to(ROOT).as_posix():digest(path)
        for name in ('review_rw_grounded_ai_stages.py','rw-grounded-thermal-independent-review.json')
        if (path:=BASE/name).is_file()
    }
    report['historical_references_are_not_current_application_qualification'] = True
    try:
        module = runpy.run_path(str(ROOT/'scripts/run_native_endurance.py'),run_name='independent_review_fingerprint')
        fp = module['runtime_source_fingerprint']
        current = lambda: (fp(module['application_source_manifest'](ROOT)),fp(module['runtime_source_manifest'](ROOT)))
        reviewer.require('current_application_and_core_match_explicit_target',current()==(args.application,args.core))
        reviewer.qualification()
        thermal_driver,drivers = reviewer.wrapper_states()
        browser = reviewer.model_stages(thermal_driver,drivers)
        reviewer.browser_and_charts(browser,drivers)
        if args.stage=='all':
            reviewer.visual_reviews()
            report['status'] = 'INDEPENDENT_PRODUCTION_QUALIFICATION_AND_VISUAL_REVIEW_PASS'
        else:
            report['status'] = 'ARTIFACTS_VERIFIED_VISUAL_REVIEW_PENDING'
        reviewer.require('source_still_bound_after_review',current()==(args.application,args.core))
        reviewer.require('tests_still_bound_after_review',
            {p.relative_to(ROOT).as_posix():digest(p) for p in (ROOT/'tests').rglob('*.py')}==reviewer.expected_tests)
        for path, expected in list(reviewer.sources.items()):
            reviewer.require('reviewed_artifact_unchanged:'+path,digest(resolve(path))==expected)
        reviewer.require('review_driver_unchanged',digest(Path(__file__))==report['review_driver_sha256'])
    except Exception as exc:
        report.update(status='FAILED_OR_INCOMPLETE_REQUIRES_REVIEW',error={'type':type(exc).__name__,'message':str(exc)})
    report.update(checks=reviewer.checks,details=reviewer.details,sealed_bundles=reviewer.bundles,
        source_artifact_sha256=reviewer.sources,
        qualification_state_sha256=digest(args.qualification_state) if args.qualification_state.is_file() else None,
        rendered_image_sha256=reviewer.details.get('rendered_image_sha256',{}),
        finished_at_utc=datetime.now(timezone.utc).isoformat())
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as handle:
        json.dump(report,handle,ensure_ascii=False,indent=2,allow_nan=False);handle.write('\n')
    print(json.dumps({'status':report['status'],'checks':len(reviewer.checks),'seals':len(reviewer.bundles),'error':report.get('error')}))
    return 0 if report['status'] in {'INDEPENDENT_PRODUCTION_QUALIFICATION_AND_VISUAL_REVIEW_PASS','ARTIFACTS_VERIFIED_VISUAL_REVIEW_PENDING'} else 1


if __name__=='__main__':
    raise SystemExit(main())
