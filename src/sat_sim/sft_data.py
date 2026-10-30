"""Contract-supervised SFT data; formal evaluation cases never supply labels."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .capability_agent import build_capability_json_prompt, _explicit_event_requirement_issues
from .capability_registry import active_capability_ids
from .llm_backends import OPENAI_TASKSPEC_SYSTEM_PROMPT
from .scenario_templates import instantiate_scenario_template, list_scenario_templates
from .task_models import canonicalize_task_spec, to_runtime_task_spec
from .task_validator import validate_task_spec
from .task_compiler import compile_task_spec
from .form_schema import capability_output_defaults


def digest(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def semantic_family(spec):
    effects = sorted(str(event.get('effect')) for kind in ('faults','degradations','constraints')
                     for event in spec['events'].get(kind, []))
    return '|'.join((spec['model']['capability_id'], spec['model']['target'].get('mode','nominal'), *effects))


def _reserved_requests(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ('request', 'request_text') and isinstance(item, str):
                yield digest(item)
            yield from _reserved_requests(item)
    elif isinstance(value, list):
        for item in value:
            yield from _reserved_requests(item)


def build_dataset(output: Path, *, evaluation_manifest: Path | None = None) -> dict:
    if any((output/name).exists() for name in ('train.jsonl','validation.jsonl','manifest.json')):
        raise FileExistsError('SFT_OUTPUT_ALREADY_EXISTS_USE_FRESH_DIRECTORY')
    output.mkdir(parents=True, exist_ok=True)
    reserved = set()
    if evaluation_manifest is not None:
        reserved.update(_reserved_requests(json.loads(evaluation_manifest.read_text(encoding='utf-8'))))
    active = set(active_capability_ids())
    system = OPENAI_TASKSPEC_SYSTEM_PROMPT
    records = {'train':[], 'validation':[]}
    rejected = []
    seen = set()
    families = {'train':set(), 'validation':set()}
    for template in list_scenario_templates()['templates']:
        capability_id = template['capability_id']
        if capability_id not in active:
            continue
        source = instantiate_scenario_template(template['template_id'])
        source['outputs'].update(capability_output_defaults(capability_id))
        # The request must describe the same event delivery that the emitted
        # legacy runtime label will actually use, including migration aliases.
        source = canonicalize_task_spec(to_runtime_task_spec(source))
        family = semantic_family(source)
        split = 'validation' if int(digest(family)[:8], 16) % 5 == 0 else 'train'
        for duration in (30., 60., 120., 300.):
            for variant in range(3):
                spec = deepcopy(source)
                scale = duration/float(spec['simulation']['duration_s'])
                spec['simulation']['duration_s'] = duration
                spec['simulation']['sample_s'] = min(float(spec['simulation']['sample_s']), duration/5.)
                spec['simulation']['step_s'] = min(float(spec['simulation']['step_s']), spec['simulation']['sample_s'])
                spec['simulation'].setdefault('solver', {})['step_s'] = spec['simulation']['step_s']
                for kind in ('faults','degradations','constraints'):
                    for event in spec['events'].get(kind, []):
                        for field in ('start_s','end_s'):
                            if event.get(field) is not None:
                                event[field] = float(event[field])*scale
                spec['task']['id'] = 'generated_task'
                spec['outputs']['output_root'] = 'datasets/generated_task'
                events = json.dumps(spec['events'], ensure_ascii=False, separators=(',',':'))
                mode = spec['model']['target'].get('mode','nominal')
                requests = (
                    f"请创建{template['name']}的{duration:g}秒工程仿真，采用注册能力 {capability_id}。场景：{template['summary']}。事件配置：{events}。输出默认遥测。",
                    f"Create a {duration:g}-second internal engineering simulation for {template['object_id']}, mode={mode}. Use {capability_id}. Scenario: {template['summary']}. Events: {events}. Keep registered default telemetry.",
                    f"使用 {capability_id} 配置{template['name']}，总时长{duration:g}s；{template['summary']}；事件为{events}。生成可运行的仿真任务，参数和输出采用登记默认值。",
                )
                # Every value used by exact semantic scoring must be available
                # to the student. Template-only sampling/parameter overrides
                # otherwise create an unknowable target, even for a valid task.
                settings = {
                    'target_mode': mode,
                    'sample_s': spec['simulation']['sample_s'],
                    'solver.step_s': spec['simulation']['step_s'],
                    'parameters': spec['parameters'].get('values', spec['parameters']),
                }
                request = requests[variant] + ' Required configuration: ' + json.dumps(
                    settings, ensure_ascii=False, separators=(',', ':'))
                request_hash = digest(request)
                if request_hash in reserved or request_hash in seen:
                    continue
                seen.add(request_hash)
                try:
                    target = to_runtime_task_spec(spec)
                    # Template authoring metadata is not a generation target.
                    target.pop('metadata', None)
                    validation = validate_task_spec(target)
                    validation.raise_for_errors()
                    if _explicit_event_requirement_issues(target, request, require_declaration=True):
                        raise ValueError('SFT_INPUT_LABEL_EVENT_MISMATCH')
                    compile_task_spec(target)
                    prompt = build_capability_json_prompt(request, allowed_capabilities=(capability_id,))
                except Exception as exc:
                    rejected.append({'template_id':template['template_id'], 'duration_s':duration, 'reason':str(exc)})
                    continue
                record = {'id':request_hash, 'family':family, 'template_id':template['template_id'],
                    'request':request, 'capability_id':capability_id,
                    'label_source':'synthetic_registry_contract_not_flight_data',
                    'messages':[{'role':'system','content':system}, {'role':'user','content':prompt},
                                {'role':'assistant','content':json.dumps(target, ensure_ascii=False, separators=(',',':'))}]}
                records[split].append(record)
                families[split].add(family)
    if not all(records.values()) or families['train'] & families['validation']:
        raise RuntimeError('SFT_SPLIT_INVALID_OR_SEMANTIC_LEAKAGE')
    files = {}
    for split, items in records.items():
        path = output/f'{split}.jsonl'
        content = ''.join(json.dumps(item, ensure_ascii=False, sort_keys=True)+'\n' for item in sorted(items,key=lambda item:item['id']))
        path.write_text(content, encoding='utf-8', newline='\n')
        files[split] = {'path':str(path), 'sha256':digest(content), 'count':len(items), 'family_count':len(families[split])}
    manifest = {'schema_version':'sat-taskspec-sft-data.v1', 'splits':files,
        'semantic_family_overlap':0, 'formal_evaluation_request_overlap':0,
        'label_source':'synthetic_registry_contract_not_flight_data',
        'default_output_policy':'shared_ui_agent_registry_v1',
        'request_configuration_policy':'explicit_template_parameters_and_time_v1',
        'request_event_policy':'explicit_runtime_normalized_events_v1',
        'builder_source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'prompt_and_guard_source_sha256':hashlib.sha256(Path(__file__).with_name('capability_agent.py').read_bytes()).hexdigest(),
        'formal_evaluation_manifest_sha256':hashlib.sha256(evaluation_manifest.read_bytes()).hexdigest() if evaluation_manifest else None,
        'rejected':rejected, 'student_model_accuracy':'NOT_EVALUATED'}
    (output/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return manifest
