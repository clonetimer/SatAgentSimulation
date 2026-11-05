"""Successful persisted parse receipts bind a run before any task/run writes."""
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.canonical_transport import MAX_JSON_BYTES, task_spec_json
from sat_sim.task_spec import spec_sha256


@pytest.fixture
def context(tmp_path):
    root = tmp_path / 'artifacts'
    app = create_app(runs_root=tmp_path/'runs', artifacts_root=root,
                     embedded_worker=False, auth_mode='disabled')
    with TestClient(app) as client:
        yield client, root, tmp_path/'runs'


def original():
    return json.loads((Path(__file__).parent/'fixtures/form_projection/recorded_create17.json').read_text(encoding='utf-8'))


def parse(client, spec):
    response = client.post('/tasks/parse', json={'input_kind':'task_spec', 'task_spec_json':json.dumps(spec)})
    assert response.status_code == 200 and response.json()['ok'], response.text
    return response.json()


def test_successful_parse_binds_exact_numeric_task_and_provenance(context):
    client, root, runs = context
    spec = original()
    receipt = parse(client, spec)
    response = client.post('/runs', json={'task_spec_json':json.dumps(spec), 'source_request_id':receipt['request_id'],
                                         'run_id':'receipt-prepare-only', 'execute':False})
    assert response.status_code == 200, response.text
    provenance = json.loads((runs/'receipt-prepare-only/input/provenance.json').read_text(encoding='utf-8'))
    assert provenance['source_request_id'] == receipt['request_id']
    assert provenance['task_spec_sha256'] == receipt['result']['task_spec_sha256'] == spec_sha256(spec)
    persisted = json.loads((runs/'receipt-prepare-only/input/task_spec.json').read_text(encoding='utf-8'))
    assert spec_sha256(persisted) == spec_sha256(spec)


@pytest.mark.parametrize('case,code', [
    ('changed', 'TASK_SOURCE_TASK_MISMATCH'), ('numeric_type', 'TASK_SOURCE_TASK_MISMATCH'),
    ('missing', 'TASK_SOURCE_MISSING'), ('failed', 'TASK_SOURCE_NOT_SUCCESSFUL'),
    ('genuine_failed', 'TASK_SOURCE_NOT_SUCCESSFUL'),
    ('truthy_ok', 'TASK_SOURCE_NOT_SUCCESSFUL'), ('corrupt', 'TASK_SOURCE_INVALID_REPORT'),
    ('list', 'TASK_SOURCE_INVALID_REPORT'), ('empty_task', 'TASK_SOURCE_INVALID_REPORT'),
    ('invalid_task', 'TASK_SOURCE_INVALID_REPORT'), ('hash_missing', 'TASK_SOURCE_INVALID_REPORT'),
    ('hash_bad', 'TASK_SOURCE_INVALID_REPORT'), ('hash_inconsistent', 'TASK_SOURCE_HASH_MISMATCH'),
    ('empty_id', 'TASK_SOURCE_INVALID_ID'), ('traversal', 'TASK_SOURCE_INVALID_ID'),
    ('windows_path', 'TASK_SOURCE_INVALID_ID'), ('uppercase', 'TASK_SOURCE_INVALID_ID'),
])
def test_invalid_receipt_is_409_before_all_persistence(context, monkeypatch, case, code):
    import sat_sim.api as module
    client, root, runs = context
    spec = original()
    if case == 'genuine_failed':
        invalid = original()
        invalid['model']['capability_id'] = 'component.nonexistent.v1'
        parsed = client.post('/tasks/parse', json={'input_kind':'task_spec', 'task_spec_json':json.dumps(invalid)})
        assert parsed.status_code == 200 and parsed.json()['ok'] is False, parsed.text
        receipt = parsed.json()
    else:
        receipt = parse(client, spec)
    request_id = receipt['request_id']
    report_path = root/'tasks'/request_id/'unified_agent_report.json'
    report = json.loads(report_path.read_text(encoding='utf-8'))
    if case == 'changed': spec['simulation']['duration_s'] += 1
    elif case == 'numeric_type': spec['parameters']['values']['orbit_radius_m'] = int(spec['parameters']['values']['orbit_radius_m'])
    elif case == 'missing': report_path.unlink()
    elif case == 'failed': report['ok'] = False
    elif case == 'truthy_ok': report['ok'] = 1
    elif case == 'corrupt': report_path.write_text('{', encoding='utf-8')
    elif case == 'list': report = []
    elif case == 'empty_task': report['task_spec'] = {}
    elif case == 'invalid_task': report['task_spec'] = {'schema_version':'1.0.0'}
    elif case == 'hash_missing': report.pop('task_spec_sha256')
    elif case == 'hash_bad': report['task_spec_sha256'] = 'z'*64
    elif case == 'hash_inconsistent': report['task_spec_sha256'] = '0'*64
    elif case == 'empty_id': request_id = ''
    elif case == 'traversal': request_id = '../' + request_id
    elif case == 'windows_path': request_id = 'C:\\' + request_id
    elif case == 'uppercase': request_id = request_id.upper()
    if case not in ('missing', 'corrupt'):
        report_path.write_text(json.dumps(report), encoding='utf-8')
    writes = []
    def forbidden(*args, **kwargs):
        writes.append(True)
        raise AssertionError('receipt rejection must precede persistence')
    monkeypatch.setattr(client.app.state.task_center, 'save', forbidden)
    monkeypatch.setattr(module, 'prepare_run', forbidden)
    response = client.post('/runs', json={'task_spec_json':json.dumps(spec), 'source_request_id':request_id,
                                         'run_id':'must-not-exist', 'execute':False})
    assert response.status_code == 409, response.text
    assert response.json()['detail']['reason_code'] == code
    assert writes == []
    assert not (runs/'must-not-exist').exists()
    assert client.get('/task-center/tasks').json()['tasks'] == []


