"""Data corruption and evaluation contamination must stop GPU training."""
import hashlib
import json
from pathlib import Path

import pytest

from sat_sim_ml.data import load_training_data, sha256, verify_base_model


def dataset(tmp_path, *, same_family=False):
    splits = {}
    for split in ('train','validation'):
        row = {'id':split, 'family':'shared' if same_family else split,
               'messages':[{'role':'system','content':'system'}, {'role':'user','content':'user'},
                           {'role':'assistant','content':'{"task_id":"example"}'}]}
        path = tmp_path/f'{split}.jsonl'
        path.write_text(json.dumps(row)+'\n', encoding='utf-8')
        splits[split] = {'sha256':sha256(path),'count':1}
    (tmp_path/'manifest.json').write_text(json.dumps({'splits':splits}), encoding='utf-8')


def test_modified_dataset_stops_before_training(tmp_path):
    dataset(tmp_path)
    (tmp_path/'train.jsonl').write_text('{}\n', encoding='utf-8')
    with pytest.raises(ValueError, match='SFT_DATASET_HASH_MISMATCH'):
        load_training_data(tmp_path)


def test_builder_never_overwrites_an_existing_training_dataset(tmp_path):
    from sat_sim.sft_data import build_dataset
    original = tmp_path/'train.jsonl'
    original.write_text('immutable previous training evidence\n',encoding='utf-8')
    before = original.read_bytes()
    with pytest.raises(FileExistsError,match='SFT_OUTPUT_ALREADY_EXISTS_USE_FRESH_DIRECTORY'):
        build_dataset(tmp_path)
    assert original.read_bytes()==before


@pytest.mark.parametrize('corruption', ['hidden_sample_override', 'request_missing_from_prompt'])
def test_rehashed_data_still_requires_visible_matching_configuration(tmp_path, corruption):
    dataset(tmp_path)
    manifest_path = tmp_path/'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['request_configuration_policy'] = 'explicit_template_parameters_and_time_v1'
    for split in ('train', 'validation'):
        path = tmp_path/f'{split}.jsonl'
        row = json.loads(path.read_text(encoding='utf-8'))
        settings = {'target_mode':'nominal', 'sample_s':1., 'solver.step_s':.1, 'parameters':{'power_w':10.}}
        row['request'] = 'Run 30 seconds. Required configuration: ' + json.dumps(settings)
        row['messages'][1]['content'] = row['request']
        target = {'target':{'mode':'nominal'}, 'simulation':{'sample_s':1., 'solver':{'step_s':.1}}, 'parameters':{'power_w':10.}}
        if split == 'train':
            if corruption == 'hidden_sample_override':
                target['simulation']['sample_s'] = 6.
            else:
                row['messages'][1]['content'] = 'Run 30 seconds.'
        row['messages'][-1]['content'] = json.dumps(target)
        path.write_text(json.dumps(row)+'\n', encoding='utf-8')
        manifest['splits'][split]['sha256'] = sha256(path)
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='SFT_INPUT_LABEL_CONFIGURATION_MISMATCH'):
        load_training_data(tmp_path)


def test_rehashed_but_semantically_leaking_split_is_rejected(tmp_path):
    dataset(tmp_path, same_family=True)
    with pytest.raises(ValueError, match='SFT_EVALUATION_LEAKAGE: family'):
        load_training_data(tmp_path)


def test_base_model_requires_complete_hash_evidence(tmp_path):
    snapshot = tmp_path/'snapshot'
    snapshot.mkdir()
    weight = snapshot/'model.safetensors'
    weight.write_bytes(b'fixture, not real weights')
    files = [{'path':weight.name,'sha256':sha256(weight),'bytes':weight.stat().st_size}]
    manifest = {'schema_version':'sat-sft-base-model.v1','trust_remote_code':False,
                'snapshot':str(snapshot),'files':files,
                'snapshot_content_sha256':hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()}
    path = tmp_path/'model.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    assert verify_base_model(path)[0] == snapshot
    weight.write_bytes(b'corrupted fixture')
    with pytest.raises(ValueError, match='SFT_BASE_MODEL_HASH_MISMATCH'):
        verify_base_model(path)


def test_generated_training_data_is_disjoint_from_validation_and_reserved_evaluation():
    root = Path(__file__).resolve().parents[1]
    path = root/'data/training/taskspec_lora'
    records, manifest = load_training_data(path)
    assert len(records['train']) >= 100
    assert len(records['validation']) >= 20
    assert manifest['formal_evaluation_request_overlap'] == 0
    assert all(row['label_source'] == 'synthetic_registry_contract_not_flight_data'
               for rows in records.values() for row in rows)


@pytest.mark.parametrize('corruption',['none','wrong_magnitude','missing_declaration'])
def test_event_contract_is_checked_even_after_dataset_hashes_are_recomputed(tmp_path,corruption):
    from sat_sim.scenario_templates import instantiate_scenario_template
    from sat_sim.task_models import to_runtime_task_spec
    dataset(tmp_path)
    manifest_path = tmp_path/'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['request_event_policy']='explicit_runtime_normalized_events_v1'
    canonical = instantiate_scenario_template('whole_battery_loss')
    canonical['events']['faults'][0]['magnitude']=.3
    for split in ('train','validation'):
        path = tmp_path/f'{split}.jsonl'
        row = json.loads(path.read_text(encoding='utf-8'))
        row['request']='Events: '+json.dumps(canonical['events'])
        target = to_runtime_task_spec(canonical)
        if split=='train' and corruption=='wrong_magnitude':
            target['modifiers']['faults'][0]['severity']=1.
        if split=='train' and corruption=='missing_declaration':
            row['request']='Create an unspecified simulation.'
        row['messages'][1]['content']=row['request']
        row['messages'][-1]['content']=json.dumps(target)
        path.write_text(json.dumps(row)+'\n',encoding='utf-8')
        manifest['splits'][split]['sha256']=sha256(path)
    manifest_path.write_text(json.dumps(manifest),encoding='utf-8')
    if corruption=='none':
        assert load_training_data(tmp_path)[0]['train']
    else:
        with pytest.raises(ValueError,match='SFT_INPUT_LABEL_EVENT_MISMATCH'):
            load_training_data(tmp_path)
