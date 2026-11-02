"""Distribution contract over real archives/files/HTTP; no model or CUDA mocks."""
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import zipfile

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/model_release.py'


def module():
    assert SCRIPT.is_file(), 'Model distribution installer is not implemented'
    spec = importlib.util.spec_from_file_location('sat_model_release', SCRIPT)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def digest(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def release(tmp_path):
    base = tmp_path / 'original-base'
    base.mkdir()
    (base / 'model.safetensors').write_bytes(b'base-weight-fixture')
    (base / 'config.json').write_bytes(b'{}')
    files = [{'path': p.name, 'bytes': p.stat().st_size, 'sha256': digest(p.read_bytes())}
             for p in sorted(base.iterdir())]
    contents = {'model/adapter/adapter_model.safetensors': b'lora-weight-fixture',
                'model/training_provenance.json': b'{"state":"TRAINED_NOT_TASKSPEC_ACCEPTED"}',
                'LICENSE': b'license-fixture'}
    archive = tmp_path / 'adapter.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        for name, data in contents.items():
            z.writestr(name, data)
    lock = {'schema_version': 'sat-model-release.v1', 'release_id': 'test-v5', 'model_name': 'test-v5',
            'archive': {'url': 'https://invalid.example/adapter.zip', 'bytes': archive.stat().st_size,
                        'sha256': digest(archive.read_bytes())},
            'payload': [{'path': k, 'bytes': len(v), 'sha256': digest(v)} for k, v in contents.items()],
            'base': {'model_id': 'Qwen/test', 'revision': 'master', 'files': files,
                     'snapshot_content_sha256': digest(json.dumps(files, sort_keys=True).encode())}}
    path = tmp_path / 'release.json'
    path.write_text(json.dumps(lock), encoding='utf-8')
    return {'path': path, 'lock': lock, 'archive': archive, 'base': base, 'cache': tmp_path / 'fresh-cache'}


def install(m, r, **kwargs):
    return m.install(r['path'], r['cache'], archive_path=r['archive'], base_directory=r['base'], **kwargs)


def rewrite_archive(r, name, content=b'bad', symlink=False, duplicate=False):
    if symlink:
        with zipfile.ZipFile(r['archive']) as z:
            original = {i.filename: z.read(i) for i in z.infolist()}
        with zipfile.ZipFile(r['archive'], 'w') as z:
            for path, data in original.items():
                info = zipfile.ZipInfo(path)
                if path == 'model/adapter/adapter_model.safetensors':
                    info.create_system = 3
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                z.writestr(info, data)
        r['lock']['archive'].update(bytes=r['archive'].stat().st_size, sha256=digest(r['archive'].read_bytes()))
        r['path'].write_text(json.dumps(r['lock']), encoding='utf-8')
        return
    with zipfile.ZipFile(r['archive'], 'a') as z:
        info = zipfile.ZipInfo(name)
        if symlink:
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(info, content)
        if duplicate:
            with pytest.warns(UserWarning, match='Duplicate name'):
                z.writestr(info, content)
    r['lock']['archive'].update(bytes=r['archive'].stat().st_size, sha256=digest(r['archive'].read_bytes()))
    r['path'].write_text(json.dumps(r['lock']), encoding='utf-8')


def test_offline_install_relocates_paths_and_existing_base_verifier_accepts(release):
    m = module()
    result = install(m, release, offline=True)
    from sat_sim_ml.data import verify_base_model
    snapshot, provenance = verify_base_model(Path(result['base_manifest']))
    assert snapshot == (release['cache'] / 'test-v5/base').resolve()
    assert provenance['snapshot_content_sha256'] == release['lock']['base']['snapshot_content_sha256']
    assert Path(result['adapter']).is_dir()
    assert (Path(result['adapter']) / 'adapter_model.safetensors').read_bytes() == b'lora-weight-fixture'
    assert m.check(release['path'], release['cache'])['status'] == 'MODEL_FILES_VERIFIED_NOT_GPU_QUALIFIED'
    assert install(m, release, offline=True) == result


@pytest.mark.parametrize('part', ['archive', 'base'])
def test_corrupt_source_does_not_publish_partial_installation(release, part):
    m = module()
    target = release['archive'] if part == 'archive' else release['base'] / 'model.safetensors'
    target.write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='MISMATCH'):
        install(m, release, offline=True)
    assert not (release['cache'] / 'test-v5').exists()


@pytest.mark.parametrize('attack', ['traversal', 'absolute', 'extra', 'symlink', 'duplicate'])
def test_untrusted_zip_entries_rejected_without_escape(release, attack):
    m = module()
    names = {'traversal': '../escaped', 'absolute': '/escaped', 'extra': 'unexpected.py',
             'symlink': 'model/adapter/link', 'duplicate': 'model/adapter/adapter_model.safetensors'}
    rewrite_archive(release, names[attack], symlink=attack == 'symlink', duplicate=attack == 'duplicate')
    with pytest.raises(ValueError, match='ARCHIVE'):
        install(m, release, offline=True)
    assert not (release['cache'] / 'test-v5').exists()
    assert not (release['cache'] / 'escaped').exists()


def test_altered_installation_is_rejected_and_not_overwritten(release):
    m = module()
    result = install(m, release, offline=True)
    path = Path(result['adapter']) / 'adapter_model.safetensors'
    path.write_bytes(b'altered')
    with pytest.raises(ValueError, match='MISMATCH'):
        install(m, release, offline=True)
    assert path.read_bytes() == b'altered'


def test_launch_uses_relocated_paths_and_existing_server(release):
    m = module()
    result = install(m, release, offline=True)
    command = m.serve_command(release['path'], release['cache'], python='gpu-python', port=8040)
    assert command[0] == 'gpu-python'
    assert Path(command[1]).name == 'serve_modelscope_model.py'
    assert command[command.index('--base-manifest') + 1] == result['base_manifest']
    assert command[command.index('--adapter') + 1] == result['adapter']
    assert command[command.index('--model-name') + 1] == 'test-v5'
    assert command[command.index('--port') + 1] == '8040'


def test_offline_missing_sources_never_tries_network(release):
    m = module()
    with pytest.raises(ValueError, match='OFFLINE'):
        m.install(release['path'], release['cache'], offline=True)


def test_real_http_download_installs_exact_archive_and_base(release):
    m = module()
    requests = []
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(release['path'].parent), **kw)
        def log_message(self, *_):
            pass
        def do_GET(self):
            requests.append(self.path)
            super().do_GET()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        origin = 'http://127.0.0.1:' + str(server.server_port)
        release['lock']['archive']['url'] = origin + '/adapter.zip'
        for row in release['lock']['base']['files']:
            row['url'] = origin + '/original-base/' + row['path']
        release['path'].write_text(json.dumps(release['lock']), encoding='utf-8')
        m.install(release['path'], release['cache'])
        assert set(requests) == {'/adapter.zip', '/original-base/config.json', '/original-base/model.safetensors'}
        assert m.check(release['path'], release['cache'])['status'] == 'MODEL_FILES_VERIFIED_NOT_GPU_QUALIFIED'
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