def test_legacy_submission_without_receipt_still_prepares(context):
    client, _, runs = context
    response = client.post('/runs', json={'task_spec_json':json.dumps(original()), 'run_id':'legacy', 'execute':False})
    assert response.status_code == 200, response.text
    provenance = json.loads((runs/'legacy/input/provenance.json').read_text(encoding='utf-8'))
    assert 'source_request_id' not in provenance


def test_helper_returns_only_validated_identity_and_hash(context):
    from sat_sim.task_source import validate_task_source, TaskSourceValidationError
    client, root, _ = context
    spec = original()
    receipt = parse(client, spec)
    assert validate_task_source(root, receipt['request_id'], spec) == {
        'request_id':receipt['request_id'], 'task_spec_sha256':spec_sha256(spec)}
    with pytest.raises(TaskSourceValidationError):
        validate_task_source(root, '', spec)


def test_receipt_provenance_is_covered_by_existing_manifest_and_seal(context):
    # Integrity plumbing only: prepare and synthetic seal, zero execution.
    from sat_sim.run_bundle import _bundle_manifest, RunRecord, verify_run_bundle
    client, _, runs = context
    spec = original()
    receipt = parse(client, spec)
    response = client.post('/runs', json={'task_spec_json':json.dumps(spec), 'source_request_id':receipt['request_id'],
                                         'run_id':'receipt-seal-contract', 'execute':False})
    assert response.status_code == 200, response.text
    root = runs/'receipt-seal-contract'
    record = RunRecord.model_validate_json((root/'run_record.json').read_text(encoding='utf-8'))
    manifest = _bundle_manifest(root, record)
    assert 'input/provenance.json' in manifest['files']
    path = root/'bundle_manifest.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    (root/'SEALED.json').write_text(json.dumps({'run_id':record.run_id,
        'bundle_manifest_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}), encoding='utf-8')
    assert verify_run_bundle(root)['ok'] is True
    provenance_path = root/'input/provenance.json'
    provenance = json.loads(provenance_path.read_text(encoding='utf-8'))
    provenance['source_request_id'] = '0'*32
    provenance_path.write_text(json.dumps(provenance), encoding='utf-8')
    result = verify_run_bundle(root)
    assert result['ok'] is False
    assert any(issue['code'] == 'RUN_BUNDLE_ARTIFACT_HASH_MISMATCH' for issue in result['issues'])


def test_unicode_receipt_binds_valid_utf8_text_below_transport_limit(context):
    client, root, runs = context
    spec = original()
    spec['metadata']['unicode_note'] = '测' * 180_000
    text = task_spec_json(spec)
    assert len(text.encode('utf-8')) < MAX_JSON_BYTES
    assert len(json.dumps(spec).encode('utf-8')) > MAX_JSON_BYTES
    parsed = client.post('/tasks/parse', json={'input_kind':'task_spec', 'task_spec_json':text})
    assert parsed.status_code == 200 and parsed.json()['ok'], parsed.text
    receipt = parsed.json()
    assert receipt['result']['task_spec_sha256'] == spec_sha256(spec)
    response = client.post('/runs', json={'task_spec_json':text, 'source_request_id':receipt['request_id'],
                                         'run_id':'unicode-prepare-only', 'execute':False})
    assert response.status_code == 200, response.text
    provenance = json.loads((runs/'unicode-prepare-only/input/provenance.json').read_text(encoding='utf-8'))
    persisted = json.loads((runs/'unicode-prepare-only/input/task_spec.json').read_text(encoding='utf-8'))
    assert provenance['source_request_id'] == receipt['request_id']
    assert provenance['task_spec_sha256'] == spec_sha256(spec) == spec_sha256(persisted)
    assert persisted['metadata']['unicode_note'] == spec['metadata']['unicode_note']


def test_unicode_authoritative_text_over_existing_transport_limit_stays_rejected(context):
    client, root, runs = context
    spec = original()
    spec['metadata']['unicode_note'] = '测' * 360_000
    text = json.dumps(spec, ensure_ascii=False, separators=(',', ':'))
    assert len(text.encode('utf-8')) > MAX_JSON_BYTES
    response = client.post('/tasks/parse', json={'input_kind':'task_spec', 'task_spec_json':text})
    assert response.status_code == 422, response.text
    assert 'CANONICAL_TRANSPORT_RAW_TOO_LARGE' in response.text
    assert not (root/'tasks').exists()
    assert client.get('/task-center/tasks').json()['tasks'] == []
    assert not runs.exists() or not any(runs.iterdir())
