"""Dataset and model provenance checks independent of CUDA libraries."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath


def sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024*1024):
            result.update(block)
    return result.hexdigest()


def load_training_data(directory: Path):
    manifest = json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    records = {}
    for split in ('train','validation'):
        path = directory/f'{split}.jsonl'
        if sha256(path) != manifest['splits'][split]['sha256']:
            raise ValueError(f'SFT_DATASET_HASH_MISMATCH: {split}')
        records[split] = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
        if len(records[split]) != manifest['splits'][split]['count'] or not records[split]:
            raise ValueError('SFT_DATASET_COUNT_MISMATCH')
        for record in records[split]:
            if [item['role'] for item in record['messages']] != ['system','user','assistant']:
                raise ValueError('SFT_MESSAGES_INVALID')
            if not isinstance(json.loads(record['messages'][-1]['content']), dict):
                raise ValueError('SFT_TARGET_MUST_BE_JSON_OBJECT')
            if manifest.get('request_configuration_policy') == 'explicit_template_parameters_and_time_v1':
                try:
                    request = record['request']
                    given = json.loads(request.split(' Required configuration: ', 1)[1])
                    target = json.loads(record['messages'][-1]['content'])
                    actual = {
                        'target_mode': target['target']['mode'],
                        'sample_s': target['simulation']['sample_s'],
                        'solver.step_s': target['simulation']['solver']['step_s'],
                        'parameters': target['parameters'],
                    }
                    matches = given == actual and request in record['messages'][1]['content']
                except (KeyError, IndexError, TypeError, ValueError):
                    matches = False
                if not matches:
                    raise ValueError(f'SFT_INPUT_LABEL_CONFIGURATION_MISMATCH: {record.get("id")}')
            if manifest.get('request_event_policy') == 'explicit_runtime_normalized_events_v1':
                from sat_sim.capability_agent import _explicit_event_requirement_issues
                target = json.loads(record['messages'][-1]['content'])
                if _explicit_event_requirement_issues(target, record.get('request',''), require_declaration=True):
                    raise ValueError(f'SFT_INPUT_LABEL_EVENT_MISMATCH: {record.get("id")}')
    for key in ('id','family'):
        if {r[key] for r in records['train']} & {r[key] for r in records['validation']}:
            raise ValueError(f'SFT_EVALUATION_LEAKAGE: {key}')
    return records, manifest


def verify_base_model(manifest_path: Path):
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('schema_version') != 'sat-sft-base-model.v1' or manifest.get('trust_remote_code') is not False:
        raise ValueError('SFT_BASE_MODEL_PROVENANCE_INVALID')
    files = manifest.get('files') or []
    if not any(item.get('path','').endswith('.safetensors') for item in files):
        raise ValueError('SFT_BASE_MODEL_WEIGHTS_NOT_HASHED')
    content_hash = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    if content_hash != manifest.get('snapshot_content_sha256'):
        raise ValueError('SFT_BASE_MODEL_MANIFEST_HASH_MISMATCH')
    snapshot = Path(manifest['snapshot']).resolve()
    cache_root = manifest_path.resolve().parent
    for item in files:
        if PurePosixPath(item['path']).is_absolute() or '..' in PurePosixPath(item['path']).parts:
            raise ValueError('SFT_BASE_MODEL_UNSAFE_RELATIVE_PATH')
        path = (snapshot/item['path']).resolve()
        if not path.is_relative_to(cache_root) or sha256(path) != item['sha256']:
            raise ValueError(f'SFT_BASE_MODEL_HASH_MISMATCH: {item["path"]}')
    return snapshot, manifest


def encode_record(tokenizer, record, max_length):
    prefix = tokenizer.apply_chat_template(record['messages'][:-1], tokenize=True,
        add_generation_prompt=True, enable_thinking=False)
    answer = tokenizer.encode(record['messages'][-1]['content'], add_special_tokens=False)+[tokenizer.eos_token_id]
    if len(prefix)+len(answer) > max_length:
        raise ValueError(f'SFT_SEQUENCE_TOO_LONG: {record["id"]}; tokens={len(prefix)+len(answer)}; limit={max_length}')
    return {'input_ids':prefix+answer, 'attention_mask':[1]*(len(prefix)+len(answer)),
            'labels':[-100]*len(prefix)+answer}
