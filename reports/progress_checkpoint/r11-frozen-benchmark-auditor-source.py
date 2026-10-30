"""Read-only prompt/label/answer contract audit; never rescore or run answers."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from sat_sim.task_models import canonicalize_task_spec

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT/'reports/local_requirements_completion'
INPUTS = ROOT/'data/training/taskspec_lora_v3/validation.jsonl'
RESPONSES = BASE/'current-prompt-legacy-input-evaluation-v5/all-validation-registry'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def diff(actual, expected, path=''):
    if isinstance(actual,dict) and isinstance(expected,dict):
        output = []
        for key in sorted(actual.keys()|expected.keys()):
            output.extend(diff(actual.get(key),expected.get(key),f'{path}.{key}' if path else key))
        return output
    if isinstance(actual,list) and isinstance(expected,list):
        if len(actual) != len(expected):
            return [{'path':path,'actual':actual,'expected':expected}]
        output = []
        for index,(left,right) in enumerate(zip(actual,expected)):
            output.extend(diff(left,right,f'{path}[{index}]'))
        return output
    return [] if actual==expected else [{'path':path,'actual':actual,'expected':expected}]


def main():
    output = BASE/'r11-frozen-benchmark-contract-audit.json'
    if output.exists():
        raise ValueError('FRESH_OUTPUT_REQUIRED')
    diagnosis_path = BASE/'v5-original-input-response-diagnosis.json'
    report_path = RESPONSES/'report.json'
    sources = {str(path.relative_to(ROOT)):sha(path) for path in
               [INPUTS,diagnosis_path,report_path,ROOT/'src/sat_sim/task_models.py']}
    diagnosis = json.loads(diagnosis_path.read_text(encoding='utf-8'))
    original = json.loads(report_path.read_text(encoding='utf-8'))
    records = {row['id']:row for row in map(json.loads,INPUTS.read_text(encoding='utf-8').splitlines())}
    prior = {row['id']:row for row in diagnosis['cases']}
    categories = Counter()
    fields = Counter()
    audited = []
    for case in original['cases']:
        identifier = case['id']
        record = records[identifier]
        prompt = record['messages'][1]['content']
        context = json.loads(prompt.split('## Capability context\n```json\n',1)[1].split('\n```',1)[0])
        instructed = context['selected_capability']['default_trace_fields']
        label = canonicalize_task_spec(json.loads(record['messages'][-1]['content']))
        instruction = 'use default_trace_fields for plots' in prompt
        conflict = instruction and label['outputs']['plots'] != instructed
        response_path = RESPONSES/identifier/'response.json'
        response = json.loads(response_path.read_text(encoding='utf-8'))
        item = {'id':identifier,'family':case['family'], 'original_passed':case['passed'],
                'response_sha256':sha(response_path),
                'input_messages_sha256':hashlib.sha256(json.dumps(record['messages'],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                'prompt_instructs_registered_default_plots':instruction,
                'prompt_default_plots':instructed,'normalized_label_plots':label['outputs']['plots'],
                'prompt_label_plot_conflict':conflict}
        categories['prompt_label_plot_conflict'] += int(conflict)
        try:
            actual = json.loads(response['choices'][0]['message']['content'])
            if isinstance(actual,dict) and set(actual)=={'task_spec'}:
                actual=actual['task_spec']
            actual=canonicalize_task_spec(actual)
            differences = []
            for key in ['model','simulation','parameters','events','outputs']:
                left,right = actual.get(key),label.get(key)
                if key=='outputs':
                    left={name:value for name,value in left.items() if name!='output_root'}
                    right={name:value for name,value in right.items() if name!='output_root'}
                differences.extend(diff(left,right,key))
            assert sorted(differences,key=lambda row:row['path'])==sorted(prior[identifier]['differences'],key=lambda row:row['path'])
            item['differences']=differences
            item['actual_plots_follow_prompt_default']=actual['outputs']['plots']==instructed
            categories['parseable_actual_plots_follow_prompt_default']+=int(item['actual_plots_follow_prompt_default'])
            nonplots=[row for row in differences if row['path']!='outputs.plots']
            category='only_plot_label_difference' if not nonplots else 'other_label_differences'
            categories[category]+=1
            fields.update(row['path'] for row in nonplots)
        except (json.JSONDecodeError,ValueError,TypeError,KeyError) as exc:
            # A new diagnosis must reproduce the same raw parsing failure.
            assert 'parse_or_projection_error' in prior[identifier]
            item['parse_error']={'type':type(exc).__name__,'message':str(exc)}
            categories['parse_or_projection_error']+=1
        audited.append(item)
    assert len(audited)==69 and original['status']=='FAIL'
    assert sum(case['passed'] for case in original['cases'])==0
    assert all(sha(ROOT/path)==digest for path,digest in sources.items())
    result={'status':'READ_ONLY_CONTRACT_AUDIT_COMPLETE','observed_at_utc':datetime.now(timezone.utc).isoformat(),
            'scope':'Preserves original strict0/69 FAIL, raw requests, answers, labels and evaluator. No alternative pass rate; no wrong answer executed, no model retrained. Identifies prompt-label conflicts separately from remaining generation differences.',
            'original_score':{'passed':0,'total':69,'status':'FAIL'},'cases':audited,
            'category_counts':dict(categories),'non_plot_difference_path_counts':dict(fields),
            'source_sha256':sources,'source_inputs_unchanged':True,'auditor_sha256':sha(Path(__file__)),
            'interpretation':'All frozen prompts instruct default_trace_fields for plots while normalized labels expect empty plots. The65 parseable answers follow that instruction. This contract conflict makes the original strict pass count unsuitable as a standalone estimate of semantic generalization.54 cases differ only on that field;11 also differ elsewhere;4 are unparseable. Those remaining differences are not excused or rescored, and broader AI generalization remains unmeasured.'}
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({key:result[key] for key in ['status','original_score','category_counts','non_plot_difference_path_counts','source_inputs_unchanged']}))


if __name__=='__main__':
    main()
