#!/usr/bin/env python3
"""用途：对真实本地 ModelScope 服务执行独立场景族的生成、编译和脚本运行验收。
参数：--model、--base-url、--output-dir；默认每个验证场景族选取一个固定样本。
输出：原始模型响应、来源摘要、逐项语义检查及脚本执行证据；不使用标签修补响应。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from sat_sim_ml.data import load_training_data, sha256  # noqa: E402
from sat_sim_ml.evaluation import verify_task_execution, registry_bound_messages  # noqa: E402
from sat_sim.task_models import canonicalize_task_spec  # noqa: E402
from sat_sim.task_validator import validate_task_spec  # noqa: E402
from sat_sim.task_compiler import compile_task_spec  # noqa: E402
from sat_sim.script_exporter import export_runner_script  # noqa: E402


def request_json(url, body=None, timeout=300):
    raw = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    request = urllib.request.Request(url, data=raw, headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def evaluate_product(args, output, health):
    """Test the production NL router, refinement, compiler and generated runner."""
    from sat_sim.lmstudio_acceptance import DEFAULT_CASES
    from sat_sim.model_providers import ModelProviderProfile
    from sat_sim.unified_agent import UnifiedAgentRequest, run_unified_agent
    profile = ModelProviderProfile(provider_id='local-modelscope', label='ModelScope actual evaluation',
        backend='openai_compatible', location='local', tier='L2', model=args.model,
        base_url=args.base_url.rstrip('/').removesuffix('/v1')+'/v1', structured_output='json_schema',
        temperature=0., timeout_s=300., max_output_tokens=4096, source='target_acceptance')
    provider_file = output/'providers.json'
    provider_file.write_text(json.dumps({'providers':[profile.to_dict(include_runtime=True)]}),encoding='utf-8')
    report = {'schema_version':'sat-modelscope-product-evaluation.v2', 'status':'RUNNING',
              'evaluator_source_sha256':sha256(Path(__file__)),
              'max_repair_attempts':args.max_repair_attempts,
              'model':args.model,'source':health['source'], 'scope':'Actual production NL chain; no template fallback accepted.',
              'cases':[]}
    specs = {}
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for case in DEFAULT_CASES:
        directory = output/case.case_id
        directory.mkdir()
        row = {'case_id':case.case_id,'passed':False,'checks':{}}
        checks = row['checks']
        try:
            if case.base_case_id and case.base_case_id not in specs:
                raise ValueError('BASE_CASE_DID_NOT_PRODUCE_A_VALID_TASK')
            result = run_unified_agent(UnifiedAgentRequest(input_kind='natural_language',
                request_text=case.request_text, base_task_spec=specs.get(case.base_case_id),
                output_dir=directory, backend='auto', provider_id='local-modelscope',
                provider_config_path=provider_file, routing_mode='local', compile_if_valid=True,
                model_timeout_s=300., model_temperature=0., model_max_output_tokens=4096,
                model_structured_output='json_schema', max_repair_attempts=args.max_repair_attempts,
                experience_reuse_enabled=False))
            spec = result.task_spec
            facade = result.facade_result.to_dict() if result.facade_result else {}
            identity = facade.get('backend_identity',{})
            invocation = identity.get('invocation_evidence',{})
            checks['actual_model_invocation'] = (identity.get('actual_model_execution_verified') is True and
                invocation.get('verified') is True and all(invocation.get(key) for key in
                ('model_id','request_id','prompt_sha256','response_sha256','structured_output_sha256')))
            checks['no_fallback'] = (result.route.provider_id=='local-modelscope' and
                identity.get('backend')=='openai_compatible' and identity.get('actual_model_used') is True and
                spec.get('metadata',{}).get('agent_safety',{}).get('model_fallback_used') is not True)
            checks['valid'] = result.validation.ok and result.guards.ok
            checks['compiled'] = result.compiled is not None
            checks['capability'] = spec.get('model',{}).get('capability_id') in case.expected_capability_ids
            outputs = json.dumps(spec.get('outputs',{})).lower()
            checks['requested_outputs'] = all(token.lower() in outputs for token in case.required_outputs)
            expected_duration = {'adcs_create':20.,'adcs_refine':30.,'whole_create':40.}[case.case_id]
            checks['requested_duration'] = spec.get('simulation',{}).get('duration_s') == expected_duration
            row['invocation_evidence'] = invocation
            if all(checks.values()):
                script = Path(result.files['generated_script']).resolve()
                # The product-generated script itself is run, without replacing its TaskSpec.
                with (directory/'stdout.log').open('w',encoding='utf-8') as stdout, (directory/'stderr.log').open('w',encoding='utf-8') as stderr:
                    process = subprocess.run([sys.executable,str(script)],cwd=directory,
                                             stdout=stdout,stderr=stderr,timeout=180,check=False)
                checks['generated_script_executed'] = process.returncode == 0
                row['script_sha256'] = sha256(script)
                if process.returncode == 0:
                    proof = verify_task_execution(spec, directory)
                    checks.update(proof['checks'])
                    row['runtime_verification'] = str(directory/'runtime-verification.json')
                    if proof['passed']:
                        specs[case.case_id] = spec
            else:
                checks['generated_script_executed'] = False
            row['passed'] = all(checks.values())
        except Exception as exc:
            row['error'] = {'type':type(exc).__name__,'message':str(exc)}
        report['cases'].append(row)
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({'case_id':case.case_id,'passed':row['passed'],'checks':checks}),flush=True)
    after = request_json(args.base_url.rstrip('/').removesuffix('/v1')+'/health')
    report['model_source_unchanged'] = after == health
    report['passed_cases'] = sum(row['passed'] for row in report['cases'])
    report['status'] = 'PASS' if report['model_source_unchanged'] and report['passed_cases']==len(DEFAULT_CASES) else 'FAIL'
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return 0 if report['status']=='PASS' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--base-url', default='http://127.0.0.1:8031')
    parser.add_argument('--dataset-dir', type=Path, default=ROOT/'data/training/taskspec_lora')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--all-validation', action='store_true')
    parser.add_argument('--product-chain', action='store_true', help='Evaluate the production NL creation/refinement chain instead of held-out prompts')
    parser.add_argument('--registry-constraints', action='store_true', help='Separate registry-augmented evaluation; preserve the frozen original-prompt baseline')
    parser.add_argument('--max-repair-attempts',type=int,choices=range(0,4),default=0)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output/'report.json').exists():
        parser.error('Choose a new output directory to preserve the previous evaluation')
    records, manifest = load_training_data(args.dataset_dir)
    selected = sorted(records['validation'], key=lambda r:(r['family'], r['id']))
    if not args.all_validation:
        selected = list({row['family']:row for row in reversed(selected)}.values())
        selected.sort(key=lambda row:row['family'])
    base = args.base_url.rstrip('/').removesuffix('/v1')
    health = request_json(base+'/health')
    source = health.get('source', {})
    if health.get('model') != args.model or source.get('engine') != 'transformers' or not source.get('base_content_sha256'):
        raise RuntimeError('ACTUAL_MODEL_SOURCE_IDENTITY_REQUIRED')
    if args.product_chain:
        return evaluate_product(args, output, health)
    report = {'schema_version':'sat-modelscope-heldout-evaluation.v2', 'status':'RUNNING',
              'evaluator_source_sha256':sha256(Path(__file__)),
              'model':args.model, 'source':source, 'dataset_manifest_sha256':sha256(args.dataset_dir/'manifest.json'),
              'selection':'all_validation' if args.all_validation else 'one_fixed_sample_per_validation_family',
              'prompt_policy':'registry_augmented' if args.registry_constraints else 'frozen_original',
              'validation_family_count':manifest['splits']['validation']['family_count'],
              'label_source':'synthetic_registry_contract_not_flight_data',
              'scope':'Held-out contract tasks; does not establish flight validity or general language coverage.',
              'cases':[]}
    def save():
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    save()
    for row in selected:
        case_dir = output/row['id']
        case_dir.mkdir()
        checks = {}
        case = {'id':row['id'],'family':row['family'],'checks':checks,'passed':False}
        started = time.perf_counter()
        try:
            # Only system and user messages are sent. The independent label remains local.
            messages = row['messages'][:2]
            if args.registry_constraints:
                messages, case['registry_constraints'] = registry_bound_messages(messages, row['family'].split('|',1)[0])
            payload = {'model':args.model,'messages':messages, 'max_tokens':4096,'temperature':0}
            (case_dir/'request.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
            case['request_sha256'] = hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
            response = request_json(base+'/v1/chat/completions', payload)
            (case_dir/'response.json').write_text(json.dumps(response,ensure_ascii=False,indent=2),encoding='utf-8')
            content = response['choices'][0]['message']['content']
            evidence = response.get('sat_sim',{})
            checks['actual_model_source'] = all(evidence.get(key)==value for key,value in source.items())
            checks['no_template_fallback'] = evidence.get('template_fallback_used') is False
            checks['response_hash'] = evidence.get('output_sha256') == hashlib.sha256(content.encode()).hexdigest()
            checks['completed'] = response['choices'][0]['finish_reason'] == 'stop'
            spec = json.loads(content)
            checks['json_object'] = isinstance(spec,dict)
            # Both forms are explicitly allowed by the production JSON prompt.
            if isinstance(spec, dict) and set(spec) == {'task_spec'}:
                spec = spec['task_spec']
            validate_task_spec(spec).raise_for_errors()
            checks['registry_valid'] = True
            actual = canonicalize_task_spec(spec)
            expected = canonicalize_task_spec(json.loads(row['messages'][-1]['content']))
            for key in ('model','simulation','parameters','events'):
                checks[key] = actual.get(key) == expected.get(key)
            checks['outputs'] = ({k:v for k,v in actual['outputs'].items() if k != 'output_root'} ==
                                 {k:v for k,v in expected['outputs'].items() if k != 'output_root'})
            # Execute only the validated, correctly bounded requested scenario.
            # A malformed/wrong answer is recorded as failure without label substitution.
            if all(checks.values()):
                compile_task_spec(spec)
                checks['compiled'] = True
                spec_path = case_dir/'model_taskspec.json'
                spec_path.write_text(json.dumps(spec,ensure_ascii=False,indent=2),encoding='utf-8')
                script = export_runner_script(spec_path,case_dir/'run.py',output_root=case_dir/'dataset')
                with (case_dir/'stdout.log').open('w',encoding='utf-8') as stdout, (case_dir/'stderr.log').open('w',encoding='utf-8') as stderr:
                    result = subprocess.run([sys.executable,str(script.output_path)],cwd=case_dir,
                                            stdout=stdout,stderr=stderr,timeout=180,check=False)
                checks['script_executed'] = result.returncode == 0
                case['script_sha256'] = sha256(script.output_path)
                case['script_returncode'] = result.returncode
                if result.returncode == 0:
                    execution = json.loads((case_dir/'stdout.log').read_text(encoding='utf-8'))
                    checks['execution_ok'] = execution.get('ok') is True
                    case['execution_summary'] = execution.get('summary')
                    if checks['execution_ok']:
                        proof = verify_task_execution(spec, case_dir)
                        checks.update(proof['checks'])
                        case['runtime_verification'] = str(case_dir/'runtime-verification.json')
            else:
                checks['script_executed'] = False
            case['passed'] = bool(checks) and all(checks.values())
        except Exception as exc:
            case['error'] = {'type':type(exc).__name__,'message':str(exc)}
        case['elapsed_s'] = time.perf_counter()-started
        report['cases'].append(case)
        save()
        print(json.dumps({'id':row['id'],'passed':case['passed'],'checks':checks}),flush=True)
    report['passed_cases'] = sum(row['passed'] for row in report['cases'])
    report['status'] = 'PASS' if report['cases'] and report['passed_cases']==len(report['cases']) else 'FAIL'
    save()
    return 0 if report['status']=='PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
