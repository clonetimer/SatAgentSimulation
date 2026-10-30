"""A file manifest alone must not qualify an unsealed or rewritten bundle."""
import hashlib
import json

import pytest

from sat_sim.run_bundle import verify_run_bundle


def _sealed_bundle(root):
    artifact = root / 'result.txt'
    artifact.write_text('original evidence', encoding='utf-8')
    manifest = {'run_id':'test-run', 'files':{'result.txt':{
        'sha256':hashlib.sha256(artifact.read_bytes()).hexdigest(),
        'size_bytes':artifact.stat().st_size,
    }}}
    path = root / 'bundle_manifest.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    seal = {'run_id':'test-run', 'bundle_manifest_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    (root/'SEALED.json').write_text(json.dumps(seal), encoding='utf-8')
    return manifest, seal


def _codes(root):
    result = verify_run_bundle(root)
    assert result['ok'] is False
    return {item['code'] for item in result['issues']}


def _rewrite_files(root, files):
    manifest = {'run_id':'test-run', 'files':files}
    path = root/'bundle_manifest.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    seal = {'run_id':'test-run', 'bundle_manifest_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    (root/'SEALED.json').write_text(json.dumps(seal), encoding='utf-8')


def test_sealed_bundle_and_rewritten_artifact_manifest(tmp_path):
    manifest, _ = _sealed_bundle(tmp_path)
    assert verify_run_bundle(tmp_path)['ok'] is True
    artifact = tmp_path/'result.txt'
    artifact.write_text('rewritten evidence', encoding='utf-8')
    assert 'RUN_BUNDLE_ARTIFACT_HASH_MISMATCH' in _codes(tmp_path)
    manifest['files']['result.txt']['sha256'] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    (tmp_path/'bundle_manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    # Rehashing changed artifacts in the manifest cannot bypass the seal.
    assert 'RUN_BUNDLE_SEAL_HASH_MISMATCH' in _codes(tmp_path)


def test_missing_or_malformed_seal_cannot_qualify(tmp_path):
    _sealed_bundle(tmp_path)
    path = tmp_path/'SEALED.json'
    path.unlink()
    assert 'RUN_BUNDLE_SEAL_MISSING' in _codes(tmp_path)
    path.write_text('{', encoding='utf-8')
    assert 'RUN_BUNDLE_SEAL_INVALID' in _codes(tmp_path)


def test_wrong_seal_identity_is_rejected(tmp_path):
    _, seal = _sealed_bundle(tmp_path)
    seal['run_id'] = 'another-run'
    (tmp_path/'SEALED.json').write_text(json.dumps(seal), encoding='utf-8')
    assert 'RUN_BUNDLE_SEAL_RUN_ID_MISMATCH' in _codes(tmp_path)


def test_invalid_manifest_is_reported_as_integrity_failure(tmp_path):
    _sealed_bundle(tmp_path)
    (tmp_path/'bundle_manifest.json').write_text('[]', encoding='utf-8')
    assert 'RUN_BUNDLE_MANIFEST_INVALID' in _codes(tmp_path)


def test_empty_file_manifest_does_not_qualify(tmp_path):
    _rewrite_files(tmp_path, {})
    assert 'RUN_BUNDLE_MANIFEST_INVALID' in _codes(tmp_path)


@pytest.mark.parametrize('entry', [
    None, [], {}, {'sha256':None}, {'sha256':'g'*64}, {'sha256':'a'*63},
    {'sha256':'a'*64, 'size_bytes':True},
    {'sha256':'a'*64, 'size_bytes':-1},
    {'sha256':'a'*64, 'size_bytes':'1'},
])
def test_invalid_file_entry_is_structured_failure(tmp_path, entry):
    _sealed_bundle(tmp_path)
    _rewrite_files(tmp_path, {'result.txt':entry})
    assert 'RUN_BUNDLE_ARTIFACT_ENTRY_INVALID' in _codes(tmp_path)


@pytest.mark.parametrize('relative', [
    '../outside.txt', '/absolute.txt', 'C:/outside.txt', 'C:outside.txt',
    '..\\outside.txt', 'result.txt:stream', './result.txt', 'results//a.txt',
    'a/../result.txt', 'result.txt/', 'result.txt.',
])
def test_noncanonical_paths_are_rejected_before_reading(tmp_path, monkeypatch, relative):
    from sat_sim import run_bundle

    _sealed_bundle(tmp_path)
    _rewrite_files(tmp_path, {relative:{'sha256':'a'*64}})
    monkeypatch.setattr(run_bundle, '_sha256_file', lambda _:pytest.fail('invalid artifact path was read'))
    assert 'RUN_BUNDLE_ARTIFACT_PATH_INVALID' in _codes(tmp_path)


def test_directory_and_missing_artifact_are_structured_failures(tmp_path):
    _sealed_bundle(tmp_path)
    (tmp_path/'results').mkdir()
    _rewrite_files(tmp_path, {'results':{'sha256':'a'*64}, 'missing':{'sha256':'b'*64}})
    assert _codes(tmp_path) == {'RUN_BUNDLE_ARTIFACT_NOT_FILE', 'RUN_BUNDLE_ARTIFACT_MISSING'}


def test_unreadable_artifact_is_structured_failure(tmp_path, monkeypatch):
    from sat_sim import run_bundle

    _sealed_bundle(tmp_path)
    def denied(_):
        raise PermissionError('unreadable artifact')
    monkeypatch.setattr(run_bundle, '_sha256_file', denied)
    assert 'RUN_BUNDLE_ARTIFACT_UNREADABLE' in _codes(tmp_path)


def test_declared_size_must_match_file(tmp_path):
    manifest, _ = _sealed_bundle(tmp_path)
    manifest['files']['result.txt']['size_bytes'] += 1
    _rewrite_files(tmp_path, manifest['files'])
    assert 'RUN_BUNDLE_ARTIFACT_SIZE_MISMATCH' in _codes(tmp_path)


def test_legacy_hash_only_entry_remains_compatible(tmp_path):
    manifest, _ = _sealed_bundle(tmp_path)
    del manifest['files']['result.txt']['size_bytes']
    _rewrite_files(tmp_path, manifest['files'])
    assert verify_run_bundle(tmp_path)['ok'] is True
